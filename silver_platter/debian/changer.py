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

from functools import partial
import logging
import os
import sys
from typing import Any, List, Optional, Dict, Iterable, Tuple

from breezy import version_info as breezy_version_info
from breezy.branch import Branch
from breezy.propose import Hoster, MergeProposal
from breezy.transport import Transport
from breezy.workingtree import WorkingTree

from . import (
    control_files_in_root,
    open_packaging_branch,
    guess_update_changelog,
    DEFAULT_BUILDER,
)
from ..proposal import (
    HosterLoginRequired,
    UnsupportedHoster,
    enable_tag_pushing,
    find_existing_proposed,
    get_hoster,
    iter_conflicted,
)

from ..publish import (
    SUPPORTED_MODES,
    InsufficientChangesForNewProposal,
)
from ..utils import (
    run_pre_check,
    run_post_check,
    PostCheckFailed,
    full_branch_url,
)


class ChangerReporter(object):
    def report_context(self, context):
        raise NotImplementedError(self.report_context)

    def report_metadata(self, key, value):
        raise NotImplementedError(self.report_metadata)

    def get_base_metadata(self, key, default_value=None):
        raise NotImplementedError(self.get_base_metadata)


class ChangerError(Exception):
    def __init__(
            self, category: str, summary: str, original: Optional[Exception] = None, details: Any = None
    ):
        self.category = category
        self.summary = summary
        self.original = original
        self.details = details


class ChangerResult(object):
    def __init__(
        self,
        description: Optional[str],
        mutator: Any,
        tags: Optional[Dict[str, bytes]] = None,
        value: Optional[int] = None,
        proposed_commit_message: Optional[str] = None,
        title: Optional[str] = None,
        labels: Optional[List[str]] = None,
        sufficient_for_proposal: bool = True,
    ):
        self.description = description
        self.mutator = mutator
        self.tags = tags or {}
        self.value = value
        self.proposed_commit_message = proposed_commit_message
        self.title = title
        self.labels = labels
        self.sufficient_for_proposal = sufficient_for_proposal


def get_package(
    package: str,
    branch_name: str,
    overwrite_unrelated: bool = False,
    refresh: bool = False,
    possible_transports: Optional[List[Transport]] = None,
    possible_hosters: Optional[List[Hoster]] = None,
    owner: Optional[str] = None,
) -> Tuple[
    str,
    Branch,
    str,
    Optional[Branch],
    Optional[Hoster],
    Optional[MergeProposal],
    Optional[bool],
]:
    main_branch, subpath = open_packaging_branch(
        package, possible_transports=possible_transports
    )

    overwrite: Optional[bool] = False

    try:
        hoster = get_hoster(main_branch, possible_hosters=possible_hosters)
    except UnsupportedHoster:
        # We can't figure out what branch to resume from when there's no
        # hoster that can tell us.
        resume_branch = None
        existing_proposal = None
        hoster = None
    else:
        (resume_branch, overwrite, existing_proposal) = find_existing_proposed(
            main_branch,
            hoster,
            branch_name,
            owner=owner,
            overwrite_unrelated=overwrite_unrelated,
        )
    if refresh:
        overwrite = True
        resume_branch = None

    return (
        package,
        main_branch,
        subpath,
        resume_branch,
        hoster,
        existing_proposal,
        overwrite,
    )


def iter_packages(
    packages: Iterable[str],
    branch_name: str,
    overwrite_unrelated: bool = False,
    refresh: bool = False,
    derived_owner: Optional[str] = None,
):
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
    possible_transports: List[Transport] = []
    possible_hosters: List[Hoster] = []

    for pkg in packages:
        logging.info("Processing: %s", pkg)

        (
            pkg,
            main_branch,
            subpath,
            resume_branch,
            hoster,
            existing_proposal,
            overwrite,
        ) = get_package(
            pkg,
            branch_name,
            overwrite_unrelated=overwrite_unrelated,
            refresh=refresh,
            possible_transports=possible_transports,
            possible_hosters=possible_hosters,
            owner=derived_owner,
        )

        yield (
            pkg,
            main_branch,
            subpath,
            resume_branch,
            hoster,
            existing_proposal,
            overwrite,
        )


class DummyChangerReporter(ChangerReporter):
    def report_context(self, context):
        pass

    def report_metadata(self, key, value):
        pass

    def get_base_metadata(self, key, default_value=None):
        return None


def _run_single_changer(  # noqa: C901
    changer,
    pkg: str,
    main_branch: Branch,
    subpath: str,
    resume_branch: Optional[Branch],
    hoster: Optional[Hoster],
    existing_proposal: Optional[MergeProposal],
    overwrite: Optional[bool],
    mode: str,
    branch_name: str,
    diff: bool = False,
    committer: Optional[str] = None,
    build_verify: bool = False,
    preserve_repositories: bool = False,
    install: bool = False,
    pre_check: Optional[str] = None,
    post_check: Optional[str] = None,
    builder: str = DEFAULT_BUILDER,
    dry_run: bool = False,
    update_changelog: Optional[bool] = None,
    label: Optional[List[str]] = None,
    derived_owner: Optional[str] = None,
    build_target_dir: Optional[str] = None,
) -> Optional[bool]:
    from breezy import errors
    from . import (
        BuildFailedError,
        MissingUpstreamTarball,
        Workspace,
    )

    if hoster is None and mode == "attempt-push":
        logging.warn(
            "Unsupported hoster; will attempt to push to %s",
            full_branch_url(main_branch),
        )
        mode = "push"
    with Workspace(
        main_branch, resume_branch=resume_branch
    ) as ws, ws.local_tree.lock_write():
        if ws.refreshed:
            overwrite = True
        run_pre_check(ws.local_tree, pre_check)
        if control_files_in_root(ws.local_tree, subpath):
            debian_path = subpath
        else:
            debian_path = os.path.join(subpath, "debian")
        if update_changelog is None:
            dch_guess = guess_update_changelog(ws.local_tree, debian_path)
            if dch_guess:
                logging.info('%s', dch_guess[1])
                update_changelog = dch_guess[0]
            else:
                # Assume yes.
                update_changelog = True
        try:
            changer_result = changer.make_changes(
                ws.local_tree,
                subpath=subpath,
                update_changelog=update_changelog,
                committer=committer,
                reporter=DummyChangerReporter(),
            )
        except ChangerError as e:
            logging.error('%s: %s', e.category, e.summary)
            return False

        if not ws.changes_since_main():
            if existing_proposal:
                logging.info("%s: nothing left to do. Closing proposal.", pkg)
                existing_proposal.close()
            else:
                logging.info("%s: nothing to do", pkg)
            return None

        try:
            run_post_check(ws.local_tree, post_check, ws.base_revid)
        except PostCheckFailed as e:
            logging.info("%s: %s", pkg, e)
            return False
        if build_verify or install:
            try:
                ws.build(builder=builder, result_dir=build_target_dir)
            except BuildFailedError:
                logging.info("%s: build failed", pkg)
                return False
            except MissingUpstreamTarball:
                logging.info("%s: unable to find upstream source", pkg)
                return False

        if install:
            from .apply import install_built_package
            install_built_package(ws.local_tree, ws.subpath, build_target_dir)

        enable_tag_pushing(ws.local_tree.branch)

        kwargs: Dict[str, Any] = {}
        if breezy_version_info >= (3, 1):
            kwargs["tags"] = changer_result.tags

        try:
            publish_result = ws.publish_changes(
                mode,
                branch_name,
                get_proposal_description=partial(
                    changer.get_proposal_description, changer_result.mutator
                ),
                get_proposal_commit_message=(
                    lambda oldmp: changer_result.proposed_commit_message
                ),
                dry_run=dry_run,
                hoster=hoster,
                allow_create_proposal=changer_result.sufficient_for_proposal,
                overwrite_existing=overwrite,
                existing_proposal=existing_proposal,
                derived_owner=derived_owner,
                labels=label,
                **kwargs
            )
        except UnsupportedHoster as e:
            logging.error(
                "%s: No known supported hoster for %s. Run 'svp login'?",
                pkg,
                full_branch_url(e.branch),
            )
            return False
        except NoSuchProject as e:
            logging.info("%s: project %s was not found", pkg, e.project)
            return False
        except errors.PermissionDenied as e:
            logging.info("%s: %s", pkg, e)
            return False
        except errors.DivergedBranches:
            logging.info("%s: a branch exists. Use --overwrite to discard it.", pkg)
            return False
        except InsufficientChangesForNewProposal:
            logging.info('%s: insufficient changes for a new merge proposal',
                         pkg)
            return False
        except HosterLoginRequired as e:
            logging.error(
                "Credentials for hosting site at %r missing. " "Run 'svp login'?",
                e.hoster.base_url,
            )
            return False

        if publish_result.proposal:
            changer.describe(changer_result.mutator, publish_result)
        if diff:
            for branch_entry in changer_result.branches:
                role = branch_entry[0]
                if len(changer_result.branches) > 1:
                    sys.stdout.write("%s\n" % role)
                    sys.stdout.write(("-" * len(role)) + "\n")
                sys.stdout.flush()
                changer_result.show_diff(
                    ws.local_tree.branch.repository, sys.stdout.buffer, role=role
                )
                if len(changer_result.branches) > 1:
                    sys.stdout.write("\n")
        if preserve_repositories:
            ws.defer_destroy()
            logging.info('Workspace preserved in %s', ws.local_tree.abspath(ws.subpath))

        return True
