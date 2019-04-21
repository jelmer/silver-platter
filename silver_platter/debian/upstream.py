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
    get_hoster,
    publish_changes,
    UnsupportedHoster,
    )
from ..utils import (
    run_pre_check,
    )

from . import (
    open_packaging_branch,
    Workspace,
    )
from breezy.plugins.debian.errors import UpstreamAlreadyImported

from breezy.trace import note, warning


BRANCH_NAME = "new-upstream-release"


def merge_upstream(tree, snapshot=False):
    # TODO(jelmer): Don't call UI implementation, refactor brz-debian
    cmd_merge_upstream().run(directory=tree.basedir, snapshot=snapshot)


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
    possible_hosters = []
    for package in args.packages:
        main_branch = open_packaging_branch(package)
        overwrite = False

        try:
            hoster = get_hoster(main_branch, possible_hosters=possible_hosters)
        except UnsupportedHoster as e:
            if args.mode != 'push':
                raise
            # We can't figure out what branch to resume from when there's no
            # hoster that can tell us.
            warning('Unsupported hoster (%s), will attempt to push to %s',
                    e, main_branch.user_url)
        with Workspace(main_branch) as ws:
            run_pre_check(ws.local_tree, args.pre_check)
            try:
                merge_upstream(tree=ws.local_tree, snapshot=args.snapshot)
            except UpstreamAlreadyImported as e:
                note('Last upstream version %s already imported', e.version)
                continue
            with ws.local_tree.get_file('debian/changelog') as f:
                cl = Changelog(f.read())
                upstream_version = cl.version.upstream_version
            subprocess.check_call(
                ["debcommit", "-a"], cwd=ws.local_tree.basedir)

            if args.build_verify:
                ws.build(builder=args.builder)

            def get_proposal_description(existing_proposal):
                return "Merge new upstream release %s" % upstream_version

            (proposal, is_new) = publish_changes(
                ws, args.mode, BRANCH_NAME,
                get_proposal_description=get_proposal_description,
                dry_run=args.dry_run, hoster=hoster,
                overwrite_existing=overwrite)

            if proposal:
                if is_new:
                    note('%s: Created new merge proposal %s.',
                         package, proposal.url)
                else:
                    note('%s: Updated merge proposal %s.',
                         package, proposal.url)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(prog='propose-new-upstream')
    setup_parser(parser)
    args = parser.parse_args()
    main(args)
