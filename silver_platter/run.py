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

"""Automatic proposal/push creation."""

import argparse
import logging
import subprocess
import sys
from typing import List, Optional

import silver_platter  # noqa: F401

from .apply import (
    ScriptFailed,
    ScriptMadeNoChanges,
    ScriptNotFound,
    script_runner,
)
from .proposal import (
    ForgeLoginRequired,
    MergeProposal,
    UnsupportedForge,
    enable_tag_pushing,
    find_existing_proposed,
    get_forge,
)
from .publish import SUPPORTED_MODES, InsufficientChangesForNewProposal
from .utils import (
    BranchMissing,
    BranchUnavailable,
    BranchUnsupported,
    full_branch_url,
    open_branch,
)
from .workspace import Workspace

from ._svp_rs import derived_branch_name


def apply_and_publish(  # noqa: C901
        url: str, name: str, command: str, mode: str,
        commit_pending: Optional[bool] = None,
        labels: Optional[List[str]] = None, diff: bool = False,
        verify_command: Optional[str] = None,
        derived_owner: Optional[str] = None,
        refresh: bool = False, allow_create_proposal=None,
        get_commit_message=None, get_title=None, get_description=None):
    try:
        main_branch = open_branch(url)
    except (BranchUnavailable, BranchMissing, BranchUnsupported) as e:
        logging.exception("%s: %s", url, e)
        return 2

    overwrite = False

    try:
        forge = get_forge(main_branch)
    except UnsupportedForge as e:
        if mode != "push":
            raise
        # We can't figure out what branch to resume from when there's no forge
        # that can tell us.
        resume_branch = None
        existing_proposals: Optional[List[MergeProposal]] = []
        logging.warn(
            "Unsupported forge (%s), will attempt to push to %s",
            e,
            full_branch_url(main_branch),
        )
    else:
        (resume_branch, resume_overwrite,
         existing_proposals) = find_existing_proposed(
            main_branch, forge, name, owner=derived_owner
        )
        if resume_overwrite is not None:
            overwrite = resume_overwrite
    if refresh:
        if resume_branch:
            overwrite = True
        resume_branch = None

    if existing_proposals and len(existing_proposals) > 1:
        logging.warning(
            'Multiple open merge proposals for branch at %s: %r',
            resume_branch.user_url,  # type: ignore
            [mp.url for mp in existing_proposals])
        existing_proposal = existing_proposals[0]
        logging.info('Updating %s', existing_proposal.url)
    else:
        existing_proposal = None

    with Workspace(main_branch, resume_branch=resume_branch) as ws:
        try:
            result = script_runner(ws.local_tree, command, commit_pending)
        except ScriptMadeNoChanges:
            logging.error("Script did not make any changes.")
            return 0
        except ScriptFailed:
            logging.error("Script failed to run.")
            return 2
        except ScriptNotFound:
            logging.error("Script could not be found.")
            return 2

        if verify_command:
            try:
                subprocess.check_call(
                    verify_command, shell=True, cwd=ws.local_tree.abspath(".")
                )
            except subprocess.CalledProcessError:
                logging.error("Verify command failed.")
                return 2

        enable_tag_pushing(ws.local_tree.branch)

        try:
            publish_result = ws.publish_changes(
                mode=mode,
                name=name,
                get_proposal_description=(
                    lambda df, ep: get_description(result, df, ep)),
                get_proposal_commit_message=(
                    lambda ep: get_commit_message(result, ep)),
                get_proposal_title=(
                    lambda ep: get_title(result, ep)),
                allow_create_proposal=(
                    lambda: allow_create_proposal(result)),
                forge=forge,
                labels=labels,
                overwrite_existing=overwrite,
                derived_owner=derived_owner,
                existing_proposal=existing_proposal,
            )
        except UnsupportedForge as e:
            logging.error(
                "No known supported forge for %s. Run 'svp login'?",
                full_branch_url(e.branch),
            )
            return 2
        except InsufficientChangesForNewProposal:
            logging.info('Insufficient changes for a new merge proposal')
            return 1
        except ForgeLoginRequired as e:
            logging.error(
                "Credentials for hosting site at %r missing. "
                "Run 'svp login'?",
                e.forge.base_url,
            )
            return 2

        if publish_result.proposal:
            if publish_result.is_new:
                logging.info("Merge proposal created.")
            else:
                logging.info("Merge proposal updated.")
            if publish_result.proposal.url:
                logging.info("URL: %s", publish_result.proposal.url)
            logging.info(
                "Description: %s", publish_result.proposal.get_description())

        if diff:
            ws.show_diff(sys.stdout.buffer)

        return 1


def main(argv: List[str]) -> Optional[int]:  # noqa: C901
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "url", help="URL of branch to work on.", type=str, nargs="?")
    parser.add_argument(
        "--command", help="Path to script to run.", type=str)
    parser.add_argument(
        "--derived-owner", type=str, default=None,
        help="Owner for derived branches."
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Refresh changes if branch already exists",
    )
    parser.add_argument(
        "--label", type=str, help="Label to attach", action="append",
        default=[]
    )
    parser.add_argument(
        "--name", type=str, help="Proposed branch name", default=None)
    parser.add_argument(
        "--diff", action="store_true", help="Show diff of generated changes."
    )
    parser.add_argument(
        "--mode",
        help="Mode for pushing",
        choices=SUPPORTED_MODES,
        default=None,
        type=str,
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
    parser.add_argument(
        "--candidates", type=str, help="File with candidate list.")
    args = parser.parse_args(argv)

    if args.recipe:
        from .recipe import Recipe
        recipe = Recipe.from_path(args.recipe)
    else:
        recipe = None

    if not args.url and not args.candidates:
        parser.error("url or candidates are required")

    urls = []

    if args.url:
        urls = [args.url]

    if args.candidates:
        from .candidates import CandidateList
        candidatelist = CandidateList.from_path(args.candidates)
        urls.extend([candidate.url for candidate in candidatelist])

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
        logging.exception('No command specified.')
        return 1

    if args.name is not None:
        name = args.name
    elif recipe and recipe.name:
        name = recipe.name
    else:
        name = derived_branch_name(command)

    if args.mode:
        mode = args.mode
    elif recipe and recipe.mode:
        mode = recipe.mode
    else:
        mode = "recipe"

    refresh = args.refresh

    if recipe and not recipe.resume:
        refresh = True

    def allow_create_proposal(result):
        if result.value is None:
            return True
        if recipe.propose_threshold is not None:
            return result.value >= recipe.propose_threshold
        return True

    def get_commit_message(result, existing_proposal):
        if recipe:
            return recipe.render_merge_request_commit_message(result.context)
        if existing_proposal is not None:
            return existing_proposal.get_commit_message()
        return None

    def get_title(result, existing_proposal):
        if recipe:
            return recipe.render_merge_request_title(result.context)
        if existing_proposal is not None:
            return existing_proposal.get_title()
        return None

    def get_description(result, description_format, existing_proposal):
        if recipe:
            description = recipe.render_merge_request_description(
                description_format, result.context)
            if description:
                return description
        if result.description is not None:
            return result.description
        if existing_proposal is not None:
            return existing_proposal.get_description()
        raise ValueError("No description available")

    retcode = 0

    for url in urls:
        result = apply_and_publish(
                url, name=name, command=command, mode=mode,
                commit_pending=commit_pending,
                labels=args.label, diff=args.diff,
                derived_owner=args.derived_owner, refresh=refresh,
                allow_create_proposal=allow_create_proposal,
                get_commit_message=get_commit_message,
                get_title=get_title,
                get_description=get_description)
        retcode = max(retcode, result)

    return retcode


if __name__ == "__main__":
    sys.exit(main(sys.argv))
