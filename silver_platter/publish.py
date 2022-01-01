#!/usr/bin/python
# Copyright (C) 2020 Jelmer Vernooij <jelmer@jelmer.uk>
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
from typing import List, Union, Dict, Optional, Tuple, Any, Callable

from breezy.branch import Branch
from breezy import (
    errors,
    merge as _mod_merge,
    revision as _mod_revision,
)
from breezy.errors import PermissionDenied
from breezy.memorybranch import MemoryBranch
from breezy.propose import (
    get_hoster,
    Hoster,
    MergeProposal,
    MergeProposalExists,
    NoSuchProject,
    UnsupportedHoster,
)
from breezy.transport import Transport

from breezy.propose import (
    SourceNotDerivedFromTarget,
)


from .utils import (
    open_branch,
    full_branch_url,
)


__all__ = [
    "push_changes",
    "push_derived_changes",
    "propose_changes",
    "EmptyMergeProposal",
    "check_proposal_diff",
    "DryRunProposal",
    "find_existing_proposed",
    "NoSuchProject",
    "PermissionDenied",
    "UnsupportedHoster",
    "SourceNotDerivedFromTarget",
]


MODE_PUSH = "push"
MODE_ATTEMPT_PUSH = "attempt-push"
MODE_PROPOSE = "propose"
MODE_PUSH_DERIVED = "push-derived"
SUPPORTED_MODES: List[str] = [
    MODE_PUSH,
    MODE_ATTEMPT_PUSH,
    MODE_PROPOSE,
    MODE_PUSH_DERIVED,
]


def _tag_selector_from_tags(tags):
    # TODO(jelmer): Select dict
    return tags.__contains__


def push_result(
    local_branch: Branch,
    remote_branch: Branch,
    additional_colocated_branches: Optional[Union[List[str], Dict[str, str]]] = None,
    tags: Optional[Union[Dict[str, bytes], List[str]]] = None,
    stop_revision: Optional[bytes] = None,
) -> None:
    kwargs = {}
    if tags is not None:
        kwargs["tag_selector"] = _tag_selector_from_tags(tags)
    try:
        local_branch.push(
            remote_branch, overwrite=False, stop_revision=stop_revision, **kwargs
        )
    except errors.LockFailed as e:
        # Almost certainly actually a PermissionDenied error..
        raise errors.PermissionDenied(path=full_branch_url(remote_branch), extra=e)
    for from_branch_name in additional_colocated_branches or []:
        try:
            add_branch = local_branch.controldir.open_branch(name=from_branch_name)  # type: ignore
        except errors.NotBranchError:
            pass
        else:
            if isinstance(additional_colocated_branches, dict):
                to_branch_name = additional_colocated_branches[from_branch_name]
            else:
                to_branch_name = from_branch_name
            remote_branch.controldir.push_branch(add_branch, name=to_branch_name, **kwargs)  # type: ignore


def push_changes(
    local_branch: Branch,
    main_branch: Branch,
    hoster: Optional[Hoster],
    possible_transports: Optional[List[Transport]] = None,
    additional_colocated_branches: Optional[Union[List[str], Dict[str, str]]] = None,
    dry_run: bool = False,
    tags: Optional[Union[Dict[str, bytes], List[str]]] = None,
    stop_revision: Optional[bytes] = None,
) -> None:
    """Push changes to a branch."""
    if hoster is None:
        push_url = main_branch.user_url
    else:
        push_url = hoster.get_push_url(main_branch)
    logging.info("pushing to %s", push_url)
    target_branch = open_branch(push_url, possible_transports=possible_transports)
    if not dry_run:
        push_result(
            local_branch,
            target_branch,
            additional_colocated_branches,
            tags=tags,
            stop_revision=stop_revision,
        )


def push_derived_changes(
    local_branch: Branch,
    main_branch: Branch,
    hoster: Hoster,
    name: str,
    overwrite_existing: Optional[bool] = False,
    owner: Optional[str] = None,
    tags: Optional[Union[Dict[str, bytes], List[str]]] = None,
    stop_revision: Optional[bytes] = None,
) -> Tuple[Branch, str]:
    kwargs = {}
    if tags is not None:
        kwargs["tag_selector"] = _tag_selector_from_tags(tags)
    remote_branch, public_branch_url = hoster.publish_derived(
        local_branch,
        main_branch,
        name=name,
        overwrite=overwrite_existing,
        owner=owner,
        revision_id=stop_revision,
        **kwargs
    )
    return remote_branch, public_branch_url


def propose_changes(  # noqa: C901
    local_branch: Branch,
    main_branch: Branch,
    hoster: Hoster,
    name: str,
    mp_description: str,
    resume_branch: Optional[Branch] = None,
    resume_proposal: Optional[MergeProposal] = None,
    overwrite_existing: Optional[bool] = True,
    labels: Optional[List[str]] = None,
    dry_run: bool = False,
    commit_message: Optional[str] = None,
    additional_colocated_branches: Optional[Union[List[str], Dict[str, str]]] = None,
    allow_empty: bool = False,
    reviewers: Optional[List[str]] = None,
    tags: Optional[Union[Dict[str, bytes], List[str]]] = None,
    owner: Optional[str] = None,
    stop_revision: Optional[bytes] = None,
    allow_collaboration: bool = False,
) -> Tuple[MergeProposal, bool]:
    """Create or update a merge proposal.

    Args:
      local_branch: Local branch with changes to propose
      main_branch: Target branch to propose against
      hoster: Associated hoster for main branch
      mp_description: Merge proposal description
      resume_branch: Existing derived branch
      resume_proposal: Existing merge proposal to resume
      overwrite_existing: Whether to overwrite any other existing branch
      labels: Labels to add
      dry_run: Whether to just dry-run the change
      commit_message: Optional commit message
      additional_colocated_branches: Additional colocated branches to propose
      allow_empty: Whether to allow empty merge proposals
      reviewers: List of reviewers
      tags: Tags to push (None for default behaviour)
      owner: Derived branch owner
      stop_revision: Revision to stop pushing at
      allow_collaboration: Allow target branch owners to modify source branch
    Returns:
      Tuple with (proposal, is_new)
    """
    if not allow_empty:
        check_proposal_diff(local_branch, main_branch, stop_revision)
    push_kwargs = {}
    if tags is not None:
        push_kwargs["tag_selector"] = _tag_selector_from_tags(tags)
    if not dry_run:
        if resume_branch is not None:
            local_branch.push(
                resume_branch,
                overwrite=overwrite_existing,
                stop_revision=stop_revision,
                **push_kwargs
            )
            remote_branch = resume_branch
        else:
            remote_branch, public_branch_url = hoster.publish_derived(
                local_branch,
                main_branch,
                name=name,
                overwrite=overwrite_existing,
                revision_id=stop_revision,
                owner=owner,
                **push_kwargs
            )
        for from_branch_name in additional_colocated_branches or []:
            try:
                local_colo_branch = local_branch.controldir.open_branch(  # type: ignore
                    name=from_branch_name
                )
            except errors.NotBranchError:
                pass
            else:
                if isinstance(additional_colocated_branches, dict):
                    to_branch_name = additional_colocated_branches[from_branch_name]
                else:
                    to_branch_name = from_branch_name
                remote_branch.controldir.push_branch(  # type: ignore
                    source=local_colo_branch,
                    overwrite=overwrite_existing,
                    name=to_branch_name,
                    **push_kwargs
                )
    if resume_proposal is not None and dry_run:
        resume_proposal = DryRunProposal.from_existing(
            resume_proposal, source_branch=local_branch
        )
    if (
        resume_proposal is not None
        and getattr(resume_proposal, "is_closed", None)
        and resume_proposal.is_closed()
    ):
        from breezy.propose import (
            ReopenFailed,
        )

        try:
            resume_proposal.reopen()  # type: ignore
        except ReopenFailed:
            logging.info("Reopening existing proposal failed. Creating new proposal.")
            resume_proposal = None
    if resume_proposal is None:
        if not dry_run:
            proposal_builder = hoster.get_proposer(remote_branch, main_branch)
            kwargs: Dict[str, Any] = {}
            kwargs["commit_message"] = commit_message
            kwargs["allow_collaboration"] = allow_collaboration
            try:
                mp = proposal_builder.create_proposal(
                    description=mp_description,
                    labels=labels,
                    reviewers=reviewers,
                    **kwargs
                )
            except MergeProposalExists as e:
                if getattr(e, "existing_proposal", None) is None:
                    # Hoster didn't tell us where the actual proposal is.
                    raise
                resume_proposal = e.existing_proposal
            except errors.PermissionDenied:
                logging.info("Permission denied while trying to create " "proposal.")
                raise
            else:
                return (mp, True)
        else:
            mp = DryRunProposal(
                local_branch,
                main_branch,
                labels=labels,
                description=mp_description,
                commit_message=commit_message,
                reviewers=reviewers,
                owner=owner,
                stop_revision=stop_revision,
            )
            return (mp, True)
    # Check that the proposal doesn't already has this description.
    # Setting the description (regardless of whether it changes)
    # causes Launchpad to send emails.
    if resume_proposal.get_description() != mp_description:
        resume_proposal.set_description(mp_description)
    if resume_proposal.get_commit_message() != commit_message:
        try:
            resume_proposal.set_commit_message(commit_message)
        except errors.UnsupportedOperation:
            pass
    return (resume_proposal, False)


class EmptyMergeProposal(Exception):
    """Merge proposal does not have any changes."""

    def __init__(self, local_branch: Branch, main_branch: Branch):
        self.local_branch = local_branch
        self.main_branch = main_branch


def check_proposal_diff(
    other_branch: Branch, main_branch: Branch, stop_revision: Optional[bytes] = None
) -> None:
    if stop_revision is None:
        stop_revision = other_branch.last_revision()
    main_revid = main_branch.last_revision()
    other_branch.repository.fetch(main_branch.repository, main_revid)
    with other_branch.lock_read():
        main_tree = other_branch.repository.revision_tree(main_revid)
        revision_graph = other_branch.repository.get_graph()
        tree_branch = MemoryBranch(other_branch.repository, (None, main_revid), None)
        merger = _mod_merge.Merger(
            tree_branch, this_tree=main_tree, revision_graph=revision_graph
        )
        merger.set_other_revision(stop_revision, other_branch)
        try:
            merger.find_base()
        except errors.UnrelatedBranches:
            merger.set_base_revision(_mod_revision.NULL_REVISION, other_branch)
        merger.merge_type = _mod_merge.Merge3Merger  # type: ignore
        tree_merger = merger.make_merger()
        with tree_merger.make_preview_transform() as tt:
            changes = tt.iter_changes()
            if not any(changes):
                raise EmptyMergeProposal(other_branch, main_branch)


class DryRunProposal(MergeProposal):
    """A merge proposal that is not actually created.

    :ivar url: URL for the merge proposal
    """

    def __init__(
        self,
        source_branch: Branch,
        target_branch: Branch,
        labels: Optional[List[str]] = None,
        description: Optional[str] = None,
        commit_message: Optional[str] = None,
        reviewers: Optional[List[str]] = None,
        owner: Optional[str] = None,
        stop_revision: Optional[bytes] = None,
    ):
        self.description = description
        self.closed = False
        self.labels = labels or []
        self.source_branch = source_branch
        self.target_branch = target_branch
        self.commit_message = commit_message
        self.url = None
        self.reviewers = reviewers
        self.owner = owner
        self.stop_revision = stop_revision

    @classmethod
    def from_existing(
        cls, mp: MergeProposal, source_branch: Optional[Branch] = None
    ) -> MergeProposal:
        if source_branch is None:
            source_branch = open_branch(mp.get_source_branch_url())
        commit_message = mp.get_commit_message()
        return cls(
            source_branch=source_branch,
            target_branch=open_branch(mp.get_target_branch_url()),
            description=mp.get_description(),
            commit_message=commit_message,
        )

    def __repr__(self) -> str:
        return "%s(%r, %r)" % (
            self.__class__.__name__,
            self.source_branch,
            self.target_branch,
        )

    def get_description(self) -> Optional[str]:
        """Get the description of the merge proposal."""
        return self.description

    def set_description(self, description: str) -> None:
        self.description = description

    def get_commit_message(self) -> Optional[str]:
        return self.commit_message

    def set_commit_message(self, commit_message: str) -> None:
        self.commit_message = commit_message

    def get_source_branch_url(self) -> str:
        """Return the source branch."""
        return full_branch_url(self.source_branch)

    def get_target_branch_url(self) -> str:
        """Return the target branch."""
        return full_branch_url(self.target_branch)

    def close(self) -> None:
        """Close the merge proposal (without merging it)."""
        self.closed = True

    def is_merged(self) -> bool:
        """Check whether this merge proposal has been merged."""
        return False

    def is_closed(self) -> bool:
        """Check whether this merge proposal has been closed."""
        return False

    def reopen(self) -> None:
        pass


def find_existing_proposed(
    main_branch: Branch,
    hoster: Hoster,
    name: str,
    overwrite_unrelated: bool = False,
    owner: Optional[str] = None,
    preferred_schemes: Optional[List[str]] = None,
) -> Tuple[Optional[Branch], Optional[bool], Optional[MergeProposal]]:
    """Find an existing derived branch with the specified name, and proposal.

    Args:
      main_branch: Main branch
      hoster: The hoster
      name: Name of the derived branch
      overwrite_unrelated: Whether to overwrite existing (but unrelated)
        branches
      preferred_schemes: List of preferred schemes
    Returns:
      Tuple with (resume_branch, overwrite_existing, existing_proposal)
      The resume_branch is the branch to continue from; overwrite_existing
      means there is an existing branch in place that should be overwritten.
    """
    try:
        if preferred_schemes is not None:
            existing_branch = hoster.get_derived_branch(
                main_branch, name=name, owner=owner, preferred_schemes=preferred_schemes
            )
        else:  # TODO: Support older versions of breezy without preferred_schemes
            existing_branch = hoster.get_derived_branch(
                main_branch, name=name, owner=owner
            )
    except errors.NotBranchError:
        return (None, None, None)
    else:
        logging.info(
            "Branch %s already exists (branch at %s)",
            name,
            full_branch_url(existing_branch),
        )
        # If there is an open or rejected merge proposal, resume that.
        merged_proposal = None
        for mp in hoster.iter_proposals(existing_branch, main_branch, status="all"):
            if not mp.is_closed() and not mp.is_merged():
                return (existing_branch, False, mp)
            else:
                merged_proposal = mp
        else:
            if merged_proposal is not None:
                logging.info(
                    "There is a proposal that has already been merged at %s.",
                    merged_proposal.url,
                )
                return (None, True, None)
            else:
                # No related merge proposals found, but there is an existing
                # branch (perhaps for a different target branch?)
                if overwrite_unrelated:
                    return (None, True, None)
                else:
                    # TODO(jelmer): What to do in this case?
                    return (None, False, None)


def merge_conflicts(
    main_branch: Branch, other_branch: Branch, other_revision: Optional[bytes] = None
) -> bool:
    """Check whether two branches are conflicted when merged.

    Args:
      main_branch: Main branch to merge into
      other_branch: Branch to merge (and use for scratch access, needs write
                    access)
      other_revision: Other revision to check
    Returns:
      boolean indicating whether the merge would result in conflicts
    """
    if other_revision is None:
        other_revision = other_branch.last_revision()
    if other_branch.repository.get_graph().is_ancestor(
        main_branch.last_revision(), other_revision
    ):
        return False

    other_branch.repository.fetch(
        main_branch.repository, revision_id=main_branch.last_revision()
    )

    # Reset custom merge hooks, since they could make it harder to detect
    # conflicted merges that would appear on the hosting site.
    old_file_content_mergers = _mod_merge.Merger.hooks["merge_file_content"]
    _mod_merge.Merger.hooks["merge_file_content"] = []

    other_tree = other_branch.repository.revision_tree(other_revision)
    try:
        try:
            merger = _mod_merge.Merger.from_revision_ids(
                other_tree,
                other_branch=other_branch,
                other=main_branch.last_revision(),
                tree_branch=other_branch,
            )
        except errors.UnrelatedBranches:
            # Unrelated branches don't technically *have* to lead to
            # conflicts, but there's not a lot to be salvaged here, either.
            return True
        merger.merge_type = _mod_merge.Merge3Merger
        tree_merger = merger.make_merger()
        with tree_merger.make_preview_transform():
            return bool(tree_merger.cooked_conflicts)
    finally:
        _mod_merge.Merger.hooks["merge_file_content"] = old_file_content_mergers


class PublishResult(object):
    """A object describing the result of a publish action."""

    def __init__(
        self, mode: str, proposal: Optional[MergeProposal] = None, is_new: bool = False
    ) -> None:
        self.mode = mode
        self.proposal = proposal
        self.is_new = is_new

    def __tuple__(self) -> Tuple[Optional[MergeProposal], bool]:
        # Backwards compatibility
        return (self.proposal, self.is_new)


class InsufficientChangesForNewProposal(Exception):
    """There were not enough changes for a new merge proposal."""


def publish_changes(
    local_branch: Branch,
    main_branch: Branch,
    resume_branch: Optional[Branch],
    mode: str,
    name: str,
    get_proposal_description: Callable[[str, Optional[MergeProposal]], str],
    get_proposal_commit_message: Callable[
        [Optional[MergeProposal]], Optional[str]
    ] = None,
    dry_run: bool = False,
    hoster: Optional[Hoster] = None,
    allow_create_proposal: bool = True,
    labels: Optional[List[str]] = None,
    overwrite_existing: Optional[bool] = True,
    existing_proposal: Optional[MergeProposal] = None,
    reviewers: Optional[List[str]] = None,
    tags: Optional[Union[List[str], Dict[str, bytes]]] = None,
    derived_owner: Optional[str] = None,
    allow_collaboration: bool = False,
    stop_revision: Optional[bytes] = None,
) -> PublishResult:
    """Publish a set of changes.

    Args:
      ws: Workspace to push from
      mode: Mode to use ('push', 'push-derived', 'propose')
      name: Branch name to push
      get_proposal_description: Function to retrieve proposal description
      get_proposal_commit_message: Function to retrieve proposal commit message
      dry_run: Whether to dry run
      hoster: Hoster, if known
      allow_create_proposal: Whether to allow creating proposals
      labels: Labels to set for any merge proposals
      overwrite_existing: Whether to overwrite existing (but unrelated) branch
      existing_proposal: Existing proposal to update
      reviewers: List of reviewers for merge proposal
      tags: Tags to push (None for default behaviour)
      derived_owner: Name of any derived branch
      allow_collaboration: Whether to allow target branch owners to modify
        source branch.
    """
    if mode not in SUPPORTED_MODES:
        raise ValueError("invalid mode %r" % mode)

    if stop_revision is None:
        stop_revision = local_branch.last_revision()

    if stop_revision == main_branch.last_revision():
        if existing_proposal is not None:
            logging.info("closing existing merge proposal - no new revisions")
            existing_proposal.close()
        return PublishResult(mode)

    if resume_branch and resume_branch.last_revision() == stop_revision:
        # No new revisions added on this iteration, but changes since main
        # branch. We may not have gotten round to updating/creating the
        # merge proposal last time.
        logging.info("No changes added; making sure merge proposal is up to date.")

    if hoster is None:
        hoster = get_hoster(main_branch)

    if mode == MODE_PUSH_DERIVED:
        (remote_branch, public_url) = push_derived_changes(
            local_branch,
            main_branch,
            hoster=hoster,
            name=name,
            overwrite_existing=overwrite_existing,
            tags=tags,
            owner=derived_owner,
            stop_revision=stop_revision,
        )
        return PublishResult(mode)

    if mode in (MODE_PUSH, MODE_ATTEMPT_PUSH):
        try:
            # breezy would do this check too, but we want to be *really* sure.
            with local_branch.lock_read():
                graph = local_branch.repository.get_graph()
                if not graph.is_ancestor(main_branch.last_revision(), stop_revision):
                    raise errors.DivergedBranches(main_branch, local_branch)
            push_changes(
                local_branch,
                main_branch,
                hoster=hoster,
                dry_run=dry_run,
                tags=tags,
                stop_revision=stop_revision,
            )
        except errors.PermissionDenied:
            if mode == MODE_ATTEMPT_PUSH:
                logging.info("push access denied, falling back to propose")
                mode = MODE_PROPOSE
            else:
                logging.info("permission denied during push")
                raise
        else:
            return PublishResult(mode=mode)

    assert mode == "propose"
    if not resume_branch and not allow_create_proposal:
        raise InsufficientChangesForNewProposal()

    mp_description = get_proposal_description(
        getattr(hoster, "merge_proposal_description_format", "plain"),
        existing_proposal if resume_branch else None,
    )
    if get_proposal_commit_message is not None:
        commit_message = get_proposal_commit_message(
            existing_proposal if resume_branch else None
        )
    (proposal, is_new) = propose_changes(
        local_branch,
        main_branch,
        hoster=hoster,
        name=name,
        mp_description=mp_description,
        resume_branch=resume_branch,
        resume_proposal=existing_proposal,
        overwrite_existing=overwrite_existing,
        labels=labels,
        dry_run=dry_run,
        commit_message=commit_message,
        reviewers=reviewers,
        tags=tags,
        owner=derived_owner,
        allow_collaboration=allow_collaboration,
        stop_revision=stop_revision,
    )
    return PublishResult(mode, proposal, is_new)
