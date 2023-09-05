#!/usr/bin/python3
# Copyright (C) 2018 Jelmer Vernooij <jelmer@jelmer.uk>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA

"""Support for uploading packages."""

import datetime
import logging
import os
import subprocess
import sys
import tempfile
import time
from contextlib import suppress
from email.utils import parseaddr
from typing import Callable, List, Optional, Tuple

from breezy import gpg  # type: ignore
from breezy.commit import NullCommitReporter, PointlessCommit
from breezy.config import NoEmailInUsername, extract_email_address
from breezy.errors import NoSuchTag, PermissionDenied
from breezy.plugins.debian.apt_repo import Apt, LocalApt, RemoteApt
from breezy.plugins.debian.builder import BuildFailedError
from breezy.plugins.debian.cmds import _build_helper
from breezy.plugins.debian.import_dsc import DistributionBranch
from breezy.plugins.debian.release import release
from breezy.plugins.debian.upstream import MissingUpstreamTarball
from breezy.plugins.debian.util import (
    MissingChangelogError,
    NoPreviousUpload,
    changelog_find_previous_upload,
    dput_changes,
    find_changelog,
)
from breezy.revision import NULL_REVISION
from breezy.tree import MissingNestedTree
from breezy.workingtree import WorkingTree
from debian.changelog import Version, get_maintainer
from debmutate.changelog import (
    ChangelogEditor,
    ChangelogParseError,
    changeblock_ensure_first_line,
    gbp_dch,
)
from debmutate.control import ControlEditor
from debmutate.reformatting import GeneratedFile

import silver_platter  # noqa: F401

from ..probers import select_probers
from ..utils import (
    BranchMissing,
    BranchRateLimited,
    BranchUnavailable,
    BranchUnsupported,
    open_branch,
)
from . import (
    DEFAULT_BUILDER,
    NoSuchPackage,
    Workspace,
    apt_get_source_package,
    source_package_vcs,
    split_vcs_url,
)


def debsign(path, keyid=None):
    (bd, changes_file) = os.path.split(path)
    args = ["debsign"]
    if keyid:
        args.append("-k%s" % keyid)
    args.append(changes_file)
    subprocess.check_call(args, cwd=bd)


class LastUploadMoreRecent(Exception):
    """Last version in archive is newer than vcs version."""

    def __init__(self, archive_version, vcs_version) -> None:
        self.archive_version = archive_version
        self.vcs_version = vcs_version
        super().__init__(
            f"last upload ({archive_version}) is more recent "
            f"than vcs ({vcs_version})"
        )


class NoUnuploadedChanges(Exception):
    """Indicates there are no unuploaded changes for a package."""

    def __init__(self, archive_version) -> None:
        self.archive_version = archive_version
        super().__init__(
            "nothing to upload, latest version is in archive: %s" %
            archive_version
        )


class NoUnreleasedChanges(Exception):
    """Indicates there are no unreleased changes for a package."""

    def __init__(self, version) -> None:
        self.version = version
        super().__init__(
            "nothing to upload, latest version in vcs is not unreleased: %s" %
            version
        )


class RecentCommits(Exception):
    """Indicates there are too recent commits for a package."""

    def __init__(self, commit_age, min_commit_age) -> None:
        self.commit_age = commit_age
        self.min_commit_age = min_commit_age
        super().__init__(
            "Last commit is only %d days old (< %d)"
            % (self.commit_age, self.min_commit_age)
        )


class CommitterNotAllowed(Exception):
    """Specified committer is not allowed."""

    def __init__(self, committer, allowed_committers) -> None:
        self.committer = committer
        self.allowed_committers = allowed_committers
        super().__init__(
            f"Committer {self.committer} not in allowed committers: "
            f"{self.allowed_committers!r}"
        )


class LastReleaseRevisionNotFound(Exception):
    """The revision for the last uploaded release can't be found."""

    def __init__(self, package, version) -> None:
        self.package = package
        self.version = version
        super().__init__(
            f"Unable to find revision matching version {version!r} "
            f"for {package}"
        )


def check_revision(rev, min_commit_age, allowed_committers):
    """Check whether a revision can be included in an upload.

    Args:
      rev: revision to check
      min_commit_age: Minimum age for revisions
      allowed_committers: List of allowed committers
    Raises:
      RecentCommits: When there are commits younger than min_commit_age
    """
    # TODO(jelmer): deal with timezone
    if min_commit_age is not None:
        commit_time = datetime.datetime.fromtimestamp(rev.timestamp)
        time_delta = datetime.datetime.now() - commit_time
        if time_delta.days < min_commit_age:
            raise RecentCommits(time_delta.days, min_commit_age)
    # TODO(jelmer): Allow tag to prevent automatic uploads
    try:
        committer_email = extract_email_address(rev.committer)
    except NoEmailInUsername as e:
        logging.warning(
            'Unable to extract email from %r', rev.committer)
        raise CommitterNotAllowed(rev.committer, allowed_committers) from e
    if allowed_committers and committer_email not in allowed_committers:
        raise CommitterNotAllowed(committer_email, allowed_committers)


def find_last_release_revid(branch, version):
    db = DistributionBranch(branch, None)
    return db.revid_of_version(version)


def get_maintainer_keys(context):
    for key in context.keylist(
            source="/usr/share/keyrings/debian-keyring.gpg"):
        yield key.fpr
        for subkey in key.subkeys:
            yield subkey.keyid


class GbpDchFailed(Exception):
    """gbp dch failed to run."""


class GeneratedChangelogFile(Exception):
    """unable to update changelog since it is generated."""


def prepare_upload_package(  # noqa: C901
    local_tree: WorkingTree,
    subpath: str,
    pkg: str,
    last_uploaded_version: Optional[Version],
    builder: str,
    *,
    gpg_strategy: Optional[gpg.GPGStrategy] = None,
    min_commit_age: Optional[int] = None,
    allowed_committers: Optional[List[str]] = None,
    apt: Optional[Apt] = None,
) -> Tuple[str, str]:
    debian_path = os.path.join(subpath, "debian")
    try:
        from lintian_brush.detect_gbp_dch import guess_update_changelog
    except ImportError:
        run_gbp_dch = True   # Let's just try
    else:
        cl_behaviour = guess_update_changelog(local_tree, debian_path)
        run_gbp_dch = (
            cl_behaviour is None or not cl_behaviour.update_changelog)
    if run_gbp_dch:
        try:
            gbp_dch(local_tree.abspath(subpath))
        except subprocess.CalledProcessError:
            # TODO(jelmer): gbp dch sometimes fails when there is no existing
            # open changelog entry; it fails invoking
            # "dpkg --lt None <old-version>"
            raise GbpDchFailed()
        local_tree.commit(
            specific_files=[os.path.join(debian_path, 'changelog')],
            message='update changelog\n\nGbp-Dch: Ignore')
    cl, top_level = find_changelog(local_tree, merge=False, max_blocks=None)
    if (last_uploaded_version is not None
            and cl.version == last_uploaded_version):
        raise NoUnuploadedChanges(cl.version)
    try:
        previous_version_in_branch = changelog_find_previous_upload(cl)
    except NoPreviousUpload:
        pass
    else:
        if (last_uploaded_version is not None and
                last_uploaded_version > previous_version_in_branch):
            raise LastUploadMoreRecent(
                last_uploaded_version, previous_version_in_branch)

    if last_uploaded_version is not None:
        logging.info("Checking revisions since %s", last_uploaded_version)
    with local_tree.lock_read():
        if last_uploaded_version is not None:
            try:
                last_release_revid = find_last_release_revid(
                    local_tree.branch, last_uploaded_version
                )
            except NoSuchTag:
                raise LastReleaseRevisionNotFound(pkg, last_uploaded_version)
        else:
            last_release_revid = NULL_REVISION
        graph = local_tree.branch.repository.get_graph()
        revids = list(
            graph.iter_lefthand_ancestry(
                local_tree.branch.last_revision(), [last_release_revid]
            )
        )
        if not revids:
            logging.info("No pending changes")
            raise NoUnuploadedChanges(cl.version)
        if gpg_strategy:
            logging.info("Verifying GPG signatures...")
            count, result, all_verifiables = gpg.bulk_verify_signatures(
                local_tree.branch.repository, revids, gpg_strategy
            )
            for revid, code, _key in result:
                if code != gpg.SIGNATURE_VALID:
                    raise Exception(
                        "No valid GPG signature on %r: %d" % (revid, code))
        for _revid, rev in local_tree.branch.repository.iter_revisions(revids):
            if rev is not None:
                check_revision(rev, min_commit_age, allowed_committers)

        if cl.distributions != "UNRELEASED":
            raise NoUnreleasedChanges(cl.version)
    qa_upload = False
    team_upload = False
    control_path = local_tree.abspath(os.path.join(debian_path, "control"))
    with ControlEditor(control_path) as e:
        maintainer = parseaddr(e.source["Maintainer"])
        if maintainer[1] == "packages@qa.debian.org":
            qa_upload = True
        # TODO(jelmer): Check whether this is a team upload
        # TODO(jelmer): determine whether this is a NMU upload
    if qa_upload or team_upload:
        changelog_path = local_tree.abspath(
            os.path.join(debian_path, "changelog"))
        with ChangelogEditor(changelog_path) as e:
            if qa_upload:
                changeblock_ensure_first_line(e[0], "QA upload.")
                message = "Mention QA Upload."
            elif team_upload:
                changeblock_ensure_first_line(e[0], "Team upload.")
                message = "Mention Team Upload."
            else:
                message = None
        if message is not None:
            with suppress(PointlessCommit):
                local_tree.commit(
                    specific_files=[os.path.join(debian_path, "changelog")],
                    message=message,
                    allow_pointless=False,
                    reporter=NullCommitReporter(),
                )
    try:
        tag_name = release(local_tree, subpath)
    except GeneratedFile:
        raise GeneratedChangelogFile()
    target_dir = tempfile.mkdtemp()
    if last_uploaded_version is not None:
        builder = builder.replace(
            "${LAST_VERSION}", str(last_uploaded_version))
    target_changes = _build_helper(
        local_tree, subpath, local_tree.branch, target_dir, builder=builder,
        apt=apt
    )
    debsign(target_changes['source'])
    return target_changes['source'], tag_name


def select_apt_packages(apt_repo, package_names, maintainer):
    packages = []

    with apt_repo:
        for source in apt_repo.iter_sources():
            if maintainer:
                fullname, email = parseaddr(source['Maintainer'])
                if email not in maintainer:
                    continue

            if package_names and source['Package'] not in package_names:
                continue

            packages.append(source['Package'])

        return packages


class PackageProcessingFailure(Exception):

    def __init__(self, reason, description=None) -> None:
        self.reason = reason
        self.description = description


class PackageIgnored(Exception):

    def __init__(self, reason, description=None) -> None:
        self.reason = reason
        self.description = description


def check_git_commits(vcslog, min_commit_age, allowed_committers):
    class GitRevision:

        @property
        def committer(self):
            return self.headers.get('Committer') or self.headers.get('Author')

        @property
        def timestamp(self):
            datestr = self.headers.get('Date')
            dt = datetime.datetime.strptime(
                datestr.strip(), "%a %b %d %H:%M:%S %Y %z")
            return dt.timestamp()

        def __init__(self, commit_id, headers, message) -> None:
            self.commit_id = commit_id
            self.headers = headers
            self.message = message

        @classmethod
        def from_lines(cls, lines):
            commit_id = None
            message = []
            headers = {}
            for i, line in enumerate(lines):
                if line.startswith('commit '):
                    commit_id = line[len('commit '):]
                elif line == '':
                    message = lines[i+1:]
                    break
                else:
                    name, value = line.split(': ')
                    headers[name] = value
            return cls(commit_id, headers, message)

    last_commit_ts = None
    lines = []
    for line in vcslog.splitlines():
        if line == '' and lines[-1][0].isspace():
            gitrev = GitRevision.from_lines(lines)
            if last_commit_ts is None:
                last_commit_ts = gitrev.timestamp
            check_revision(
                gitrev, min_commit_age,
                allowed_committers)
            lines = []
        else:
            lines.append(line)
    if lines:
        gitrev = GitRevision.from_lines(lines)
        if last_commit_ts is None:
            last_commit_ts = gitrev.timestamp
        check_revision(gitrev, min_commit_age, allowed_committers)
    return last_commit_ts


def process_package(
        apt_repo, package,
        builder: str, *, exclude=None, autopkgtest_only: bool = False,
        gpg_verification: bool = False,
        acceptable_keys=None, debug: bool = False,
        diff: bool = False, min_commit_age=None, allowed_committers=None,
        vcs_type=None, vcs_url=None, source_name=None,
        archive_version=None, verify_command: Optional[str] = None):
    if exclude is None:
        exclude = set()
    logging.info("Processing %s", package)
    # Can't use open_packaging_branch here, since we want to use pkg_source
    # later on.
    if "/" not in package:
        try:
            with apt_repo:
                pkg_source = apt_get_source_package(apt_repo, package)
        except NoSuchPackage:
            logging.info("%s: package not found in apt", package)
            raise PackageProcessingFailure('not-in-apt')
        if vcs_type is None or vcs_url is None:
            try:
                vcs_type, vcs_url = source_package_vcs(pkg_source)
            except KeyError:
                logging.info(
                    "%s: no declared vcs location, skipping",
                    pkg_source["Package"]
                )
                raise PackageProcessingFailure('not-in-vcs')
        if source_name is None:
            source_name = pkg_source["Package"]
        if source_name in exclude:
            raise PackageIgnored('excluded')
        if archive_version is None:
            archive_version = pkg_source["Version"]
        has_testsuite = "Testsuite" in pkg_source
    else:
        if vcs_url is None:
            vcs_url = package
        has_testsuite = None
    (location, branch_name, subpath) = split_vcs_url(vcs_url)
    if subpath is None:
        subpath = ""
    probers = select_probers(vcs_type)
    try:
        main_branch = open_branch(
            location, probers=probers, name=branch_name)
    except (BranchUnavailable, BranchMissing, BranchUnsupported) as e:
        if debug:
            logging.exception("%s: %s", vcs_url, e)
        else:
            logging.info(
                "%s: branch unavailable: %s", vcs_url, e)
        raise PackageProcessingFailure('vcs-inaccessible')
    with Workspace(main_branch) as ws:
        if source_name is None:
            with ControlEditor(
                ws.local_tree.abspath(
                    os.path.join(subpath, "debian/control"))
            ) as ce:
                source_name = ce.source["Source"]
                try:
                    with apt_repo:
                        pkg_source = apt_get_source_package(
                            apt_repo, source_name)
                except NoSuchPackage:
                    logging.info("%s: package not found in apt", package)
                    raise PackageProcessingFailure('not-in-apt')
                archive_version = pkg_source['Version']
            has_testsuite = "Testsuite" in ce.source
        if source_name in exclude:
            raise PackageIgnored('excluded')
        if (
            autopkgtest_only
            and not has_testsuite
            and not ws.local_tree.has_filename(
                os.path.join(subpath, "debian/tests/control")
            )
        ):
            logging.info(
                "%s: Skipping, package has no autopkgtest.", source_name)
            raise PackageIgnored('no-autopkgtest')
        branch_config = ws.local_tree.branch.get_config_stack()
        if gpg_verification:
            gpg_strategy = gpg.GPGStrategy(branch_config)
            if not acceptable_keys:
                acceptable_keys = list(
                    get_maintainer_keys(gpg_strategy.context))
            gpg_strategy.set_acceptable_keys(",".join(acceptable_keys))
        else:
            gpg_strategy = None

        try:
            target_changes, tag_name = prepare_upload_package(
                ws.local_tree,
                subpath,
                source_name,
                archive_version,
                builder=builder,
                gpg_strategy=gpg_strategy,
                min_commit_age=min_commit_age,
                allowed_committers=allowed_committers,
                apt=apt_repo,
            )
        except GbpDchFailed as e:
            logging.warn("%s: 'gbp dch' failed to run: %s", source_name, e)
            raise PackageProcessingFailure('gbp-dch-failed')
        except MissingUpstreamTarball as e:
            logging.warning(
                "%s: missing upstream tarball: %s", source_name, e)
            raise PackageProcessingFailure('missing-upstream-tarball')
        except BranchRateLimited as e:
            logging.warning(
                '%s: rate limited by server (retrying after %s)',
                source_name, e.retry_after)
            raise PackageProcessingFailure('rate-limited')
        except CommitterNotAllowed as e:
            logging.warn(
                "%s: committer %s not in allowed list: %r",
                source_name,
                e.committer,
                e.allowed_committers,
            )
            raise PackageIgnored('committer-not-allowed')
        except BuildFailedError as e:
            logging.warn("%s: package failed to build: %s", source_name, e)
            raise PackageProcessingFailure('build-failed')
        except LastReleaseRevisionNotFound as e:
            logging.warn(
                "%s: Unable to find revision matching last release "
                "%s, skipping.",
                source_name,
                e.version,
            )
            raise PackageProcessingFailure('last-release-missing')
        except LastUploadMoreRecent as e:
            logging.warn(
                "%s: Last upload (%s) was more recent than VCS (%s)",
                source_name,
                e.archive_version,
                e.vcs_version,
            )
            raise PackageProcessingFailure('last-upload-not-in-vcs')
        except ChangelogParseError as e:
            logging.info("%s: Error parsing changelog: %s", source_name, e)
            raise PackageProcessingFailure('changelog-parse-error')
        except MissingChangelogError:
            logging.info("%s: No changelog found, skipping.", source_name)
            raise PackageProcessingFailure('missing-changelog')
        except GeneratedChangelogFile:
            logging.info(
                "%s: Changelog is generated and unable to update, skipping.",
                source_name)
            raise PackageProcessingFailure('generated-changelog')
        except RecentCommits as e:
            logging.info(
                "%s: Recent commits (%d days), skipping.",
                source_name, e.commit_age
            )
            raise PackageIgnored('recent-commits')
        except NoUnuploadedChanges as e:
            logging.info("%s: No unuploaded changes (%s), skipping.",
                         source_name, e.archive_version)
            raise PackageIgnored('no-unuploaded-changes')
        except NoUnreleasedChanges:
            logging.info(
                "%s: No unreleased changes, skipping.", source_name)
            raise PackageIgnored('no-unreleased-changes')
        except MissingNestedTree:
            logging.exception('missing nested tree')
            raise PackageIgnored('unsuported-nested-tree')

        if verify_command:
            try:
                subprocess.check_call([verify_command, target_changes])
            except subprocess.CalledProcessError as e:
                if e.returncode == 1:
                    raise PackageIgnored(
                        'verify-command-declined',
                        "{}: Verify command {!r} declined upload".format(
                            source_name, verify_command))
                else:
                    raise PackageProcessingFailure(
                        'verify-command-error',
                        f"{source_name}: "
                        f"Error running verify command {verify_command}: "
                        f"returncode {e.returncode}")

        tags = []
        if tag_name is not None:
            logging.info("Pushing tag %s", tag_name)
            tags.append(tag_name)
        try:
            ws.push(tags=tags)
        except PermissionDenied:
            logging.info(
                "%s: Permission denied pushing to branch, skipping.",
                source_name
            )
            raise PackageProcessingFailure('vcs-permission-denied')
        dput_changes(target_changes)
        if diff:
            sys.stdout.flush()
            ws.show_diff(sys.stdout.buffer)
            sys.stdout.buffer.flush()


def vcswatch_prescan_package(
        package, vw, exclude=None, min_commit_age=None,
        allowed_committers=None):
    vcs_url = vw["url"]
    vcs_type = vw["vcs"]
    source_name = vw["package"]
    if source_name in exclude:
        raise PackageIgnored('excluded')
    if vcs_url is None or vcs_type is None:
        raise PackageProcessingFailure('not-in-vcs')
    # TODO(jelmer): check autopkgtest_only ?
    # from debian.deb822 import Deb822
    # pkg_source = Deb822(vw["controlfile"])
    # has_testsuite = "Testsuite" in pkg_source
    if vw["commits"] == 0:
        raise PackageIgnored('no-unuploaded-changes')
    if vw['status'] == 'ERROR':
        logging.warning(
            'vcswatch: unable to access %s: %s', vw['package'], vw['error'])
        raise PackageProcessingFailure('vcs-inaccessible')
    logging.debug("vcswatch last scanned at: %s", vw["last_scan"])
    if vcs_type == 'Git' and vw["vcslog"] is not None:
        try:
            return check_git_commits(
                vw["vcslog"], min_commit_age=min_commit_age,
                allowed_committers=allowed_committers)
        except CommitterNotAllowed as e:
            logging.warn(
                "%s: committer %s not in allowed list: %r",
                source_name,
                e.committer,
                e.allowed_committers,
            )
            raise PackageIgnored('committer-not-allowed')
        except RecentCommits as e:
            logging.info(
                "%s: Recent commits (%d days), skipping.",
                source_name, e.commit_age
            )
            raise PackageIgnored('recent-commits')
    return None


def vcswatch_prescan_packages(
        packages, inc_stats: Callable[[str], None],
        exclude=None, min_commit_age=None,
        allowed_committers=None):
    logging.info('Using vcswatch to prescan %d packages', len(packages))
    import gzip
    import json
    from urllib.request import Request, urlopen

    from .. import version_string
    url = "https://qa.debian.org/data/vcswatch/vcswatch.json.gz"
    request = Request(url, headers={
        'User-Agent': "silver-platter/%s" % version_string})
    vcswatch = {
        p['package']: p
        for p in json.load(gzip.GzipFile(fileobj=urlopen(request)))}
    by_ts = {}
    failures = 0
    for package in packages:
        try:
            vw = vcswatch[package]
        except KeyError:
            continue
        try:
            ts = vcswatch_prescan_package(
                    package, vw,
                    exclude=exclude, min_commit_age=min_commit_age,
                    allowed_committers=allowed_committers)
        except PackageProcessingFailure as e:
            inc_stats(e.reason)
            failures += 1
        except PackageIgnored as e:
            inc_stats(e.reason)
        else:
            by_ts[package] = ts
    return ([k for (k, v) in
             sorted(by_ts.items(), key=lambda k: k[1] or 0, reverse=True)],
            failures,
            vcswatch)


def open_last_attempt_db():
    try:
        import tdb
        from xdg.BaseDirectory import xdg_data_home
    except ModuleNotFoundError:
        return None
    else:
        last_attempt_path = os.path.join(
            xdg_data_home, 'silver-platter', 'last-upload-attempt.tdb')
        os.makedirs(os.path.dirname(last_attempt_path), exist_ok=True)
        return tdb.open(
            last_attempt_path, tdb_flags=tdb.DEFAULT,
            flags=os.O_RDWR | os.O_CREAT, mode=0o600)


def main(argv):  # noqa: C901
    import argparse

    parser = argparse.ArgumentParser(prog="upload-pending-commits")
    parser.add_argument("packages", nargs="*")
    parser.add_argument(
        "--acceptable-keys",
        help="List of acceptable GPG keys",
        action="append",
        default=[],
        type=str,
    )
    parser.add_argument(
        "--gpg-verification",
        help="Verify GPG signatures on commits",
        action="store_true",
    )
    parser.add_argument(
        "--min-commit-age",
        help="Minimum age of the last commit, in days",
        type=int,
        default=0,
    )
    parser.add_argument("--diff", action="store_true", help="Show diff.")
    parser.add_argument(
        "--builder",
        type=str,
        help="Build command",
        default=(
            DEFAULT_BUILDER + " --source --source-only-changes "
            "--debbuildopt=-v${LAST_VERSION}"
        ),
    )
    parser.add_argument(
        "--maintainer",
        type=str,
        action="append",
        help="Select all packages maintained by specified maintainer.",
    )
    parser.add_argument(
        "--vcswatch",
        action="store_true",
        default=False,
        help="Use vcswatch to determine what packages need uploading."
    )
    parser.add_argument(
        "--exclude", type=str, action="append", default=[],
        help="Ignore source package"
    )
    parser.add_argument(
        "--autopkgtest-only",
        action="store_true",
        help="Only process packages with autopkgtests.",
    )
    parser.add_argument(
        "--allowed-committer",
        type=str,
        action="append",
        help="Require that all new commits are from specified committers",
    )
    parser.add_argument(
        "--debug",
        action="store_true")
    parser.add_argument(
        "--shuffle",
        action="store_true",
        help="Randomize order packages are processed in.")
    parser.add_argument(
        "--verify-command", type=str, default=None,
        help=("Command to verify whether upload is necessary. "
              "Should return 1 to decline, 0 to upload."))
    parser.add_argument(
        '--apt-repository', type=str,
        help='APT repository to use. Defaults to locally configured.',
        default=(
            os.environ.get('APT_REPOSITORY')
            or os.environ.get('REPOSITORIES')))
    parser.add_argument(
        '--apt-repository-key', type=str,
        help=('APT repository key to use for validation, '
              'if --apt-repository is set.'),
        default=os.environ.get('APT_REPOSITORY_KEY'))

    args = parser.parse_args(argv)

    ret = 0

    if not args.packages and not args.maintainer:
        (name, email) = get_maintainer()
        if email:
            logging.info("Processing packages maintained by %s", email)
            args.maintainer = [email]
        else:
            parser.print_usage()
            sys.exit(1)

    if not args.vcswatch:
        logging.info(
            "Use --vcswatch to only process packages for which "
            "vcswatch found pending commits."
        )

    if args.apt_repository:
        apt_repo = RemoteApt.from_string(
            args.apt_repository, args.apt_repository_key)
    else:
        apt_repo = LocalApt()

    if args.maintainer:
        if args.packages:
            parser.print_error(
                '--maintainer is incompatible with specifying package names')
        packages = select_apt_packages(
            apt_repo, args.packages, args.maintainer)
    else:
        packages = args.packages

    if not packages:
        logging.info("No packages found.")
        parser.print_usage()
        sys.exit(1)

    if args.shuffle:
        import random
        random.shuffle(packages)

    stats = {}

    def inc_stats(result):
        stats.setdefault(result, 0)
        stats[result] += 1

    if args.vcswatch:
        packages, failures, extra_data = vcswatch_prescan_packages(
            packages, inc_stats, exclude=args.exclude,
            min_commit_age=args.min_commit_age,
            allowed_committers=args.allowed_committer)
        if failures > 0:
            ret = 1
    else:
        extra_data = {}

    if len(packages) > 1:
        logging.info(
            "Uploading %d packages: %s", len(packages), ", ".join(packages))

    last_attempt = open_last_attempt_db()

    if last_attempt:
        orig_packages = list(packages)

        def last_attempt_key(p):
            try:
                t = int(last_attempt[p.encode('utf-8')])
            except KeyError:
                t = 0
            return (t, orig_packages.index(p))
        packages.sort(key=last_attempt_key)

    for package in packages:
        vcs_type = extra_data.get(package, {}).get('vcs_type')
        vcs_url = extra_data.get(package, {}).get('vcs_url')
        archive_version = extra_data.get(package, {}).get('archive_version')
        source_name = extra_data.get(package, {}).get('package')

        try:
            process_package(
                apt_repo, package,
                builder=args.builder, exclude=args.exclude,
                autopkgtest_only=args.autopkgtest_only,
                gpg_verification=args.gpg_verification,
                acceptable_keys=args.acceptable_keys,
                debug=args.debug,
                diff=args.diff, min_commit_age=args.min_commit_age,
                allowed_committers=args.allowed_committer,
                vcs_type=vcs_type, vcs_url=vcs_url,
                archive_version=archive_version,
                source_name=source_name,
                verify_command=args.verify_command)
        except PackageProcessingFailure as e:
            inc_stats(e.reason)
            ret = 1
        except PackageIgnored as e:
            inc_stats(e.reason)

        if last_attempt:
            last_attempt[package.encode('utf-8')] = b'%d' % (time.time())

    if len(packages) > 1:
        logging.info('Results:')
        for error, c in stats.items():
            logging.info('  %s: %d', error, c)

    return ret


if __name__ == "__main__":
    sys.exit(main(sys.argv))
