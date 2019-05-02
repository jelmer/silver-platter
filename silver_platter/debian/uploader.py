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
from breezy.trace import show_error

from . import (
    get_source_package,
    source_package_vcs_url,
    Workspace,
    DEFAULT_BUILDER,
    )
from ..utils import (
    open_branch,
    BranchUnavailable,
    )


def check_revision(rev, min_commit_age):
    print("checking %r" % rev)
    # TODO(jelmer): deal with timezone
    commit_time = datetime.datetime.fromtimestamp(rev.timestamp)
    time_delta = datetime.datetime.now() - commit_time
    if time_delta.days < min_commit_age:
        raise Exception("Last commit is only %d days old" % time_delta.days)
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
        local_tree, pkg, last_uploaded_version, gpg_strategy, min_commit_age,
        builder):
    cl, top_level = find_changelog(
            local_tree, merge=False, max_blocks=None)
    if cl.version == last_uploaded_version:
        raise Exception(
                "nothing to upload, latest version is in archive: %s" %
                cl.version)
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
    release(local_tree)
    target_dir = tempfile.mkdtemp()
    target_changes = _build_helper(
        local_tree, local_tree.branch, target_dir, builder=builder)
    debsign(target_changes)
    return target_changes


def setup_parser(parser):
    parser.add_argument("packages", nargs='*')
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


def main(args):
    ret = 0
    for package in args.packages:
        # Can't use open_packaging_branch here, since we want to use pkg_source
        # later on.
        pkg_source = get_source_package(package)
        vcs_type, vcs_url = source_package_vcs_url(pkg_source)
        try:
            main_branch = open_branch(vcs_url)
        except BranchUnavailable as e:
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
                ws.local_tree,
                pkg_source["Package"], pkg_source["Version"], gpg_strategy,
                args.min_commit_age, args.builder)

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
