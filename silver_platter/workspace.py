#!/usr/bin/python
# Copyright (C) 2018-2020 Jelmer Vernooij <jelmer@jelmer.uk>
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

from typing import Optional, Callable, List, Union, Dict, BinaryIO, Any, Tuple

from breezy.branch import Branch
from breezy.tree import Tree
from breezy.workingtree import WorkingTree
from breezy.diff import show_diff_trees
from breezy.errors import (
    DivergedBranches,
    NotBranchError,
    NoColocatedBranchSupport,
    PermissionDenied,
    )
from breezy.propose import (
    get_hoster,
    Hoster,
    MergeProposal,
    )

from breezy.trace import note

from .publish import (
    merge_conflicts,
    propose_changes,
    push_changes,
    push_derived_changes,
    )
from .utils import (
    create_temp_sprout,
    full_branch_url,
    )


__all__ = [
    'Workspace',
    'publish_changes',
    'MergeProposalDescriptionMissing',
    'PublishResult',
    ]


SUPPORTED_MODES: List[str] = [
    'push',
    'attempt-push',
    'propose',
    'push-derived',
    ]


class MergeProposalDescriptionMissing(Exception):
    """No description specified for merge proposal."""


class PublishResult(object):
    """A object describing the result of a publish action."""

    def __init__(self, mode: str,
                 proposal: Optional[MergeProposal] = None,
                 is_new: bool = False) -> None:
        self.mode = mode
        self.proposal = proposal
        self.is_new = is_new

    def __tuple__(self) -> Tuple[Optional[MergeProposal], bool]:
        # Backwards compatibility
        return (self.proposal, self.is_new)


class Workspace(object):
    """Workspace for creating changes to a branch.

    main_branch: The upstream branch
    resume_branch: Optional in-progress branch that we previously made changes
        on, and should ideally continue from.
    cached_branch: Branch to copy revisions from, if possible.
    local_tree: The tree the user can work in
    """

    _destroy: Optional[Callable[[], None]]
    local_tree: WorkingTree
    main_branch_revid: Optional[bytes]

    def __init__(self, main_branch: Branch,
                 resume_branch: Optional[Branch] = None,
                 cached_branch: Optional[Branch] = None,
                 additional_colocated_branches: Optional[List[str]] = None,
                 dir: Optional[str] = None,
                 path: Optional[str] = None) -> None:
        self.main_branch = main_branch
        self.main_branch_revid = None
        self.cached_branch = cached_branch
        self.resume_branch = resume_branch
        self.additional_colocated_branches = (
            additional_colocated_branches or [])
        self._destroy = None
        self._dir = dir
        self._path = path

    def __str__(self):
        if self._path is None:
            return "Workspace for %s" % full_branch_url(self.main_branch)
        else:
            return "Workspace for %s at %s" % (
                full_branch_url(self.main_branch), self._path)

    def __repr__(self):
        return (
            "%s(%r, resume_branch=%r, cached_branch=%r, "
            "additional_colocated_branches=%r, dir=%r, path=%r)" % (
                type(self).__name__, self.main_branch, self.resume_branch,
                self.cached_branch, self.additional_colocated_branches,
                self._dir, self._path))

    def __enter__(self) -> Any:
        self.local_tree, self._destroy = create_temp_sprout(
            self.cached_branch or self.resume_branch or self.main_branch,
            self.additional_colocated_branches,
            dir=self._dir, path=self._path)
        self.main_branch_revid = self.main_branch.last_revision()
        self.refreshed = False
        with self.local_tree.branch.lock_write():
            if self.cached_branch:
                self.local_tree.pull(
                    self.resume_branch or self.main_branch, overwrite=True)
            if self.resume_branch:
                try:
                    self.local_tree.pull(self.main_branch, overwrite=False)
                except DivergedBranches:
                    pass
                for branch_name in self.additional_colocated_branches:
                    try:
                        remote_colo_branch = (
                            self.main_branch.controldir.open_branch(
                                name=branch_name))
                    except (NotBranchError,
                            NoColocatedBranchSupport):
                        continue
                    self.local_tree.branch.controldir.push_branch(
                        name=branch_name, source=remote_colo_branch,
                        overwrite=True)
                if merge_conflicts(self.main_branch, self.local_tree.branch):
                    note('restarting branch')
                    self.local_tree.update(revision=self.main_branch_revid)
                    self.local_tree.branch.generate_revision_history(
                        self.main_branch_revid)
                    self.resume_branch = None
                    self.refreshed = True
            self.orig_revid = self.local_tree.last_revision()
        return self

    def defer_destroy(self) -> Optional[Callable[[], None]]:
        ret = self._destroy
        self._destroy = None
        return ret

    def changes_since_main(self) -> bool:
        return self.local_tree.branch.last_revision() != self.main_branch_revid

    def changes_since_resume(self) -> bool:
        return self.orig_revid != self.local_tree.branch.last_revision()

    def push(self, hoster: Optional[Hoster] = None, dry_run: bool = False,
             tags: Optional[Union[Dict[str, bytes], List[str]]] = None,
             stop_revision: Optional[bytes] = None) -> None:
        if hoster is None:
            hoster = get_hoster(self.main_branch)
        return push_changes(
            self.local_tree.branch, self.main_branch, hoster=hoster,
            additional_colocated_branches=self.additional_colocated_branches,
            dry_run=dry_run, tags=tags, stop_revision=stop_revision)

    def propose(self, name: str, description: str,
                hoster: Optional[Hoster] = None,
                existing_proposal: Optional[MergeProposal] = None,
                overwrite_existing: Optional[bool] = None,
                labels: Optional[List[str]] = None,
                dry_run: bool = False,
                commit_message: Optional[str] = None,
                reviewers: Optional[List[str]] = None,
                tags: Optional[Union[Dict[str, bytes], List[str]]] = None,
                owner: Optional[str] = None,
                allow_collaboration: bool = False,
                stop_revision: Optional[bytes] = None) -> MergeProposal:
        if hoster is None:
            hoster = get_hoster(self.main_branch)
        return propose_changes(
            self.local_tree.branch, self.main_branch, hoster=hoster, name=name,
            mp_description=description, resume_branch=self.resume_branch,
            resume_proposal=existing_proposal,
            overwrite_existing=(overwrite_existing or False), labels=labels,
            dry_run=dry_run, commit_message=commit_message,
            reviewers=reviewers, owner=owner,
            additional_colocated_branches=self.additional_colocated_branches,
            tags=tags, allow_collaboration=allow_collaboration,
            stop_revision=stop_revision)

    def push_derived(self,
                     name: str, hoster: Optional[Hoster] = None,
                     overwrite_existing: Optional[bool] = False,
                     owner: Optional[str] = None,
                     tags: Optional[Union[Dict[str, bytes], List[str]]] = None,
                     stop_revision: Optional[bytes] = None
                     ) -> Tuple[Branch, str]:
        """Push a derived branch.

        Args:
          name: Branch name
          hoster: Optional hoster to use
          overwrite_existing: Whether to overwrite an existing branch
          tags: Tags list to push
          owner: Owner name
        Returns:
          tuple with remote_branch and public_branch_url
        """
        if hoster is None:
            hoster = get_hoster(self.main_branch)
        return push_derived_changes(
            self.local_tree.branch,
            self.main_branch, hoster, name,
            overwrite_existing=overwrite_existing,
            owner=owner, tags=tags, stop_revision=stop_revision)

    def orig_tree(self) -> Tree:
        return self.local_tree.branch.repository.revision_tree(self.orig_revid)

    def show_diff(self, outf: BinaryIO,
                  old_label: str = 'old/', new_label: str = 'new/') -> None:
        orig_tree = self.orig_tree()
        show_diff_trees(
            orig_tree, self.local_tree.basis_tree(), outf,
            old_label=old_label, new_label=new_label)

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._destroy:
            self._destroy()
            self._destroy = None
        return False


def publish_changes(
        ws: Workspace, mode: str, name: str,
        get_proposal_description: Callable[
            [str, Optional[MergeProposal]], Optional[str]],
        get_proposal_commit_message: Callable[
            [Optional[MergeProposal]], Optional[str]] = None,
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
        stop_revision: Optional[bytes] = None) -> PublishResult:
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

    if not ws.changes_since_main():
        if existing_proposal is not None:
            note('closing existing merge proposal - no new revisions')
            existing_proposal.close()
        return PublishResult(mode)

    if not ws.changes_since_resume():
        # No new revisions added on this iteration, but changes since main
        # branch. We may not have gotten round to updating/creating the
        # merge proposal last time.
        note('No changes added; making sure merge proposal is up to date.')

    if hoster is None:
        hoster = get_hoster(ws.main_branch)

    if mode == 'push-derived':
        (remote_branch, public_url) = ws.push_derived(
            name=name, overwrite_existing=overwrite_existing,
            tags=tags, owner=derived_owner, stop_revision=stop_revision)
        return PublishResult(mode)

    if mode in ('push', 'attempt-push'):
        try:
            ws.push(hoster, dry_run=dry_run, tags=tags,
                    stop_revision=stop_revision)
        except PermissionDenied:
            if mode == 'attempt-push':
                note('push access denied, falling back to propose')
                mode = 'propose'
            else:
                note('permission denied during push')
                raise
        else:
            return PublishResult(mode=mode)

    assert mode == 'propose'
    if not ws.resume_branch and not allow_create_proposal:
        # TODO(jelmer): Raise an exception of some sort here?
        return PublishResult(mode)

    mp_description = get_proposal_description(
        getattr(hoster, 'merge_proposal_description_format', 'plain'),
        existing_proposal if ws.resume_branch else None)
    if get_proposal_commit_message is not None:
        commit_message = get_proposal_commit_message(
            existing_proposal if ws.resume_branch else None)
    if not mp_description:
        raise MergeProposalDescriptionMissing()
    (proposal, is_new) = ws.propose(
        name, mp_description, hoster=hoster,
        existing_proposal=existing_proposal,
        labels=labels, dry_run=dry_run, overwrite_existing=overwrite_existing,
        commit_message=commit_message, reviewers=reviewers,
        tags=tags, allow_collaboration=allow_collaboration,
        owner=derived_owner, stop_revision=stop_revision)

    return PublishResult(mode, proposal, is_new)
