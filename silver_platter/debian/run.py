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

"""Support for updating with a script."""

import sys

from breezy.trace import warning

from ..proposal import (
    enable_tag_pushing,
    find_existing_proposed,
    publish_changes,
    get_hoster,
    UnsupportedHoster,
    SUPPORTED_MODES,
    )
from ..run import (
    ScriptMadeNoChanges,
    derived_branch_name,
    script_runner,
    )

from . import (
    open_packaging_branch,
    Workspace,
    DEFAULT_BUILDER,
    )


def setup_parser(parser):
    parser.add_argument('script', help='Path to script to run.', type=str)
    parser.add_argument(
        'package', help='Package name or URL of branch to work on.', type=str)
    parser.add_argument('--refresh', action="store_true",
                        help='Refresh branch (discard current branch) and '
                        'create from scratch')
    parser.add_argument('--label', type=str,
                        help='Label to attach', action="append", default=[])
    parser.add_argument('--name', type=str,
                        help='Proposed branch name', default=None)
    parser.add_argument('--diff', action="store_true",
                        help="Show diff of generated changes.")
    parser.add_argument(
        '--mode',
        help='Mode for pushing', choices=SUPPORTED_MODES,
        default="propose", type=str)
    parser.add_argument(
        "--dry-run",
        help="Create branches but don't push or propose anything.",
        action="store_true", default=False)
    parser.add_argument(
        '--build-verify',
        help='Build package to verify it.', action='store_true')
    parser.add_argument(
        '--builder', type=str, default=DEFAULT_BUILDER,
        help='Build command to run.')
    parser.add_argument(
        '--build-target-dir', type=str,
        help=("Store built Debian files in specified directory "
              "(with --build-verify)"))
    parser.add_argument(
        '--commit-pending',
        help='Commit pending changes after script.',
        choices=['yes', 'no', 'auto'],
        default='auto', type=str)


def main(args):
    from breezy.plugins.propose import propose as _mod_propose
    from breezy.trace import note, show_error
    main_branch = open_packaging_branch(args.package)

    if args.name is None:
        name = derived_branch_name(args.script)
    else:
        name = args.name

    commit_pending = {'auto': None, 'yes': True, 'no': False}[
        args.commit_pending]

    overwrite = False

    try:
        hoster = get_hoster(main_branch)
    except UnsupportedHoster as e:
        if args.mode != 'push':
            raise
        # We can't figure out what branch to resume from when there's no hoster
        # that can tell us.
        resume_branch = None
        existing_proposal = None
        warning('Unsupported hoster (%s), will attempt to push to %s',
                e, main_branch.user_url)
    else:
        (resume_branch, overwrite, existing_proposal) = (
            find_existing_proposed(main_branch, hoster, name))
    if args.refresh:
        resume_branch = None
    with Workspace(main_branch, resume_branch=resume_branch) as ws:
        try:
            description = script_runner(
                ws.local_tree, args.script, commit_pending)
        except ScriptMadeNoChanges:
            show_error('Script did not make any changes.')
            return 1

        if args.build_verify:
            ws.build(builder=args.builder, result_dir=args.build_target_dir)

        def get_description(existing_proposal):
            if description is not None:
                return description
            if existing_proposal is not None:
                return existing_proposal.get_description()
            raise ValueError("No description available")

        enable_tag_pushing(ws.local_tree.branch)

        try:
            (proposal, is_new) = publish_changes(
                ws, args.mode, name,
                get_proposal_description=get_description,
                dry_run=args.dry_run, hoster=hoster,
                labels=args.label, overwrite_existing=overwrite,
                existing_proposal=existing_proposal)
        except UnsupportedHoster as e:
            show_error('No known supported hoster for %s. Run \'svp login\'?',
                       e.branch.user_url)
            return 1
        except _mod_propose.HosterLoginRequired as e:
            show_error(
                'Credentials for hosting site at %r missing. '
                'Run \'svp login\'?', e.hoster.base_url)
            return 1

        if proposal:
            if is_new:
                note('Merge proposal created.')
            else:
                note('Merge proposal updated.')
            if proposal.url:
                note('URL: %s', proposal.url)
            note('Description: %s', proposal.get_description())

        if args.diff:
            ws.show_diff(sys.stdout.buffer)
