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

import silver_platter   # noqa: F401

import datetime
from email.utils import parseaddr
import os
import subprocess
import sys
import tempfile
from typing import List

from debmutate.changelog import (
    ChangelogEditor,
    changeblock_ensure_first_line,
    )
from debmutate.control import ControlEditor

from breezy import gpg
from breezy.errors import NoSuchTag
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
    debsign,
    )
from breezy.trace import note, show_error, warning

from debian.changelog import get_maintainer

from . import (
    apt_get_source_package,
    source_package_vcs,
    split_vcs_url,
    Workspace,
    DEFAULT_BUILDER,
    select_probers,
    )
from ..utils import (
    open_branch,
    BranchUnavailable,
    BranchMissing,
    BranchUnsupported,
    )


class NoUnuploadedChanges(Exception):
    """Indicates there are no unuploaded changes for a package."""

    def __init__(self, archive_version):
        self.archive_version = archive_version
        super(NoUnuploadedChanges, self).__init__(
            "nothing to upload, latest version is in archive: %s" %
            archive_version)


class NoUnreleasedChanges(Exception):
    """Indicates there are no unreleased changes for a package."""

    def __init__(self, version):
        self.version = version
        super(NoUnreleasedChanges, self).__init__(
            "nothing to upload, latest version in vcs is not unreleased: %s" %
            version)


class RecentCommits(Exception):
    """Indicates there are too recent commits for a package."""

    def __init__(self, commit_age, min_commit_age):
        self.commit_age = commit_age
        self.min_commit_age = min_commit_age
        super(RecentCommits, self).__init__(
            "Last commit is only %d days old (< %d)" % (
                self.commit_age, self.min_commit_age))


class LastReleaseRevisionNotFound(Exception):
    """The revision for the last uploaded release can't be found."""

    def __init__(self, package, version):
        self.package = package
        self.version = version
        super(LastReleaseRevisionNotFound, self).__init__(
            "Unable to find revision matching version %r for %s" % (
                version, package))


def check_revision(rev, min_commit_age):
    """Check whether a revision can be included in an upload.

    Args:
      rev: revision to check
      min_commit_age: Minimum age for revisions
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


def find_last_release_revid(branch, version):
    db = DistributionBranch(branch, None)
    return db.revid_of_version(version)


def get_maintainer_keys(context):
    for key in context.keylist(
            source='/usr/share/keyrings/debian-keyring.gpg'):
        yield key.fpr
        for subkey in key.subkeys:
            yield subkey.keyid


def prepare_upload_package(
        local_tree, subpath, pkg, last_uploaded_version, builder,
        gpg_strategy=None, min_commit_age=None):
    if local_tree.has_filename(os.path.join(subpath, 'debian/gbp.conf')):
        subprocess.check_call(
            ['gbp', 'dch', '--ignore-branch'],
            cwd=local_tree.abspath('.'))
    cl, top_level = find_changelog(local_tree, merge=False, max_blocks=None)
    if cl.version == last_uploaded_version:
        raise NoUnuploadedChanges(cl.version)
    previous_version_in_branch = changelog_find_previous_upload(cl)
    if last_uploaded_version > previous_version_in_branch:
        raise Exception(
            "last uploaded version more recent than previous "
            "version in branch: %r > %r" % (
                last_uploaded_version, previous_version_in_branch))

    note("Checking revisions since %s" % last_uploaded_version)
    with local_tree.lock_read():
        try:
            last_release_revid = find_last_release_revid(
                    local_tree.branch, last_uploaded_version)
        except NoSuchTag:
            raise LastReleaseRevisionNotFound(pkg, last_uploaded_version)
        graph = local_tree.branch.repository.get_graph()
        revids = list(graph.iter_lefthand_ancestry(
            local_tree.branch.last_revision(), [last_release_revid]))
        if not revids:
            note("No pending changes")
            return
        if gpg_strategy:
            note('Verifying GPG signatures...')
            count, result, all_verifiables = gpg.bulk_verify_signatures(
                    local_tree.branch.repository, revids,
                    gpg_strategy)
            for revid, code, key in result:
                if code != gpg.SIGNATURE_VALID:
                    raise Exception(
                        "No valid GPG signature on %r: %d" %
                        (revid, code))
        for revid, rev in local_tree.branch.repository.iter_revisions(
                revids):
            check_revision(rev, min_commit_age)

        if cl.distributions != "UNRELEASED":
            raise NoUnreleasedChanges(cl.version)
    qa_upload = False
    team_upload = False
    control_path = local_tree.abspath(os.path.join(subpath, 'debian/control'))
    with ControlEditor(control_path) as e:
        maintainer = parseaddr(e.source['Maintainer'])
        if maintainer[1] == 'packages@qa.debian.org':
            qa_upload = True
        # TODO(jelmer): Check whether this is a team upload
        # TODO(jelmer): determine whether this is a NMU upload
    if qa_upload or team_upload:
        changelog_path = local_tree.abspath(
            os.path.join(subpath, 'debian/changelog'))
        with ChangelogEditor(changelog_path) as e:
            if qa_upload:
                changeblock_ensure_first_line(e[0], 'QA upload.')
            elif team_upload:
                changeblock_ensure_first_line(e[0], 'Team upload.')
        local_tree.commit(
            specific_files=[os.path.join(subpath, 'debian/changelog')],
            message='Mention QA Upload.',
            allow_pointless=False,
            reporter=NullCommitReporter())
    tag_name = release(local_tree, subpath)
    target_dir = tempfile.mkdtemp()
    builder = builder.replace("${LAST_VERSION}", last_uploaded_version)
    target_changes = _build_helper(
        local_tree, subpath, local_tree.branch, target_dir, builder=builder)
    debsign(target_changes)
    return target_changes, tag_name


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


def select_vcswatch_packages(packages: List[str], maintainer: List[str]):
    import psycopg2
    conn = psycopg2.connect(
        database="udd",
        user="udd-mirror",
        password="udd-mirror",
        host="udd-mirror.debian.net")
    cursor = conn.cursor()
    args = []
    query = """\
    SELECT sources.source, vcswatch.url
    FROM vcswatch JOIN sources ON sources.source = vcswatch.source
    WHERE
     vcswatch.status IN ('COMMITS', 'NEW') AND
     sources.release = 'sid'
"""
    if maintainer:
        query += " AND sources.maintainer_email IN (%s)"
        args.append(tuple(maintainer))
    if packages:
        query += " AND sources.source IN (%s)"
        args.append(tuple(packages))

    cursor.execute(query, tuple(args))

    packages = []
    for package, vcs_url in cursor.fetchall():
        packages.append(package)
    return packages


def main(argv):
    import argparse
    parser = argparse.ArgumentParser(prog='upload-pending-commits')
    parser.add_argument("packages", nargs='*')
    parser.add_argument(
        '--dry-run', action='store_true',
        help='Dry run changes.')
    parser.add_argument(
        '--acceptable-keys',
        help='List of acceptable GPG keys',
        action='append', default=[], type=str)
    parser.add_argument(
        '--gpg-verification',
        help='Verify GPG signatures on commits', action='store_true')
    parser.add_argument(
        '--min-commit-age',
        help='Minimum age of the last commit, in days',
        type=int, default=0)
    parser.add_argument(
        '--diff',
        action='store_true',
        help='Show diff.')
    parser.add_argument(
        '--builder',
        type=str,
        help='Build command',
        default=(DEFAULT_BUILDER + ' --source --source-only-changes '
                 '--debbuildopt=-v${LAST_VERSION}'))
    parser.add_argument(
        '--maintainer',
        type=str,
        action='append',
        help='Select all packages maintainer by specified maintainer.')
    parser.add_argument(
        '--vcswatch',
        action='store_true',
        default=False,
        help='Use vcswatch to determine what packages need uploading.')
    parser.add_argument(
        '--autopkgtest-only',
        action='store_true',
        help='Only process packages with autopkgtests.')

    args = parser.parse_args(argv)
    ret = 0

    if not args.packages and not args.maintainer:
        (name, email) = get_maintainer()
        if email:
            note('Processing packages maintained by %s', email)
            args.maintainer = [email]
        else:
            parser.print_usage()
            sys.exit(1)

    if args.vcswatch:
        packages = select_vcswatch_packages(
            args.packages, args.maintainer)
    else:
        note('Use --vcswatch to only process packages for which '
             'vcswatch found pending commits.')
        if args.maintainer:
            packages = select_apt_packages(
                args.packages, args.maintainer)
        else:
            packages = args.packages

    if not packages:
        note('No packages found.')
        parser.print_usage()
        sys.exit(1)

    # TODO(jelmer): Sort packages by last commit date; least recently changed
    # commits are more likely to be successful.

    if len(packages) > 1:
        note('Uploading packages: %s', ', '.join(packages))

    for package in packages:
        note('Processing %s', package)
        # Can't use open_packaging_branch here, since we want to use pkg_source
        # later on.
        if '/' not in package:
            pkg_source = apt_get_source_package(package)
            try:
                vcs_type, vcs_url = source_package_vcs(pkg_source)
            except KeyError:
                note('%s: no declared vcs location, skipping',
                     pkg_source['Package'])
                ret = 1
                continue
            source_name = pkg_source['Package']
            source_version = pkg_source['Version']
            has_testsuite = 'Testsuite' in pkg_source
        else:
            vcs_url = package
            vcs_type = None
            source_name = None
            source_version = None
            has_testsuite = None
        (location, branch_name, subpath) = split_vcs_url(vcs_url)
        if subpath is None:
            subpath = ''
        probers = select_probers(vcs_type)
        try:
            main_branch = open_branch(
                location, probers=probers, name=branch_name)
        except (BranchUnavailable, BranchMissing, BranchUnsupported) as e:
            show_error('%s: %s', vcs_url, e)
            ret = 1
            continue
        with Workspace(main_branch) as ws:
            if source_name is None:
                with ControlEditor(
                        ws.local_tree.abspath(
                            os.path.join(subpath, 'debian/control'))
                        ) as ce:
                    source_name = ce.source['Source']
                with ChangelogEditor(
                        ws.local_tree.abspath(
                            os.path.join(subpath, 'debian/changelog'))
                        ) as cle:
                    source_version = cle[0].version
                has_testsuite = 'Testsuite' in ce.source
            if (args.autopkgtest_only and
                    not has_testsuite and
                    not ws.local_tree.has_filename(
                        os.path.join(subpath, 'debian/tests/control'))):
                note('%s: Skipping, package has no autopkgtest.',
                     source_name)
                continue
            branch_config = ws.local_tree.branch.get_config_stack()
            if args.gpg_verification:
                gpg_strategy = gpg.GPGStrategy(branch_config)
                if args.acceptable_keys:
                    acceptable_keys = args.acceptable_keys
                else:
                    acceptable_keys = list(get_maintainer_keys(
                        gpg_strategy.context))
                gpg_strategy.set_acceptable_keys(','.join(acceptable_keys))
            else:
                gpg_strategy = None

            try:
                target_changes, tag_name = prepare_upload_package(
                    ws.local_tree, subpath,
                    source_name, source_version,
                    builder=args.builder, gpg_strategy=gpg_strategy,
                    min_commit_age=args.min_commit_age)
            except BuildFailedError as e:
                warning(
                    '%s: package failed to build: %s',
                    source_name, e)
                ret = 1
                continue
            except LastReleaseRevisionNotFound as e:
                warning(
                    '%s: Unable to find revision matching last release '
                    '%s, skipping.', source_name, e.version)
                ret = 1
                continue
            except RecentCommits as e:
                note('%s: Recent commits (%d days), skipping.',
                     source_name, e.commit_age)
                continue
            except NoUnuploadedChanges:
                note('%s: No unuploaded changes, skipping.',
                     source_name)
                continue
            except NoUnreleasedChanges:
                note('%s: No unreleased changes, skipping.',
                     source_name)
                continue

            tags = []
            if tag_name is not None:
                note('Pushing tag %s', tag_name)
                tags.append(tag_name)
            ws.push(dry_run=args.dry_run, tags=tags)
            if not args.dry_run:
                dput_changes(target_changes)
            if args.diff:
                sys.stdout.flush()
                ws.show_diff(sys.stdout.buffer)
                sys.stdout.buffer.flush()

    return ret


if __name__ == '__main__':
    sys.exit(main(sys.argv))
