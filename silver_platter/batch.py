#!/usr/bin/python
# Copyright (C) 2022 Jelmer Vernooij <jelmer@jelmer.uk>
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

from contextlib import suppress
import logging
import os
import shutil
from typing import List, Optional

import ruamel.yaml

from breezy.forge import get_proposal_by_url
from breezy.workingtree import WorkingTree

from .apply import (
    script_runner,
    ScriptMadeNoChanges,
    ScriptFailed,
    ScriptNotFound,
)
from .utils import (
    open_branch,
    BranchMissing,
    BranchUnsupported,
    BranchUnavailable,
    full_branch_url,
)
from .publish import (
    InsufficientChangesForNewProposal,
    publish_changes,
    EmptyMergeProposal,
)
from .proposal import (
    ForgeLoginRequired,
    MergeProposal,
    UnsupportedForge,
    enable_tag_pushing,
    find_existing_proposed,
    get_forge,
)

from .workspace import (
    Workspace,
)


def generate_for_candidate(recipe, basepath, url, name: str,
                           *, subpath: str = ''):
    try:
        main_branch = open_branch(url)
    except (BranchUnavailable, BranchMissing, BranchUnsupported) as e:
        logging.error("%s: %s", url, e)
        raise

    with Workspace(main_branch, path=basepath) as ws:
        logging.info('Making changes to %s', main_branch.user_url)
        entry = {'url': url, 'name': name}
        if subpath:
            entry['subpath'] = subpath

        try:
            result = script_runner(
                ws.local_tree, recipe.command, recipe.commit_pending,
                subpath=subpath)
        except ScriptMadeNoChanges:
            logging.error("Script did not make any changes.")
        except ScriptFailed:
            logging.error("Script failed to run.")
        except ScriptNotFound:
            logging.error("Script not found.")
        else:
            patchpath = basepath + '.patch'
            with open(patchpath, 'wb') as f:
                ws.show_diff(f)
            if result.target_branch_url:
                entry['target_branch_url'] = result.target_branch_url
            if result.description:
                entry['description'] = result.description
            else:
                description = recipe.render_merge_request_description(
                    'markdown', result.context)
                if description:
                    entry['description'] = description
            commit_message = recipe.render_merge_request_commit_message(
                result.context)
            if commit_message:
                entry['commit-message'] = commit_message
            title = recipe.render_merge_request_title(
                result.context)
            if title:
                entry['title'] = title
            if recipe.mode:
                entry['mode'] = recipe.mode
            if recipe.labels:
                entry['labels'] = recipe.labels
            if result.context:
                entry['context'] = recipe.context
            ws.defer_destroy()
        return entry


def generate(recipe, candidates, directory, recipe_path):
    with suppress(FileExistsError):
        os.mkdir(directory)
    entries = []
    for candidate in candidates:
        name = candidate.name
        if name is None:
            name = candidate.url.rstrip('/').rsplit('/', 1)[-1]
        basename = os.path.join(directory, name)
        entry = generate_for_candidate(
            recipe, basename, candidate.url, name,
            subpath=candidate.subpath or '')
        entries.append(entry)
    batch = {'work': entries, 'recipe': recipe_path,
             name: recipe.name}
    with open(os.path.join(directory, 'batch.yaml'), 'w') as f:
        ruamel.yaml.round_trip_dump(batch, f)


def load_batch(directory):
    with open(os.path.join(directory, 'batch.yaml'), 'r') as f:
        return ruamel.yaml.round_trip_load(f)


def status(directory):
    batch = load_batch(directory)
    work = batch.get('work', [])
    if not work:
        logging.error('no work found in %s', directory)
        return 0
    for entry in work:
        if entry.get('proposal-url'):
            proposal = get_proposal_by_url(entry['proposal-url'])
            if proposal.is_merged():
                logging.info('%s: %s was merged', entry['name'],
                             entry['proposal-url'])
            elif proposal.is_closed():
                logging.info('%s: %s was closed without being merged',
                             entry['name'], entry['proposal-url'])
            else:
                logging.info('%s: %s is still open',
                             entry['name'], entry['proposal-url'])
        else:
            logging.info('%s: not published yet', entry['name'])


def publish_one(url: str, path: str, batch_name: str, mode: str,
                patchpath: str, *,
                subpath: str = '',
                labels: Optional[List[str]] = None, dry_run: bool = False,
                derived_owner: Optional[str] = None, refresh: bool = False,
                commit_message: Optional[str] = None,
                title: Optional[str] = None,
                description: Optional[str] = None, overwrite: bool = False):
    try:
        main_branch = open_branch(url)
    except (BranchUnavailable, BranchMissing, BranchUnsupported) as e:
        logging.error("%s: %s", url, e)
        raise

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
            main_branch, forge, batch_name, owner=derived_owner
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

    local_tree = WorkingTree.open(path)

    enable_tag_pushing(local_tree.branch)

    try:
        publish_result = publish_changes(
            local_tree.branch,
            main_branch,
            resume_branch,
            mode,
            batch_name,
            get_proposal_description=(
                lambda df, ep: description),  # type: ignore
            get_proposal_commit_message=lambda ep: commit_message,
            get_proposal_title=lambda ep: title,
            allow_create_proposal=True,
            dry_run=dry_run,
            forge=forge,
            labels=labels,
            overwrite_existing=overwrite,
            derived_owner=derived_owner,
            existing_proposal=existing_proposal,
        )
    except UnsupportedForge as e:
        logging.exception(
            "No known supported forge for %s. Run 'svp login'?",
            full_branch_url(e.branch),
        )
        raise
    except InsufficientChangesForNewProposal:
        logging.info('Insufficient changes for a new merge proposal')
        raise
    except ForgeLoginRequired as e:
        logging.exception(
            "Credentials for hosting site at %r missing. "
            "Run 'svp login'?",
            e.forge.base_url,
        )
        raise

    if publish_result.proposal:
        if publish_result.is_new:
            logging.info("Merge proposal created.")
        else:
            logging.info("Merge proposal updated.")
        if publish_result.proposal.url:
            logging.info("URL: %s", publish_result.proposal.url)
        logging.info(
            "Description: %s", publish_result.proposal.get_description())
    return publish_result


def publish(directory, *, dry_run: bool = False):
    batch = load_batch(directory)
    batch_name = batch['name']
    work = batch.get('work', [])
    if not work:
        logging.error('no work found in %s', directory)
        return 0
    done = []
    for i, entry in enumerate(work):
        name = entry['name']
        try:
            publish_result = publish_one(
                entry['url'], os.path.join(directory, name), batch_name,
                entry['mode'], entry['patch'],
                subpath=entry.get('subpath', ''),
                labels=entry.get('labels', []),
                dry_run=dry_run, derived_owner=entry.get('derived-owner'),
                commit_message=entry.get('commit-message'),
                title=entry.get('title'),
                description=entry.get('description'))
        except EmptyMergeProposal:
            logging.info('No changes left')
            done.append(i)
        else:
            if publish_result.mode == 'push':
                if not dry_run:
                    with suppress(FileNotFoundError):
                        os.unlink(os.path.join(directory, name + '.patch'))
                        shutil.rmtree(os.path.join(directory, name))
                done.append(i)
            elif publish_result.proposal:
                entry['proposal-url'] = publish_result.proposal.url
    for i in reversed(done):
        del work[i]
    if not dry_run:
        with open(os.path.join(directory, 'batch.yaml'), 'w') as f:
            ruamel.yaml.round_trip_dump(batch, f)
    if not work:
        logging.info('No work left in batch.yaml; you can now remove %s',
                     directory)


def main(argv: List[str]) -> Optional[int]:  # noqa: C901
    import argparse
    parser = argparse.ArgumentParser("svp batch")
    subparsers = parser.add_subparsers(dest="command")
    generate_parser = subparsers.add_parser("generate")
    generate_parser.add_argument(
        "--recipe", type=str, help="Recipe to use.")
    generate_parser.add_argument(
        "--candidates", type=str, help="File with candidate list.")
    generate_parser.add_argument('directory')
    publish_parser = subparsers.add_parser("publish")
    publish_parser.add_argument('directory')
    publish_parser.add_argument('--dry-run', action='store_true')
    status_parser = subparsers.add_parser("status")
    status_parser.add_argument('directory')
    args = parser.parse_args(argv)
    if args.command == "generate":
        if args.recipe:
            from .recipe import Recipe
            recipe = Recipe.from_path(args.recipe)
        else:
            parser.error('no recipe specified')
        if args.candidates:
            from .candidates import CandidateList
            candidates = CandidateList.from_path(args.candidates)
        else:
            parser.error('no candidate list specified')
        generate(recipe, candidates, args.directory,
                 recipe_path=os.path.relpath(args.recipe, args.directory))
    elif args.command == 'publish':
        publish(args.directory, dry_run=args.dry_run)
    elif args.command == 'status':
        status(args.directory)
    else:
        parser.print_usage()
    return 0
