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

import logging
import os
import shutil
import sys
from contextlib import suppress
from typing import Any, Callable, Dict, List, Optional

from breezy.branch import Branch
from breezy.errors import DivergedBranches
from breezy.forge import get_proposal_by_url
from breezy.workingtree import WorkingTree
from ruamel.yaml.scalarstring import LiteralScalarString

from ..batch import (
    UnrelatedBranchExists,
    drop_batch_entry,
    load_batch_metadata,
    save_batch_metadata,
)
from ..candidates import Candidate, CandidateList
from ..proposal import (
    ForgeLoginRequired,
    MergeProposal,
    UnsupportedForge,
    enable_tag_pushing,
    get_forge,
)
from ..publish import (
    EmptyMergeProposal,
    InsufficientChangesForNewProposal,
    publish_changes,
)
from ..recipe import Recipe
from ..utils import (
    BranchMissing,
    BranchUnavailable,
    BranchUnsupported,
    full_branch_url,
    open_branch,
)
from . import Workspace
from .apply import (
    CommandResult,
    ScriptFailed,
    ScriptMadeNoChanges,
    ScriptNotFound,
    script_runner,
)


def generate_for_candidate(recipe, basepath, url, *, subpath: str = '',
                           default_mode: Optional[str] = None):
    try:
        main_branch = open_branch(url)
    except (BranchUnavailable, BranchMissing, BranchUnsupported) as e:
        logging.error("%s: %s", url, e)
        raise

    with Workspace(main_branch, path=basepath) as ws:
        logging.info('Making changes to %s', main_branch.user_url)
        entry = {'url': url}
        if subpath:
            entry['subpath'] = subpath

        try:
            result: CommandResult = script_runner(
                ws.local_tree, recipe.command, recipe.commit_pending,
                subpath=subpath)
        except ScriptMadeNoChanges:
            logging.error("Script did not make any changes.")
            return None
        except ScriptFailed:
            logging.error("Script failed to run.")
            return None
        except ScriptNotFound:
            logging.error("Script not found.")
            return None
        else:
            if result.target_branch_url:
                entry['target_branch_url'] = result.target_branch_url
            if result.description:
                entry['description'] = LiteralScalarString(result.description)
            else:
                description = recipe.render_merge_request_description(
                    'markdown', result.context)
                if description:
                    entry['description'] = LiteralScalarString(description)
            commit_message = recipe.render_merge_request_commit_message(
                result.context)
            if commit_message:
                entry['commit-message'] = commit_message
            title = recipe.render_merge_request_title(
                result.context)
            if title:
                entry['title'] = title
            if recipe.mode:
                entry['mode'] = recipe.mode or default_mode
            if recipe.labels:
                entry['labels'] = recipe.labels
            if result.context:
                entry['context'] = recipe.context
            if result.source:
                entry['source'] = recipe.source
            ws.defer_destroy()
        return entry


def publish_one(url: str, path: str, batch_name: str, mode: str,
                *,
                subpath: str = '',
                existing_proposal_url: Optional[str] = None,
                labels: Optional[List[str]] = None,
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
        existing_proposal: Optional[MergeProposal] = None
        logging.warn(
            "Unsupported forge (%s), will attempt to push to %s",
            e,
            full_branch_url(main_branch),
        )
    else:
        if existing_proposal_url is not None:
            existing_proposal = forge.get_proposal_by_url(
                existing_proposal_url)
            assert existing_proposal
            resume_branch_url = existing_proposal.get_source_branch_url()
            assert resume_branch_url is not None
            resume_branch = Branch.open(resume_branch_url)
        else:
            existing_proposal = None
            resume_branch = None
    if refresh:
        if resume_branch:
            overwrite = True
        resume_branch = None

    if existing_proposal:
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
    except DivergedBranches:
        if not resume_branch:
            raise UnrelatedBranchExists()
        logging.warning('Branch exists that has diverged')
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


def publish(directory, *, selector=None):
    batch = load_batch_metadata(directory)
    try:
        batch_name = batch['name']
    except KeyError:
        logging.error('no name found in %s', directory)
        return 1
    work = batch.get('work', [])
    if not work:
        logging.error('no work found in %s', directory)
        return 0
    errors = 0
    try:
        done = []
        for i, (name, entry) in enumerate(work.items()):
            if selector and not selector(name, entry):
                continue
            try:
                publish_result = publish_one(
                    entry['url'], os.path.join(directory, name), batch_name,
                    entry['mode'],
                    subpath=entry.get('subpath', ''),
                    labels=entry.get('labels', []),
                    derived_owner=entry.get('derived-owner'),
                    commit_message=entry.get('commit-message'),
                    title=entry.get('title'),
                    existing_proposal_url=entry.get('proposal-url'),
                    description=entry.get('description'))   # type: ignore
            except EmptyMergeProposal:
                logging.info('No changes left')
                done.append(i)
            except UnrelatedBranchExists:
                errors += 1
            else:
                if publish_result.mode == 'push':
                    drop_batch_entry(directory, name)
                    done.append(i)
                elif publish_result.proposal:
                    entry['proposal-url'] = publish_result.proposal.url
            save_batch_metadata(directory, batch)
        for i in reversed(done):
            del work[i]
    finally:
        save_batch_metadata(directory, batch)
    if not work:
        logging.info('No work left in batch.yaml; you can now remove %s',
                     directory)
    if errors:
        return 1
    return 0


def status(directory, codebase=None):
    batch = load_batch_metadata(directory)
    work = batch.get('work', [])
    if not work:
        logging.error('no work found in %s', directory)
        return 0
    for name, entry in work.items():
        if codebase is not None and name != codebase:
            continue
        if entry.get('proposal-url'):
            proposal = get_proposal_by_url(entry['proposal-url'])
            if proposal.is_merged():
                logging.info('%s: %s was merged', name,
                             entry['proposal-url'])
            elif proposal.is_closed():
                logging.info('%s: %s was closed without being merged',
                             name, entry['proposal-url'])
            else:
                logging.info('%s: %s is still open',
                             name, entry['proposal-url'])
        else:
            logging.info('%s: not published yet', name)


def diff(directory, codebase):
    batch = load_batch_metadata(directory)
    work = batch.get('work', [])
    if not work:
        logging.error('no work found in %s', directory)
        return 0
    entry = work[codebase]
    try:
        main_branch = open_branch(entry['url'])
    except (BranchUnavailable, BranchMissing, BranchUnsupported) as e:
        logging.error("%s: %s", entry['url'], e)
        raise

    basepath = os.path.join(directory, codebase)
    with Workspace(main_branch, path=basepath) as ws:
        ws.show_diff(sys.stdout.buffer)


def generate(
        recipe: Recipe, candidates: List[Candidate], directory: str,
        recipe_path: str):
    with suppress(FileExistsError):
        os.mkdir(directory)

    batch: Dict[str, Any]
    try:
        batch = load_batch_metadata(directory)
    except FileNotFoundError:
        batch = {
            'recipe': recipe_path,
            'name': recipe.name,
        }
        batch['work'] = entries = {}
    else:
        entries = batch['work']

    try:
        for candidate in candidates:
            basename = candidate.name
            if basename is None:
                # TODO(jelmer): Move this logic to Candidate?
                basename = candidate.url.rstrip('/').rsplit('/', 1)[-1]
            name = basename
            # TODO(jelmer): Search by URL rather than by name?
            if name in entries and entries[name]['url'] == candidate.url:
                logging.info(
                    'An entry %s for %s exists, skipping',
                    name, entries[name]['url'])
                continue
            i = 0
            while os.path.exists(os.path.join(directory, name)):
                i += 1
                name = basename + '.%d' % i
            work_path = os.path.join(directory, name)
            try:
                entry = generate_for_candidate(
                    recipe, work_path,
                    candidate.url,
                    subpath=candidate.subpath or '',
                    default_mode=candidate.default_mode)
            except Exception:
                if os.path.exists(work_path):
                    shutil.rmtree(work_path)
                raise
            else:
                if entry:
                    entries[name] = entry
                    save_batch_metadata(directory, batch)
    finally:
        save_batch_metadata(directory, batch)


def main(argv: List[str]) -> Optional[int]:  # noqa: C901
    import argparse
    parser = argparse.ArgumentParser("svp batch")
    subparsers = parser.add_subparsers(dest="command")
    generate_parser = subparsers.add_parser("generate")
    generate_parser.add_argument(
        "--recipe", type=str, help="Recipe to use.")
    generate_parser.add_argument(
        "--candidates", type=str, help="File with candidate list.")
    generate_parser.add_argument('directory', nargs='?')
    publish_parser = subparsers.add_parser("publish")
    publish_parser.add_argument('directory')
    publish_parser.add_argument('name', nargs='?')
    status_parser = subparsers.add_parser("status")
    status_parser.add_argument('directory')
    status_parser.add_argument('codebase', nargs='?', default=None)
    diff_parser = subparsers.add_parser("diff")
    diff_parser.add_argument('directory')
    diff_parser.add_argument('codebase')
    args = parser.parse_args(argv)
    if args.command == "generate":
        if args.recipe:
            recipe = Recipe.from_path(args.recipe)
        else:
            parser.error('no recipe specified')
        if args.candidates:
            candidates = CandidateList.from_path(args.candidates)
        else:
            parser.error('no candidate list specified')
        if args.directory is None:
            args.directory = recipe.name
            logging.info('Using output directory: %s', args.directory)
        generate(
            recipe, candidates, args.directory,
            recipe_path=os.path.relpath(args.recipe, args.directory))
        logging.info(
            'Now, review the patches under %s, edit %s/batch.yaml as '
            'appropriate and then run "svp batch publish %s"',
            args.directory, args.directory, args.directory)
    elif args.command == 'publish':
        selector: Optional[Callable]
        if args.name:
            def selector(n, e):
                return n == args.name
        else:
            selector = None
        publish(args.directory, selector=selector)
        logging.info(
            'To see the status of open merge requests, run: '
            '"svp batch status %s"', args.directory)
    elif args.command == 'status':
        status(args.directory, args.codebase)
    elif args.command == 'diff':
        diff(args.directory, args.codebase)
    else:
        parser.print_usage()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
