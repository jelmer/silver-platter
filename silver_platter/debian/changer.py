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

from __future__ import absolute_import

from functools import partial
import itertools
import sys

from breezy.trace import note, warning, show_error

from . import (
    open_packaging_branch,
    should_update_changelog,
    NoSuchPackage,
    DEFAULT_BUILDER,
    )
from ..proposal import (
    HosterLoginRequired,
    SUPPORTED_MODES,
    enable_tag_pushing,
    find_existing_proposed,
    get_hoster,
    iter_conflicted,
    publish_changes,
    )
from ..utils import (
    BranchMissing,
    BranchUnavailable,
    BranchUnsupported,
    run_pre_check,
    run_post_check,
    PostCheckFailed,
    )


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
        (package name, main branch object, branch to resume (if any),
         hoster (None if the hoster is not supported),
         existing_proposal, whether to overwrite the branch)
    """
    from breezy.plugins.propose.propose import (
        UnsupportedHoster,
        )

    possible_transports = []
    possible_hosters = []

    for pkg in packages:
        note('Processing: %s', pkg)

        try:
            main_branch = open_packaging_branch(
                pkg, possible_transports=possible_transports)
        except NoSuchPackage:
            note('%s: no such package', pkg)
            continue
        except (BranchMissing, BranchUnavailable, BranchUnsupported) as e:
            note('%s: ignoring: %s', pkg, e)
            continue

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

        yield (pkg, main_branch, resume_branch, hoster, existing_proposal,
               overwrite)


class ChangerError(Exception):

    def __init__(self, summary, original):
        self.summary = summary
        self.original = original


def setup_parser(parser):
    parser.add_argument("packages", nargs='*')
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
        '--fix-conflicted', action='store_true',
        help='Fix existing merge proposals that are conflicted.')
    parser.add_argument(
        '--name', type=str,
        help='Proposed branch name', default=None)
    parser.add_argument(
        '--label', type=str,
        help='Label to attach', action="append", default=[])


class DebianChanger(object):
    """A class which can make and explain changes to a Debian package in VCS.
    """

    @classmethod
    def setup_parser(cls, parser):
        raise NotImplementedError(cls.setup_parser)

    @classmethod
    def from_args(cls, args):
        raise NotImplementedError(cls.from_args)

    def suggest_branch_name(self):
        raise NotImplementedError(self.suggest_branch_name)

    def make_changes(self, local_tree, subpath, update_changelog, committer):
        raise NotImplementedError(self.make_changes)

    def get_proposal_description(self, applied, existing_proposal):
        raise NotImplementedError(self.get_proposal_description)

    def get_commit_message(self, applied, existing_proposal):
        raise NotImplementedError(self.get_commit_message)

    def allow_create_proposal(self, applied):
        raise NotImplementedError(self.allow_create_proposal)

    def describe(self, applied, publish_result):
        raise NotImplementedError(self.describe)


def run_changer(changer, args):
    import silver_platter   # noqa: F401

    from . import (
        BuildFailedError,
        MissingUpstreamTarball,
        Workspace,
        )
    from breezy import (
        errors,
        )
    from breezy.plugins.propose.propose import (
        NoSuchProject,
        UnsupportedHoster,
        )

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

    for (pkg, main_branch, resume_branch, hoster, existing_proposal,
         overwrite) in package_iter:
        if hoster is None and args.mode == 'attempt-push':
            warning('Unsupported hoster; will attempt to push to %s',
                    main_branch.user_url)
            args.mode = 'push'
        with Workspace(main_branch, resume_branch=resume_branch) as ws, \
                ws.local_tree.lock_write():
            if ws.refreshed:
                overwrite = True
            run_pre_check(ws.local_tree, args.pre_check)
            if args.update_changelog is None:
                update_changelog = should_update_changelog(
                    ws.local_tree.branch)
            else:
                update_changelog = args.update_changelog
            try:
                changer_result = changer.make_changes(
                    ws.local_tree, subpath='',
                    update_changelog=update_changelog,
                    committer=args.committer)
            except ChangerError as e:
                show_error(e.summary)
                ret = 1
                continue

            if not ws.changes_since_main():
                if existing_proposal:
                    note('%s: nothing left to do. Closing proposal.', pkg)
                    existing_proposal.close()
                else:
                    note('%s: nothing to do', pkg)
            continue

        try:
            run_post_check(ws.local_tree, args.post_check, ws.orig_revid)
        except PostCheckFailed as e:
            note('%s: %s', pkg, e)
            continue
        if args.build_verify:
            try:
                ws.build(builder=args.builder,
                         result_dir=args.build_target_dir)
            except BuildFailedError:
                note('%s: build failed', pkg)
                ret = 1
                continue
            except MissingUpstreamTarball:
                note('%s: unable to find upstream source', pkg)
                ret = 1
                continue

        enable_tag_pushing(ws.local_tree.branch)

        try:
            publish_result = publish_changes(
                ws, args.mode, branch_name,
                get_proposal_description=partial(
                    changer.get_proposal_description, changer_result),
                get_proposal_commit_message=partial(
                    changer.get_commit_message, changer_result),
                dry_run=args.dry_run, hoster=hoster,
                allow_create_proposal=partial(
                    changer.allow_create_proposal, changer_result),
                overwrite_existing=overwrite,
                existing_proposal=existing_proposal,
                labels=args.label)
        except UnsupportedHoster as e:
            show_error(
                '%s: No known supported hoster for %s. Run \'svp login\'?',
                pkg, e.branch.user_url)
            ret = 1
            continue
        except NoSuchProject as e:
            note('%s: project %s was not found', pkg, e.project)
            ret = 1
            continue
        except errors.PermissionDenied as e:
            note('%s: %s', pkg, e)
            ret = 1
            continue
        except errors.DivergedBranches:
            note('%s: a branch exists. Use --overwrite to discard it.',
                 pkg)
            ret = 1
            continue
        except HosterLoginRequired as e:
            show_error(
                'Credentials for hosting site at %r missing. '
                'Run \'svp login\'?', e.hoster.base_url)
            ret = 1
            continue

        if publish_result.proposal:
            changer.describe(changer_result, publish_result)
        if args.diff:
            ws.show_diff(sys.stdout.buffer)
    return ret
