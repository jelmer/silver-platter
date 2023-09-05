#!/usr/bin/python
# Copyright (C) 2021 Jelmer Vernooij <jelmer@jelmer.uk>
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

import logging
import subprocess
import sys
from typing import List, Optional

from breezy.workingtree import WorkingTree
from breezy.workspace import check_clean_tree, reset_tree
from . import _svp_rs


ScriptMadeNoChanges = _svp_rs.ScriptMadeNoChanges
ScriptFailed = _svp_rs.ScriptFailed
ScriptNotFound = _svp_rs.ScriptNotFound
DetailedFailure = _svp_rs.DetailedFailure
ResultFileFormatError = _svp_rs.ResultFileFormatError
CommandResult = _svp_rs.CommandResult
script_runner = _svp_rs.script_runner


def main(argv: List[str]) -> Optional[int]:  # noqa: C901
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command", help="Path to script to run.", type=str,
        nargs='?')
    parser.add_argument(
        "--diff", action="store_true", help="Show diff of generated changes."
    )
    parser.add_argument(
        "--commit-pending",
        help="Commit pending changes after script.",
        choices=["yes", "no", "auto"],
        default=None,
        type=str,
    )
    parser.add_argument(
        "--verify-command", type=str, help="Command to run to verify changes."
    )
    parser.add_argument(
        "--recipe", type=str, help="Recipe to use.")
    args = parser.parse_args(argv)

    if args.recipe:
        from .recipe import Recipe
        recipe = Recipe.from_path(args.recipe)
    else:
        recipe = None

    if args.commit_pending:
        commit_pending = {
            "auto": None, "yes": True, "no": False}[args.commit_pending]
    elif recipe:
        commit_pending = recipe.commit_pending
    else:
        commit_pending = None

    if args.command:
        command = args.command
    elif recipe.command:
        command = recipe.command
    else:
        parser.error('No command specified.')

    local_tree, subpath = WorkingTree.open_containing('.')

    check_clean_tree(local_tree)

    try:
        result = script_runner(
            local_tree, script=command, commit_pending=commit_pending,
            subpath=subpath)

        if result.description:
            logging.info('Succeeded: %s', result.description)

        if args.verify_command:
            try:
                subprocess.check_call(
                    args.verify_command, shell=True,
                    cwd=local_tree.abspath(subpath)
                )
            except subprocess.CalledProcessError:
                parser.error("Verify command failed.")
    except Exception:
        reset_tree(local_tree, subpath=subpath)
        raise

    if args.diff:
        from breezy.diff import show_diff_trees
        old_tree = local_tree.revision_tree(result.old_revision)
        new_tree = local_tree.revision_tree(result.new_revision)
        show_diff_trees(
            old_tree,
            new_tree,
            sys.stdout.buffer,
            old_label='old/',
            new_label='new/')
    return 0
