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

import silver_platter  # noqa: F401

import datetime
from email.utils import parseaddr
import logging
import os
import subprocess
import sys
import tempfile
from typing import List

from debmutate.changelog import (
    ChangelogEditor,
    ChangelogParseError,
    changeblock_ensure_first_line,
)
from debmutate.control import ControlEditor

from breezy import gpg
from breezy.config import extract_email_address
from breezy.errors import NoSuchTag, PermissionDenied
from breezy.commit import NullCommitReporter
from breezy.plugins.debian.builder import BuildFailedError
from breezy.plugins.debian.cmds import _build_helper
from breezy.plugins.debian.import_dsc import (
    DistributionBranch,
)
from breezy.plugins.debian.release import (
    release,
)
from breezy.plugins.debian.util import (
    changelog_find_previous_upload,
    dput_changes,
    find_changelog,
    MissingChangelogError,
    NoPreviousUpload,
)
from breezy.plugins.debian.upstream import MissingUpstreamTarball

from debian.changelog import get_maintainer

from . import (
    apt_get_source_package,
    source_package_vcs,
    split_vcs_url,
    Workspace,
    DEFAULT_BUILDER,
    select_probers,
    NoSuchPackage,
)
from ..utils import (
    open_branch,
    BranchUnavailable,
    BranchMissing,
    BranchUnsupported,
    BranchRateLimited,
)


def connect_udd_mirror():
    import psycopg2

    return psycopg2.connect(
        database="udd",
        user="udd-mirror",
        password="udd-mirror",
        host="udd-mirror.debian.net",
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

    def __init__(self, archive_version, vcs_version):
        self.archive_version = archive_version
        self.vcs_version = vcs_version
        super(LastUploadMoreRecent, self).__init__(
            "last upload (%s) is more recent than vcs (%s)"
            % (archive_version, vcs_version)
        )


class NoUnuploadedChanges(Exception):
    """Indicates there are no unuploaded changes for a package."""

    def __init__(self, archive_version):
        self.archive_version = archive_version
        super(NoUnuploadedChanges, self).__init__(
            "nothing to upload, latest version is in archive: %s" % archive_version
        )


class NoUnreleasedChanges(Exception):
    """Indicates there are no unreleased changes for a package."""

    def __init__(self, version):
        self.version = version
        super(NoUnreleasedChanges, self).__init__(
            "nothing to upload, latest version in vcs is not unreleased: %s" % version
        )


class RecentCommits(Exception):
    """Indicates there are too recent commits for a package."""

    def __init__(self, commit_age, min_commit_age):
        self.commit_age = commit_age
        self.min_commit_age = min_commit_age
        super(RecentCommits, self).__init__(
            "Last commit is only %d days old (< %d)"
            % (self.commit_age, self.min_commit_age)
        )


class CommitterNotAllowed(Exception):
    """Specified committer is not allowed."""

    def __init__(self, committer, allowed_committers):
        self.committer = committer
        self.allowed_committers = allowed_committers
        super(CommitterNotAllowed, self).__init__(
            "Committer %s not in allowed committers: %r"
            % (self.committer, self.allowed_committers)
        )


class LastReleaseRevisionNotFound(Exception):
    """The revision for the last uploaded release can't be found."""

    def __init__(self, package, version):
        self.package = package
        self.version = version
        super(LastReleaseRevisionNotFound, self).__init__(
            "Unable to find revision matching version %r for %s" % (version, package)
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
    committer_email = extract_email_address(rev.committer)
    if allowed_committers and committer_email not in allowed_committers:
        raise CommitterNotAllowed(committer_email, allowed_committers)


def find_last_release_revid(branch, version):
    db = DistributionBranch(branch, None)
    return db.revid_of_version(version)


def get_maintainer_keys(context):
    for key in context.keylist(source="/usr/share/keyrings/debian-keyring.gpg"):
        yield key.fpr
        for subkey in key.subkeys:
            yield subkey.keyid


class GbpDchFailed(Exception):
    """gbp dch failed to run"""


def prepare_upload_package(  # noqa: C901
    local_tree,
    subpath,
    pkg,
    last_uploaded_version,
    builder,
    gpg_strategy=None,
    min_commit_age=None,
    allowed_committers=None,
):
    if local_tree.has_filename(os.path.join(subpath, "debian/gbp.conf")):
        try:
            subprocess.check_call(
                ["gbp", "dch", "--ignore-branch"], cwd=local_tree.abspath(".")
            )
        except subprocess.CalledProcessError:
            # TODO(jelmer): gbp dch sometimes fails when there is no existing
            # open changelog entry; it fails invoking "dpkg --lt None <old-version>"
            raise GbpDchFailed()
    cl, top_level = find_changelog(local_tree, merge=False, max_blocks=None)
    if cl.version == last_uploaded_version:
        raise NoUnuploadedChanges(cl.version)
    try:
        previous_version_in_branch = changelog_find_previous_upload(cl)
    except NoPreviousUpload:
        pass
    else:
        if last_uploaded_version > previous_version_in_branch:
            raise LastUploadMoreRecent(last_uploaded_version, previous_version_in_branch)

    logging.info("Checking revisions since %s" % last_uploaded_version)
    with local_tree.lock_read():
        try:
            last_release_revid = find_last_release_revid(
                local_tree.branch, last_uploaded_version
            )
        except NoSuchTag:
            raise LastReleaseRevisionNotFound(pkg, last_uploaded_version)
        graph = local_tree.branch.repository.get_graph()
        revids = list(
            graph.iter_lefthand_ancestry(
                local_tree.branch.last_revision(), [last_release_revid]
            )
        )
        if not revids:
            logging.info("No pending changes")
            return
        if gpg_strategy:
            logging.info("Verifying GPG signatures...")
            count, result, all_verifiables = gpg.bulk_verify_signatures(
                local_tree.branch.repository, revids, gpg_strategy
            )
            for revid, code, key in result:
                if code != gpg.SIGNATURE_VALID:
                    raise Exception("No valid GPG signature on %r: %d" % (revid, code))
        for revid, rev in local_tree.branch.repository.iter_revisions(revids):
            if rev is not None:
                check_revision(rev, min_commit_age, allowed_committers)

        if cl.distributions != "UNRELEASED":
            raise NoUnreleasedChanges(cl.version)
    qa_upload = False
    team_upload = False
    control_path = local_tree.abspath(os.path.join(subpath, "debian/control"))
    with ControlEditor(control_path) as e:
        maintainer = parseaddr(e.source["Maintainer"])
        if maintainer[1] == "packages@qa.debian.org":
            qa_upload = True
        # TODO(jelmer): Check whether this is a team upload
        # TODO(jelmer): determine whether this is a NMU upload
    if qa_upload or team_upload:
        changelog_path = local_tree.abspath(os.path.join(subpath, "debian/changelog"))
        with ChangelogEditor(changelog_path) as e:
            if qa_upload:
                changeblock_ensure_first_line(e[0], "QA upload.")
            elif team_upload:
                changeblock_ensure_first_line(e[0], "Team upload.")
        local_tree.commit(
            specific_files=[os.path.join(subpath, "debian/changelog")],
            message="Mention QA Upload.",
            allow_pointless=False,
            reporter=NullCommitReporter(),
        )
    tag_name = release(local_tree, subpath)
    target_dir = tempfile.mkdtemp()
    builder = builder.replace("${LAST_VERSION}", last_uploaded_version)
    target_changes = _build_helper(
        local_tree, subpath, local_tree.branch, target_dir, builder=builder
    )
    debsign(target_changes['source'])
    return target_changes['source'], tag_name


def select_apt_packages(package_names, maintainer):
    packages = []
    import apt_pkg

    apt_pkg.init()
    sources = apt_pkg.SourceRecords()
    while sources.step():
        if maintainer:
            fullname, email = parseaddr(sources.maintainer)
            if email not in maintainer:
                continue

        if package_names and sources.package not in package_names:
            continue

        packages.append(sources.package)

    return packages


def select_vcswatch_packages(
    packages: List[str], maintainer: List[str], autopkgtest_only: bool
):
    conn = connect_udd_mirror()
    cursor = conn.cursor()
    args = []
    query = """\
    SELECT sources.source, vcswatch.url
    FROM vcswatch JOIN sources ON sources.source = vcswatch.source
    WHERE
     vcswatch.status IN ('COMMITS', 'NEW') AND
     sources.release = 'sid'
"""
    if autopkgtest_only:
        query += " AND sources.testsuite != '' "
    if maintainer:
        query += " AND sources.maintainer_email in %s"
        args.append(tuple(maintainer))
    if packages:
        query += " AND sources.source IN %s"
        args.append(tuple(packages))

    cursor.execute(query, tuple(args))

    packages = []
    for package, vcs_url in cursor.fetchall():
        packages.append(package)
    return packages


def main(argv):  # noqa: C901
    import argparse

    parser = argparse.ArgumentParser(prog="upload-pending-commits")
    parser.add_argument("packages", nargs="*")
    parser.add_argument("--dry-run", action="store_true", help="Dry run changes.")
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
        help="Select all packages maintainer by specified maintainer.",
    )
    parser.add_argument(
        "--vcswatch",
        action="store_true",
        default=False,
        help="Use vcswatch to determine what packages need uploading.",
    )
    parser.add_argument(
        "--exclude", type=str, action="append", default=[], help="Ignore source package"
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

    if args.vcswatch:
        packages = select_vcswatch_packages(
            args.packages, args.maintainer, args.autopkgtest_only
        )
    else:
        logging.info(
            "Use --vcswatch to only process packages for which "
            "vcswatch found pending commits."
        )
        if args.maintainer:
            packages = select_apt_packages(args.packages, args.maintainer)
        else:
            packages = args.packages

    if not packages:
        logging.info("No packages found.")
        parser.print_usage()
        sys.exit(1)

    # TODO(jelmer): Sort packages by last commit date; least recently changed
    # commits are more likely to be successful.

    stats = {
        'not-in-apt': 0,
        'not-in-vcs': 0,
        'vcs-inaccessible': 0,
        'gbp-dch-failed': 0,
        'missing-upstream-tarball': 0,
        'committer-not-allowed': 0,
        'build-failed': 0,
        'last-release-missing': 0,
        'last-upload-not-in-vcs': 0,
        'missing-changelog': 0,
        'recent-commits': 0,
        'no-unuploaded-changes': 0,
        'no-unreleased-changes': 0,
        'vcs-permission-denied': 0,
        'changelog-parse-error': 0,
        }
    if args.autopkgtest_only:
        stats['no-autopkgtest'] = 0

    if len(packages) > 1:
        logging.info("Uploading packages: %s", ", ".join(packages))

    for package in packages:
        logging.info("Processing %s", package)
        # Can't use open_packaging_branch here, since we want to use pkg_source
        # later on.
        if "/" not in package:
            try:
                pkg_source = apt_get_source_package(package)
            except NoSuchPackage:
                stats['not-in-apt'] += 1
                logging.info("%s: package not found in apt", package)
                ret = 1
                continue
            try:
                vcs_type, vcs_url = source_package_vcs(pkg_source)
            except KeyError:
                stats['not-in-vcs'] += 1
                logging.info(
                    "%s: no declared vcs location, skipping", pkg_source["Package"]
                )
                ret = 1
                continue
            source_name = pkg_source["Package"]
            if source_name in args.exclude:
                continue
            source_version = pkg_source["Version"]
            has_testsuite = "Testsuite" in pkg_source
        else:
            vcs_url = package
            vcs_type = None
            source_name = None
            source_version = None
            has_testsuite = None
        (location, branch_name, subpath) = split_vcs_url(vcs_url)
        if subpath is None:
            subpath = ""
        probers = select_probers(vcs_type)
        try:
            main_branch = open_branch(location, probers=probers, name=branch_name)
        except (BranchUnavailable, BranchMissing, BranchUnsupported) as e:
            stats['vcs-inaccessible'] += 1
            logging.exception("%s: %s", vcs_url, e)
            ret = 1
            continue
        with Workspace(main_branch) as ws:
            if source_name is None:
                with ControlEditor(
                    ws.local_tree.abspath(os.path.join(subpath, "debian/control"))
                ) as ce:
                    source_name = ce.source["Source"]
                with ChangelogEditor(
                    ws.local_tree.abspath(os.path.join(subpath, "debian/changelog"))
                ) as cle:
                    source_version = cle[0].version
                has_testsuite = "Testsuite" in ce.source
            if source_name in args.exclude:
                continue
            if (
                args.autopkgtest_only
                and not has_testsuite
                and not ws.local_tree.has_filename(
                    os.path.join(subpath, "debian/tests/control")
                )
            ):
                logging.info("%s: Skipping, package has no autopkgtest.", source_name)
                stats['no-autopkgtest'] += 1
                continue
            branch_config = ws.local_tree.branch.get_config_stack()
            if args.gpg_verification:
                gpg_strategy = gpg.GPGStrategy(branch_config)
                if args.acceptable_keys:
                    acceptable_keys = args.acceptable_keys
                else:
                    acceptable_keys = list(get_maintainer_keys(gpg_strategy.context))
                gpg_strategy.set_acceptable_keys(",".join(acceptable_keys))
            else:
                gpg_strategy = None

            try:
                target_changes, tag_name = prepare_upload_package(
                    ws.local_tree,
                    subpath,
                    source_name,
                    source_version,
                    builder=args.builder,
                    gpg_strategy=gpg_strategy,
                    min_commit_age=args.min_commit_age,
                    allowed_committers=args.allowed_committer,
                )
            except GbpDchFailed as e:
                logging.warn("%s: 'gbp dch' failed to run: %s", source_name, e)
                stats['gbp-dch-failed'] += 1
                continue
            except MissingUpstreamTarball as e:
                stats['missing-upstream-tarball'] += 1
                logging.warning("%s: missing upstream tarball: %s", source_name, e)
                continue
            except BranchRateLimited as e:
                stats['rate-limited'] += 1
                logging.warning(
                    '%s: rate limited by server (retrying after %s)',
                    source_name, e.retry_after)
                ret = 1
                continue
            except CommitterNotAllowed as e:
                stats['committer-not-allowed'] += 1
                logging.warn(
                    "%s: committer %s not in allowed list: %r",
                    source_name,
                    e.committer,
                    e.allowed_committers,
                )
                continue
            except BuildFailedError as e:
                logging.warn("%s: package failed to build: %s", source_name, e)
                stats['build-failed'] += 1
                ret = 1
                continue
            except LastReleaseRevisionNotFound as e:
                logging.warn(
                    "%s: Unable to find revision matching last release "
                    "%s, skipping.",
                    source_name,
                    e.version,
                )
                stats['last-release-missing'] += 1
                ret = 1
                continue
            except LastUploadMoreRecent as e:
                stats['last-upload-not-in-vcs'] += 1
                logging.warn(
                    "%s: Last upload (%s) was more recent than VCS (%s)",
                    source_name,
                    e.archive_version,
                    e.vcs_version,
                )
                ret = 1
                continue
            except ChangelogParseError as e:
                stats['changelog-parse-error'] += 1
                logging.info("%s: Error parsing changelog: %s", source_name, e)
                ret = 1
                continue
            except MissingChangelogError:
                stats['missing-changelog'] += 1
                logging.info("%s: No changelog found, skipping.", source_name)
                ret = 1
                continue
            except RecentCommits as e:
                stats['recent-commits'] += 1
                logging.info(
                    "%s: Recent commits (%d days), skipping.", source_name, e.commit_age
                )
                continue
            except NoUnuploadedChanges:
                stats['no-unuploaded-changes'] += 1
                logging.info("%s: No unuploaded changes, skipping.", source_name)
                continue
            except NoUnreleasedChanges:
                stats['no-unreleased-changes'] += 1
                logging.info("%s: No unreleased changes, skipping.", source_name)
                continue

            tags = []
            if tag_name is not None:
                logging.info("Pushing tag %s", tag_name)
                tags.append(tag_name)
            try:
                ws.push(dry_run=args.dry_run, tags=tags)
            except PermissionDenied:
                stats['vcs-permission-denied'] += 1
                logging.info(
                    "%s: Permission denied pushing to branch, skipping.", source_name
                )
                ret = 1
                continue
            if not args.dry_run:
                dput_changes(target_changes)
            if args.diff:
                sys.stdout.flush()
                ws.show_diff(sys.stdout.buffer)
                sys.stdout.buffer.flush()

    if len(packages) > 1:
        logging.info('Results:')
        for error, c in stats.items():
            logging.info('  %s: %d', error, c)

    return ret


if __name__ == "__main__":
    sys.exit(main(sys.argv))
