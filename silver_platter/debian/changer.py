#!/usr/bin/python
# Copyright (C) 2019 Jelmer Vernooij <jelmer@jelmer.uk>
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

import argparse
from functools import partial
import itertools
import sys
from typing import Any, List, Optional, Dict

from breezy import version_info as breezy_version_info
from breezy.branch import Branch
from breezy.propose import Hoster, MergeProposal
from breezy.trace import note, warning, show_error
from breezy.workingtree import WorkingTree

from . import (
    open_packaging_branch,
    guess_update_changelog,
    NoSuchPackage,
    DEFAULT_BUILDER,
    )
from ..proposal import (
    HosterLoginRequired,
    UnsupportedHoster,
    NoSuchProject,
    SUPPORTED_MODES,
    enable_tag_pushing,
    find_existing_proposed,
    get_hoster,
    iter_conflicted,
    publish_changes,
    PublishResult,
    )
from ..utils import (
    BranchMissing,
    BranchUnavailable,
    BranchUnsupported,
    run_pre_check,
    run_post_check,
    PostCheckFailed,
    )


def get_package(package, branch_name, overwrite_unrelated=False,
                refresh=False, possible_transports=None,
                possible_hosters=None):
    main_branch, subpath = open_packaging_branch(
        package, possible_transports=possible_transports)

    overwrite = False

    try:
        hoster = get_hoster(main_branch, possible_hosters=possible_hosters)
    except UnsupportedHoster:
        # We can't figure out what branch to resume from when there's no
        # hoster that can tell us.
        resume_branch = None
        existing_proposal = None
        hoster = None
    else:
        (resume_branch, overwrite, existing_proposal) = (
            find_existing_proposed(
                main_branch, hoster, branch_name,
                overwrite_unrelated=overwrite_unrelated))
    if refresh:
        overwrite = True
        resume_branch = None

    return (
        package, main_branch, subpath, resume_branch, hoster,
        existing_proposal, overwrite)


def iter_packages(packages, branch_name, overwrite_unrelated=False,
                  refresh=False):
    """Iterate over relevant branches for a set of packages.

    Args:
      packages: Iterable over package names (or packaging URLs)
      branch_name: Branch name to look for
      overwrite_unrelated: Allow overwriting unrelated changes
      refresh: Whether to refresh existing merge proposals
    Returns:
      iterator over
        (package name, main branch object, subpath, branch to resume (if any),
         hoster (None if the hoster is not supported),
         existing_proposal, whether to overwrite the branch)
    """
    possible_transports = []
    possible_hosters = []

    for pkg in packages:
        note('Processing: %s', pkg)

        (pkg, main_branch, subpath, resume_branch, hoster, existing_proposal,
         overwrite) = get_package(
                pkg, branch_name, overwrite_unrelated=overwrite_unrelated,
                refresh=refresh, possible_transports=possible_transports,
                possible_hosters=possible_hosters)

        yield (pkg, main_branch, subpath, resume_branch, hoster,
               existing_proposal, overwrite)


class ChangerError(Exception):

    def __init__(self, summary: str, original: Optional[Exception]):
        self.summary = summary
        self.original = original


class ChangerFailure(Exception):

    def __init__(self, code, description):
        self.code = code
        self.description = description


class ChangerResult(object):

    def __init__(self, description, mutator, auxiliary_branches=[], tags=[]):
        self.description = description
        self.mutator = mutator
        self.auxiliary_branches = auxiliary_branches
        self.tags = tags


def setup_multi_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("packages", nargs='*')
    parser.add_argument(
        '--fix-conflicted', action='store_true',
        help='Fix existing merge proposals that are conflicted.')
    setup_parser_common(parser)


def setup_single_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("package")
    setup_parser_common(parser)


def setup_parser_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--dry-run",
        help="Create branches but don't push or propose anything.",
        action="store_true", default=False)
    parser.add_argument(
        '--build-verify',
        help='Build package to verify it.',
        dest='build_verify',
        action='store_true')
    parser.add_argument(
        '--pre-check',
        help='Command to run to check whether to process package.',
        type=str)
    parser.add_argument(
        '--post-check',
        help='Command to run to check package before pushing.',
        type=str)
    parser.add_argument(
        '--builder', default=DEFAULT_BUILDER, type=str,
        help='Build command to use when verifying build.')
    parser.add_argument(
        '--refresh',
        help='Discard old branch and apply fixers from scratch.',
        action='store_true')
    parser.add_argument(
        '--committer',
        help='Committer identity',
        type=str)
    parser.add_argument(
        '--mode',
        help='Mode for pushing', choices=SUPPORTED_MODES,
        default="propose", type=str)
    parser.add_argument(
        '--no-update-changelog', action="store_false", default=None,
        dest="update_changelog", help="do not update the changelog")
    parser.add_argument(
        '--update-changelog', action="store_true", dest="update_changelog",
        help="force updating of the changelog", default=None)
    parser.add_argument(
        '--diff', action="store_true",
        help="Output diff of created merge proposal.")
    parser.add_argument(
        '--build-target-dir', type=str,
        help=("Store built Debian files in specified directory "
              "(with --build-verify)"))
    parser.add_argument(
        '--overwrite', action='store_true',
        help='Overwrite existing branches.')
    parser.add_argument(
        '--name', type=str,
        help='Proposed branch name', default=None)
    parser.add_argument(
        '--label', type=str,
        help='Label to attach', action="append", default=[])


class DebianChanger(object):
    """A class which can make and explain changes to a Debian package in VCS.
    """

    name: str

    @classmethod
    def setup_parser(cls, parser: argparse.ArgumentParser) -> None:
        raise NotImplementedError(cls.setup_parser)

    @classmethod
    def from_args(cls, args: List[str]) -> 'DebianChanger':
        raise NotImplementedError(cls.from_args)

    def suggest_branch_name(self) -> str:
        raise NotImplementedError(self.suggest_branch_name)

    def make_changes(self, local_tree: WorkingTree,
                     subpath: str,
                     update_changelog: bool,
                     committer: Optional[str]) -> ChangerResult:
        raise NotImplementedError(self.make_changes)

    def get_proposal_description(
            self, applied: Any,
            description_format: str,
            existing_proposal: MergeProposal) -> str:
        raise NotImplementedError(self.get_proposal_description)

    def get_commit_message(self,
                           applied: Any,
                           existing_proposal: MergeProposal) -> str:
        raise NotImplementedError(self.get_commit_message)

    def allow_create_proposal(self, applied: Any) -> bool:
        raise NotImplementedError(self.allow_create_proposal)

    def describe(self, applied: Any, publish_result: PublishResult) -> None:
        raise NotImplementedError(self.describe)

    def value(self, applied: Any) -> Optional[int]:
        """Return indicator of value of changes."""
        return None


def _run_single_changer(
        changer: DebianChanger,
        pkg: str,
        main_branch: Branch,
        subpath: str,
        resume_branch: Optional[Branch],
        hoster: Optional[Hoster],
        existing_proposal: Optional[MergeProposal],
        overwrite: bool, mode: str, branch_name: str, diff: bool = False,
        committer: Optional[str] = None, build_verify: bool = False,
        pre_check: Optional[str] = None, post_check: Optional[str] = None,
        builder: str = DEFAULT_BUILDER, dry_run: bool = False,
        update_changelog: Optional[bool] = None,
        label: Optional[List[str]] = None,
        build_target_dir: Optional[str] = None) -> Optional[bool]:
    from breezy import errors
    from . import (
        BuildFailedError,
        MissingUpstreamTarball,
        Workspace,
        )

    if hoster is None and mode == 'attempt-push':
        warning('Unsupported hoster; will attempt to push to %s',
                main_branch.user_url)
        mode = 'push'
    with Workspace(main_branch, resume_branch=resume_branch) as ws, \
            ws.local_tree.lock_write():
        if ws.refreshed:
            overwrite = True
        run_pre_check(ws.local_tree, pre_check)
        if update_changelog is None:
            dch_guess = guess_update_changelog(ws.local_tree)
            if dch_guess:
                note(dch_guess[1])
                update_changelog = dch_guess[0]
            else:
                # Assume yes.
                update_changelog = True
        try:
            changer_result = changer.make_changes(
                ws.local_tree, subpath=subpath,
                update_changelog=update_changelog,
                committer=committer)
        except ChangerError as e:
            show_error(e.summary)
            return False

        if not ws.changes_since_main():
            if existing_proposal:
                note('%s: nothing left to do. Closing proposal.', pkg)
                existing_proposal.close()
            else:
                note('%s: nothing to do', pkg)
            return None

        try:
            run_post_check(ws.local_tree, post_check, ws.orig_revid)
        except PostCheckFailed as e:
            note('%s: %s', pkg, e)
            return False
        if build_verify:
            try:
                ws.build(builder=builder, result_dir=build_target_dir)
            except BuildFailedError:
                note('%s: build failed', pkg)
                return False
            except MissingUpstreamTarball:
                note('%s: unable to find upstream source', pkg)
                return False

        enable_tag_pushing(ws.local_tree.branch)

        kwargs: Dict[str, Any] = {}
        if breezy_version_info >= (3, 1):
            kwargs['tags'] = changer_result.tags

        try:
            publish_result = publish_changes(
                ws, mode, branch_name,
                get_proposal_description=partial(
                    changer.get_proposal_description, changer_result.mutator),
                get_proposal_commit_message=partial(
                    changer.get_commit_message, changer_result.mutator),
                dry_run=dry_run, hoster=hoster,
                allow_create_proposal=changer.allow_create_proposal(
                    changer_result.mutator),
                overwrite_existing=overwrite,
                existing_proposal=existing_proposal,
                labels=label,
                **kwargs)
        except UnsupportedHoster as e:
            show_error(
                '%s: No known supported hoster for %s. Run \'svp login\'?',
                pkg, e.branch.user_url)
            return False
        except NoSuchProject as e:
            note('%s: project %s was not found', pkg, e.project)
            return False
        except errors.PermissionDenied as e:
            note('%s: %s', pkg, e)
            return False
        except errors.DivergedBranches:
            note('%s: a branch exists. Use --overwrite to discard it.',
                 pkg)
            return False
        except HosterLoginRequired as e:
            show_error(
                'Credentials for hosting site at %r missing. '
                'Run \'svp login\'?', e.hoster.base_url)
            return False

        if publish_result.proposal:
            changer.describe(changer_result.mutator, publish_result)
        if diff:
            ws.show_diff(sys.stdout.buffer)

        return True


def run_changer(changer: DebianChanger, args: argparse.Namespace) -> int:
    import silver_platter   # noqa: F401

    ret = 0

    if args.name:
        branch_name = args.name
    else:
        branch_name = changer.suggest_branch_name()

    package_iter = iter_packages(
        args.packages, branch_name, args.overwrite, args.refresh)
    if args.fix_conflicted:
        package_iter = itertools.chain(
            package_iter, iter_conflicted(branch_name))

    for (pkg, main_branch, subpath, resume_branch, hoster, existing_proposal,
         overwrite) in package_iter:
        try:
            if _run_single_changer(
                    changer, pkg, main_branch, subpath, resume_branch, hoster,
                    existing_proposal, overwrite, args.mode,
                    branch_name, diff=args.diff,
                    committer=args.committer, build_verify=args.build_verify,
                    pre_check=args.pre_check, builder=args.builder,
                    post_check=args.post_check, dry_run=args.dry_run,
                    update_changelog=args.update_changelog,
                    label=args.label,
                    build_target_dir=args.build_target_dir) is False:
                ret = 1
        except NoSuchPackage:
            note('%s: no such package', pkg)
            ret = 1
        except (BranchMissing, BranchUnavailable, BranchUnsupported) as e:
            note('%s: ignoring: %s', pkg, e)
            ret = 1
    return ret


def run_single_changer(
        changer: DebianChanger, args: argparse.Namespace) -> int:
    import silver_platter   # noqa: F401

    if args.name:
        branch_name = args.name
    else:
        branch_name = changer.suggest_branch_name()

    try:
        (pkg, main_branch, subpath, resume_branch, hoster, existing_proposal,
         overwrite) = get_package(
                args.package, branch_name, overwrite_unrelated=args.overwrite,
                refresh=args.refresh)
    except NoSuchPackage:
        note('%s: no such package', args.package)
        return 1
    except (BranchMissing, BranchUnavailable, BranchUnsupported) as e:
        note('%s: ignoring: %s', args.package, e)
        return 1

    if _run_single_changer(
            changer, pkg, main_branch, subpath, resume_branch, hoster,
            existing_proposal, overwrite, args.mode, branch_name,
            diff=args.diff, committer=args.committer,
            build_verify=args.build_verify,
            pre_check=args.pre_check, builder=args.builder,
            post_check=args.post_check, dry_run=args.dry_run,
            update_changelog=args.update_changelog,
            label=args.label,
            build_target_dir=args.build_target_dir) is False:
        return 1
    else:
        return 0


def run_mutator(changer_cls, argv=None):
    import json
    import argparse
    import os
    from breezy.workingtree import WorkingTree
    parser = argparse.ArgumentParser()
    changer_cls.setup_parser(parser)
    args = parser.parse_args(argv)
    wt, subpath = WorkingTree.open_containing('.')
    changer = changer_cls.from_args(args)
    try:
        update_changelog_str = os.environ['UPDATE_CHANGELOG'],
    except KeyError:
        update_changelog = None
    else:
        if update_changelog_str == 'leave_changelog':
            update_changelog = False
        elif update_changelog_str == 'update_changelog':
            update_changelog = True
        else:
            # TODO(jelmer): Warn
            update_changelog = None

    try:
        base_metadata_path = os.environ['BASE_METADATA']
    except KeyError:
        existing_proposal = None
    else:
        with open(base_metadata_path, 'r') as f:
            base_metadata = json.load(f)

        class PreviousProposal(MergeProposal):

            def __init__(self, metadata):
                self.metadata = metadata

            def get_description(self):
                return self.metadata.get('description')

            def get_commit_message(self):
                return self.metadata.get('commit-message')

        existing_proposal = PreviousProposal(base_metadata['merge-proposal'])

    mutator_metadata = {}
    try:
        result = changer.make_changes(
            wt, subpath, update_changelog=update_changelog,
            committer=os.environ.get('COMMITTER'))
    except ChangerFailure as e:
        result_json = {
            'result-code': e.code,
            'description': e.description,
        }
    else:
        result_json = {
            'result-code': None,
            'description': result.description,
            'suggested-branch-name': changer.suggest_branch_name(),
            'tags': result.tags,
            'auxiliary-branches': result.auxiliary_branches,
            'value': changer.value(result.mutator),
            'mutator': mutator_metadata,
            'merge-proposal': {
                'sufficient': changer.allow_create_proposal(result.mutator),
                'commit-message': changer.get_commit_message(
                    result.mutator, existing_proposal),
                'title': None,
                'description-plain': changer.get_proposal_description(
                    result.mutator, 'plain', existing_proposal),
                'description-markdown': changer.get_proposal_description(
                    result.mutator, 'markdown', existing_proposal),
            }
        }
    json.dump(result_json, sys.stdout, indent=4)
    return 0
