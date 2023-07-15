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

import argparse
import logging
import os
import sys
from typing import List, Optional

from breezy import osutils
from breezy.urlutils import InvalidURL

import silver_platter  # noqa: F401

from ..candidates import Candidate, CandidateList
from ..proposal import (
    ForgeLoginRequired,
    MergeProposal,
    UnsupportedForge,
    enable_tag_pushing,
    find_existing_proposed,
    get_forge,
)
from ..publish import SUPPORTED_MODES, InsufficientChangesForNewProposal
from ..utils import (
    BranchMissing,
    BranchUnavailable,
    BranchUnsupported,
    full_branch_url,
    open_branch,
)
from . import (
    DEFAULT_BUILDER,
    BuildFailedError,
    MissingUpstreamTarball,
    Workspace,
    build,
)
from .apply import (
    MissingChangelog,
    ScriptFailed,
    ScriptMadeNoChanges,
    ScriptNotFound,
    install_built_package,
    script_runner,
)


def derived_branch_name(script: str) -> str:
    return os.path.splitext(osutils.basename(script.split(" ")[0]))[0]


def apply_and_publish(  # noqa: C901
        url: str, name: str, command: str, mode: str,
        subpath: str = '',
        commit_pending: Optional[bool] = None,
        labels: Optional[List[str]] = None, diff: bool = False,
        verify_command: Optional[str] = None,
        derived_owner: Optional[str] = None,
        refresh: bool = False, allow_create_proposal=None,
        get_commit_message=None, get_title=None, get_description=None,
        build_verify=False, builder=DEFAULT_BUILDER, install=False,
        build_target_dir=None, update_changelog: Optional[bool] = None,
        preserve_repositories: bool = False):
    try:
        main_branch = open_branch(url)
    except (BranchUnavailable, BranchMissing, BranchUnsupported) as e:
        logging.fatal("%s: %s", url, e)
        return 1
    except InvalidURL as e:
        logging.fatal('%s: %s', url, e)
        return 1

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
    except ForgeLoginRequired as e:
        logging.error(
            '%s: Forge login required: %s', full_branch_url(main_branch), e)
        return 1
    else:
        (resume_branch, resume_overwrite,
         existing_proposals) = find_existing_proposed(
             main_branch, forge, name, owner=derived_owner)
        if resume_overwrite is not None:
            overwrite = resume_overwrite
    if refresh:
        resume_branch = None

    if existing_proposals and len(existing_proposals) > 1:
        logging.warning(
            'Multiple open merge proposals for branch at %s: %r',
            resume_branch.user_url,  # type: ignore
            [mp.url for mp in existing_proposals])
        existing_proposal = existing_proposals[0]
        logging.info('Updating just %s', existing_proposal.url)
    else:
        existing_proposal = None

    with Workspace(main_branch, resume_branch=resume_branch) as ws:
        try:
            result = script_runner(
                ws.local_tree, command, commit_pending,
                update_changelog=update_changelog)
        except MissingChangelog as e:
            logging.error("No debian changelog (%s) present", e.args[0])
            return 1
        except ScriptMadeNoChanges:
            logging.error("Script did not make any changes.")
            return 1
        except ScriptFailed:
            logging.error("Script failed to run.")
            return 1
        except ScriptNotFound:
            logging.error("Script could not be found.")
            return 1

        if build_verify or install:
            try:
                build(ws.local_tree, subpath, builder=builder,
                      result_dir=build_target_dir)
            except BuildFailedError:
                logging.info("%s: build failed", result.source)
                return False
            except MissingUpstreamTarball:
                logging.info(
                    "%s: unable to find upstream source", result.source)
                return False

        if install:
            install_built_package(ws.local_tree, subpath, build_target_dir)

        enable_tag_pushing(ws.local_tree.branch)

        try:
            publish_result = ws.publish_changes(
                mode,
                name,
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
                existing_proposal=(
                    existing_proposals[0] if existing_proposals else None),
            )
        except UnsupportedForge as e:
            logging.error(
                "No known supported forge for %s. Run 'svp login'?",
                full_branch_url(e.branch),
            )
            return 1
        except InsufficientChangesForNewProposal:
            logging.info('Insufficient changes for a new merge proposal')
            return 0
        except ForgeLoginRequired as e:
            logging.error(
                "Credentials for hosting site at %r missing. "
                "Run 'svp login'?",
                e.forge.base_url,
            )
            return 1

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

        if preserve_repositories:
            ws.defer_destroy()
            logging.info(
                'Workspace preserved in %s', ws.local_tree.abspath(ws.subpath))


def main(argv: List[str]) -> Optional[int]:  # noqa: C901
    parser = argparse.ArgumentParser()
    parser.add_argument("url", help="URL of branch to work on.", type=str)
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
        "--label", type=str, help="Label to attach",
        action="append", default=[]
    )
    parser.add_argument(
        "--preserve-repositories", action="store_true",
        help="Preserve temporary repositories.")

    parser.add_argument(
        "--name", type=str, help="Proposed branch name", default=None)
    parser.add_argument(
        "--diff", action="store_true", help="Show diff of generated changes."
    )
    parser.add_argument(
        "--mode",
        help="Mode for pushing",
        choices=SUPPORTED_MODES,
        default="propose",
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
        "--install", "-i",
        action="store_true",
        help="Install built package (implies --build-verify)")

    parser.add_argument(
        "--recipe", type=str, help="Recipe to use.")
    parser.add_argument(
        "--candidates", type=str, help="File with candidate list.")
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

    args = parser.parse_args(argv)

    if args.recipe:
        from ..recipe import Recipe
        recipe = Recipe.from_path(args.recipe)
    else:
        recipe = None

    candidates = []

    if args.url:
        candidates = [Candidate(url=args.url)]

    if args.candidates:
        candidatelist = CandidateList.from_path(args.candidates)
        candidates.extend(candidatelist)

    if args.commit_pending:
        commit_pending = {
            "auto": None, "yes": True, "no": False}[args.commit_pending]
    elif recipe:
        commit_pending = recipe.commit_pending
    else:
        commit_pending = None

    if args.command:
        command = args.command
    elif recipe and recipe.command:
        command = recipe.command
    else:
        parser.error('No command specified.')

    if args.name is not None:
        name = args.name
    elif recipe and recipe.name:
        name = recipe.name
    else:
        name = derived_branch_name(command)

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

    for candidate in candidates:
        if apply_and_publish(
                candidate.url, name=name, command=command, mode=args.mode,
                subpath=candidate.subpath,
                commit_pending=commit_pending,
                labels=args.label, diff=args.diff,
                derived_owner=args.derived_owner, refresh=refresh,
                allow_create_proposal=allow_create_proposal,
                get_commit_message=get_commit_message,
                get_title=get_title,
                get_description=get_description,
                build_verify=args.build_verify, builder=args.builder,
                install=args.install, build_target_dir=args.build_target_dir,
                update_changelog=args.update_changelog,
                preserve_repositories=args.preserve_repositories):
            retcode = 1

    return retcode


if __name__ == "__main__":
    sys.exit(main(sys.argv))
