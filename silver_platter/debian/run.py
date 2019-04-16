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
        help='Mode for pushing', choices=['push', 'attempt-push', 'propose'],
        default="propose", type=str)
    parser.add_argument(
        "--dry-run",
        help="Create branches but don't push or propose anything.",
        action="store_true", default=False)


def run_main(args):
    import os
    from breezy import osutils
    from breezy.plugins.propose import propose as _mod_propose
    from breezy.trace import note, show_error
    from ..proposal import (
        propose_or_push,
        )
    from ..run import (
        ScriptBranchChanger,
        ScriptMadeNoChanges,
        )
    from . import (
        open_packaging_branch,
        )
    main_branch = open_packaging_branch(args.package)
    if args.name is None:
        name = os.path.splitext(osutils.basename(args.script.split(' ')[0]))[0]
    else:
        name = args.name

    # TODO(jelmer): Check that ScriptBranchChanger updates upstream version if
    # it touches anything outside of debian/.

    try:
        result = propose_or_push(
                main_branch, name, ScriptBranchChanger(args.script),
                refresh=args.refresh, labels=args.label,
                dry_run=args.dry_run, mode=args.mode)
    except _mod_propose.UnsupportedHoster as e:
        show_error('No known supported hoster for %s. Run \'svp login\'?',
                   e.branch.user_url)
        return 1
    except _mod_propose.HosterLoginRequired as e:
        show_error(
            'Credentials for hosting site at %r missing. Run \'svp login\'?',
            e.hoster.base_url)
        return 1
    except ScriptMadeNoChanges:
        show_error('Script did not make any changes.')
        return 1

    if result.merge_proposal:
        if result.is_new:
            note('Merge proposal created.')
        else:
            note('Merge proposal updated.')
        if result.merge_proposal.url:
            note('URL: %s', result.merge_proposal.url)
        note('Description: %s', result.merge_proposal.get_description())

    if args.diff:
        result.show_base_diff(sys.stdout.buffer)
