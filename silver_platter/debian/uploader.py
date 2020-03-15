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
import subprocess
import tempfile

from breezy import gpg
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
from breezy.trace import note, show_error

from . import (
    get_source_package,
    source_package_vcs_url,
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


class RecentCommits(Exception):
    """Indicates there are too recent commits for a package."""

    def __init__(self, commit_age, min_commit_age):
        self.commit_age = commit_age
        self.min_commit_age = min_commit_age
        super(RecentCommits, self).__init__(
            "Last commit is only %d days old (< %d)" % (
                self.commit_age, self.min_commit_age))


def check_revision(rev, min_commit_age):
    """Check whether a revision can be included in an upload.

    Args:
      rev: revision to check
      min_commit_age: Minimum age for revisions
    Raises:
      RecentCommits: When there are commits younger than min_commit_age
    """
    print("checking %r" % rev)
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
    if local_tree.has_filename('debian/gbp.conf'):
        subprocess.check_call(['gbp', 'dch'], cwd=local_tree.abspath('.'))
    cl, top_level = find_changelog(local_tree, merge=False, max_blocks=None)
    if cl.version == last_uploaded_version:
        raise NoUnuploadedChanges(cl.version)
    previous_version_in_branch = changelog_find_previous_upload(cl)
    if last_uploaded_version > previous_version_in_branch:
        raise Exception(
            "last uploaded version more recent than previous "
            "version in branch: %r > %r" % (
                last_uploaded_version, previous_version_in_branch))

    print("Checking revisions since %s" % last_uploaded_version)
    with local_tree.lock_read():
        last_release_revid = find_last_release_revid(
                local_tree.branch, last_uploaded_version)
        graph = local_tree.branch.repository.get_graph()
        revids = list(graph.iter_lefthand_ancestry(
            local_tree.branch.last_revision(), [last_release_revid]))
        if not revids:
            print("No pending changes")
            return
        if gpg_strategy:
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
            raise Exception("Nothing left to release")
    release(local_tree, subpath)
    target_dir = tempfile.mkdtemp()
    builder = builder.replace("${LAST_VERSION}", last_uploaded_version)
    target_changes = _build_helper(
        local_tree, subpath, local_tree.branch, target_dir, builder=builder)
    debsign(target_changes)
    return target_changes


def setup_parser(parser):
    parser.add_argument("packages", nargs='*')
    parser.add_argument(
        '--dry-run', action='store_true',
        help='Dry run changes.')
    parser.add_argument(
        '--acceptable-keys',
        help='List of acceptable GPG keys',
        action='append', default=[], type=str)
    parser.add_argument(
        '--no-gpg-verification',
        help='Do not verify GPG signatures', action='store_true')
    parser.add_argument(
        '--min-commit-age',
        help='Minimum age of the last commit, in days',
        type=int, default=0)
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
        help='Use vcswatch to determine what packages need uploading.')


def select_apt_packages(package_names, maintainer):
    packages = []
    import apt_pkg
    from email.utils import parseaddr
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


def select_vcswatch_packages(packages, maintainer):
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


def main(args):
    ret = 0

    if args.vcswatch:
        packages = select_vcswatch_packages(
            args.packages, args.maintainer)
    else:
        note('Use --vcswatch to only process packages for which '
             'vcswatch found pending commits.')
        packages = select_apt_packages(
            args.packages, args.maintainer)

    # TODO(jelmer): Sort packages by last commit date; least recently changed
    # commits are more likely to be successful.

    note('Uploading packages: %s', ', '.join(packages))

    for package in packages:
        # Can't use open_packaging_branch here, since we want to use pkg_source
        # later on.
        pkg_source = get_source_package(package)
        vcs_type, vcs_url = source_package_vcs_url(pkg_source)
        probers = select_probers(vcs_type)
        try:
            main_branch = open_branch(vcs_url, probers=probers)
        except (BranchUnavailable, BranchMissing, BranchUnsupported) as e:
            show_error('%s: %s', vcs_url, e)
            ret = 1
            continue
        with Workspace(main_branch) as ws:
            branch_config = ws.local_tree.branch.get_config_stack()
            if args.no_gpg_verification:
                gpg_strategy = None
            else:
                gpg_strategy = gpg.GPGStrategy(branch_config)
                if args.acceptable_keys:
                    acceptable_keys = args.acceptable_keys
                else:
                    acceptable_keys = list(get_maintainer_keys(
                        gpg_strategy.context))
                gpg_strategy.set_acceptable_keys(','.join(acceptable_keys))

            target_changes = prepare_upload_package(
                ws.local_tree, '',
                pkg_source["Package"], pkg_source["Version"],
                builder=args.builder, gpg_strategy=gpg_strategy,
                min_commit_age=args.min_commit_age)

            # TODO(jelmer): Upload the right tags

            ws.push(dry_run=args.dry_run)
            if not args.dry_run:
                dput_changes(target_changes)
    return ret


if __name__ == '__main__':
    import argparse
    import sys
    parser = argparse.ArgumentParser(prog='upload-pending-commits')
    setup_parser(parser)
    # TODO(jelmer): Support requiring that autopkgtest is present and passing
    args = parser.parse_args()
    sys.exit(main(args))
