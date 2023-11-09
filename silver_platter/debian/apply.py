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

import json
import logging
import sys
from typing import List, Optional

from breezy.workingtree import WorkingTree
from breezy.workspace import check_clean_tree, reset_tree

from .._svp_rs import (
    DebianCommandResult as CommandResult,
)
from .._svp_rs import (
    ScriptFailed,
    ScriptMadeNoChanges,
    ScriptNotFound,
    install_built_package,
)
from .._svp_rs import (
    debian_script_runner as script_runner,
)

__all__ = [
    "CommandResult",
    "ScriptFailed",
    "ScriptNotFound",
    "script_runner",
]


from . import (
    DEFAULT_BUILDER,
    BuildFailedError,
    MissingChangelog,
    MissingUpstreamTarball,
    build,
)


def main(argv: List[str]) -> Optional[int]:  # noqa: C901
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--command", help="Path to script to run.", type=str)
    parser.add_argument(
        "--diff", action="store_true", help="Show diff of generated changes."
    )
    parser.add_argument(
        "--no-update-changelog",
        action="store_false",
        default=None,
        dest="update_changelog",
        help="do not update the changelog",
    )
    parser.add_argument(
        "--update-changelog",
        action="store_true",
        dest="update_changelog",
        help="force updating of the changelog",
        default=None,
    )

    parser.add_argument(
        "--commit-pending",
        help="Commit pending changes after script.",
        choices=["yes", "no", "auto"],
        default=None,
        type=str,
    )
    parser.add_argument(
        "--build-verify",
        help="Build package to verify it.",
        dest="build_verify",
        action="store_true",
    )
    parser.add_argument(
        "--builder",
        default=DEFAULT_BUILDER,
        type=str,
        help="Build command to use when verifying build.",
    )
    parser.add_argument(
        "--build-target-dir",
        type=str,
        help=(
            "Store built Debian files in specified directory "
            "(with --build-verify)"
        ),
    )
    parser.add_argument(
        "--install",
        "-i",
        action="store_true",
        help="Install built package (implies --build-verify)",
    )
    parser.add_argument(
        "--dump-context", action="store_true", help="Report context on success"
    )

    parser.add_argument("--recipe", type=str, help="Recipe to use.")
    args = parser.parse_args(argv)

    if args.recipe:
        from ..recipe import Recipe

        recipe = Recipe.from_path(args.recipe)
    else:
        recipe = None

    if args.commit_pending:
        commit_pending = {"auto": None, "yes": True, "no": False}[
            args.commit_pending
        ]
    elif recipe:
        commit_pending = recipe.commit_pending
    else:
        commit_pending = None

    if args.command:
        command = args.command
    elif recipe and recipe.command:
        command = recipe.command
    else:
        parser.error("No command or recipe specified.")

    local_tree, subpath = WorkingTree.open_containing(".")

    check_clean_tree(local_tree)

    try:
        try:
            result = script_runner(
                local_tree,
                script=command,
                commit_pending=commit_pending,
                subpath=subpath,
                update_changelog=args.update_changelog,
            )
        except MissingChangelog as e:
            logging.error("No debian changelog file (%s) present", e.args[0])
            return False
        except ScriptMadeNoChanges:
            logging.info("Script made no changes")
            return False

        if result.description:
            logging.info("Succeeded: %s", result.description)

        if args.build_verify or args.install:
            try:
                build(
                    local_tree,
                    subpath,
                    builder=args.builder,
                    result_dir=args.build_target_dir,
                )
            except BuildFailedError:
                logging.error("%s: build failed", result.source)
                return False
            except MissingUpstreamTarball:
                logging.error(
                    "%s: unable to find upstream source", result.source
                )
                return False
    except Exception:
        reset_tree(local_tree, subpath=subpath)
        raise

    if args.install:
        install_built_package(local_tree, subpath, args.build_target_dir)

    if args.diff:
        from breezy.diff import show_diff_trees

        old_tree = local_tree.revision_tree(result.old_revision)
        new_tree = local_tree.revision_tree(result.new_revision)
        show_diff_trees(
            old_tree,
            new_tree,
            sys.stdout.buffer,
            old_label="old/",
            new_label="new/",
        )

    if args.dump_context:
        json.dump(result.context, sys.stdout, indent=5)
    return 0
