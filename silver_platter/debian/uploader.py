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

from debian.changelog import Version

from breezy.branch import Branch
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

from ..proposal import BranchChanger
from ..utils import TemporarySprout

from . import (
    get_source_package,
    source_package_vcs_url,
    propose_or_push,
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


class PackageUploader(BranchChanger):

    def __init__(self, pkg, last_uploaded_version, gpg_strategy,
                 min_commit_age):
        self._pkg = pkg
        self._gpg_strategy = gpg_strategy
        self._last_uploaded_version = Version(last_uploaded_version)
        self._target_changes = None
        self._builder = ('sbuild --source --source-only-changes '
                         '--debbuildopt=-v%s' % self._last_uploaded_version)
        self._min_commit_age = min_commit_age

    def __repr__(self):
        return "PackageUploader(%r)" % (self._pkg, )

    def make_changes(self, local_tree):
        cl, top_level = find_changelog(
                local_tree, merge=False, max_blocks=None)
        if cl.version == self._last_uploaded_version:
            raise Exception(
                    "nothing to upload, latest version is in archive: %s" %
                    cl.version)
        previous_version_in_branch = changelog_find_previous_upload(cl)
        if self._last_uploaded_version > previous_version_in_branch:
            raise Exception(
                "last uploaded version more recent than previous "
                "version in branch: %r > %r" % (
                    self._last_uploaded_version, previous_version_in_branch))

        print("Checking revisions since %s" % self._last_uploaded_version)
        with local_tree.lock_read():
            last_release_revid = find_last_release_revid(
                    local_tree.branch, self._last_uploaded_version)
            graph = local_tree.branch.repository.get_graph()
            revids = list(graph.iter_lefthand_ancestry(
                local_tree.branch.last_revision(), [last_release_revid]))
            if not revids:
                print("No pending changes")
                return
            if self._gpg_strategy:
                count, result, all_verifiables = gpg.bulk_verify_signatures(
                        local_tree.branch.repository, revids,
                        self._gpg_strategy)
                for revid, code, key in result:
                    if code != gpg.SIGNATURE_VALID:
                        raise Exception(
                            "No valid GPG signature on %r: %d" %
                            (revid, code))
            for revid, rev in local_tree.branch.repository.iter_revisions(
                    revids):
                check_revision(rev, self._min_commit_age)

            if cl.distributions != "UNRELEASED":
                raise Exception("Nothing left to release")
        release(local_tree)
        target_dir = tempfile.mkdtemp()
        self._target_changes = _build_helper(
                local_tree, local_tree.branch, target_dir,
                builder=self._builder)
        debsign(self._target_changes)

    def post_land(self, main_branch):
        if not self._target_changes:
            target_dir = tempfile.mkdtemp()
            with TemporarySprout(main_branch) as local_tree:
                self._target_changes = _build_helper(
                    local_tree, local_tree.branch, target_dir,
                    builder=self._builder)
        dput_changes(self._target_changes)


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


def main(args):
    for package in args.packages:
        pkg_source = get_source_package(package)
        vcs_type, vcs_url = source_package_vcs_url(pkg_source)
        main_branch = Branch.open(vcs_url)
        with main_branch.lock_read():
            branch_config = main_branch.get_config_stack()
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

            branch_changer = PackageUploader(
                    pkg_source["Package"], pkg_source["Version"], gpg_strategy,
                    args.min_commit_age)

            propose_or_push(
                main_branch, "new-upload", branch_changer, mode='push')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(prog='upload-pending-commits')
    setup_parser(parser)
    # TODO(jelmer): Support requiring that autopkgtest is present and passing
    args = parser.parse_args()
    main(args)
