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

"""Support for merging new upstream versions."""

import silver_platter  # noqa: F401

from debian.changelog import Changelog

from breezy.plugins.debian.cmds import cmd_merge_upstream
import subprocess

from ..proposal import (
    BranchChanger,
    )

from . import (
    open_packaging_branch,
    propose_or_push,
    DebuildingBranchChanger,
    PostCheckFailed,
    )
from breezy.plugins.debian.errors import UpstreamAlreadyImported

from breezy.trace import note


def merge_upstream(tree, snapshot=False):
    # TODO(jelmer): Don't call UI implementation, refactor brz-debian
    cmd_merge_upstream().run(directory=tree.basedir, snapshot=snapshot)


class NewUpstreamMerger(BranchChanger):

    def __init__(self, snapshot=False, pre_check=None, post_check=None):
        self._snapshot = snapshot
        self._pre_check = pre_check
        self._post_check = post_check

    def make_changes(self, local_tree):
        since_revid = local_tree.last_revision()
        if self._pre_check:
            if not self._pre_check(local_tree):
                return
        try:
            merge_upstream(tree=local_tree, snapshot=self._snapshot)
        except UpstreamAlreadyImported as e:
            note('Last upstream version %s already imported', e.version)
            return
        with local_tree.get_file('debian/changelog') as f:
            cl = Changelog(f.read())
            self._upstream_version = cl.version.upstream_version
        subprocess.check_call(["debcommit", "-a"], cwd=local_tree.basedir)
        if self._post_check:
            if not self._post_check(local_tree, since_revid):
                raise PostCheckFailed()

    def get_proposal_description(self, existing_proposal):
        return "Merge new upstream release %s" % self._upstream_version

    def should_create_proposal(self):
        # There are no upstream merges too small.
        return True


def setup_parser(parser):
    parser.add_argument("packages", nargs='+')
    parser.add_argument(
        '--snapshot',
        help='Merge a new upstream snapshot rather than a release',
        action='store_true')
    parser.add_argument(
        '--no-build-verify',
        help='Do not build package to verify it.',
        dest='build_verify',
        action='store_false')
    parser.add_argument(
        '--builder', type=str, default='sbuild', help='Build command.')
    parser.add_argument(
        '--pre-check',
        help='Command to run to check whether to process package.',
        type=str)
    parser.add_argument(
        "--dry-run",
        help="Create branches but don't push or propose anything.",
        action="store_true",
        default=False)
    parser.add_argument(
        '--mode',
        help='Mode for pushing', choices=['push', 'attempt-push', 'propose'],
        default="propose", type=str)


def main(args):
    for package in args.packages:
        main_branch = open_packaging_branch(package)
        # TODO(jelmer): Work out how to propose pristine-tar changes for
        # merging upstream.
        branch_changer = DebuildingBranchChanger(
            NewUpstreamMerger(args.snapshot),
            build_verify=args.build_verify, builder=args.builder)
        result = propose_or_push(
            main_branch, "new-upstream",
            branch_changer,
            mode=args.mode, dry_run=args.dry_run)
        if result.merge_proposal:
            if result.is_new:
                note('%s: Created new merge proposal %s.',
                     package, result.merge_proposal.url)
            else:
                note('%s: Updated merge proposal %s.',
                     package, result.merge_proposal.url)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(prog='propose-new-upstream')
    setup_parser(parser)
    args = parser.parse_args()
    main(args)
