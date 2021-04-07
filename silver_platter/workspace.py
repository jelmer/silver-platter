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

import logging
from typing import Optional, Callable, List, Union, Dict, BinaryIO, Any, Tuple

from breezy.branch import Branch
from breezy.tree import Tree
from breezy.workingtree import WorkingTree
from breezy.diff import show_diff_trees
from breezy.errors import (
    DivergedBranches,
    NotBranchError,
    NoColocatedBranchSupport,
)
from breezy.propose import (
    get_hoster,
    Hoster,
    MergeProposal,
    UnsupportedHoster,
)

from breezy.transport.local import LocalTransport

from .publish import (
    merge_conflicts,
    propose_changes,
    push_changes,
    push_derived_changes,
    publish_changes as _publish_changes,
    PublishResult,
)
from .utils import (
    create_temp_sprout,
    full_branch_url,
)


__all__ = [
    "Workspace",
]


logger = logging.getLogger(__name__)


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

    def __init__(
        self,
        main_branch: Branch,
        resume_branch: Optional[Branch] = None,
        cached_branch: Optional[Branch] = None,
        additional_colocated_branches: Optional[List[str]] = None,
        resume_branch_additional_colocated_branches: Optional[List[str]] = None,
        dir: Optional[str] = None,
        path: Optional[str] = None,
    ) -> None:
        self.main_branch = main_branch
        self.main_branch_revid = None
        self.cached_branch = cached_branch
        self.resume_branch = resume_branch
        self.additional_colocated_branches = additional_colocated_branches or []
        self.resume_branch_additional_colocated_branches = resume_branch_additional_colocated_branches
        self._destroy = None
        self._dir = dir
        self._path = path

    def __str__(self):
        if self._path is None:
            return "Workspace for %s" % full_branch_url(self.main_branch)
        else:
            return "Workspace for %s at %s" % (
                full_branch_url(self.main_branch),
                self._path,
            )

    def __repr__(self):
        return (
            "%s(%r, resume_branch=%r, cached_branch=%r, "
            "additional_colocated_branches=%r, "
            "resume_branch_additional_colocated_branches=%r, dir=%r, path=%r)"
            % (
                type(self).__name__,
                self.main_branch,
                self.resume_branch,
                self.cached_branch,
                self.additional_colocated_branches,
                self.resume_branch_additional_colocated_branches,
                self._dir,
                self._path,
            )
        )

    def __enter__(self) -> Any:
        for (sprout_base, sprout_coloc) in [
                (self.cached_branch, self.additional_colocated_branches),
                (self.resume_branch, self.resume_branch_additional_colocated_branches),
                (self.main_branch, self.additional_colocated_branches)]:
            if sprout_base:
                break
        else:
            raise ValueError('main branch needs to be specified')
        logger.debug("Creating sprout from %r", sprout_base)
        self.local_tree, self._destroy = create_temp_sprout(
            sprout_base,
            sprout_coloc,
            dir=self._dir,
            path=self._path,
        )
        self.main_branch_revid = self.main_branch.last_revision()
        self.refreshed = False
        with self.local_tree.branch.lock_write():
            if self.cached_branch:
                logger.debug(
                    "Pulling in missing revisions from resume/main branch %r",
                    self.resume_branch or self.main_branch,
                )
                self.local_tree.pull(
                    self.resume_branch or self.main_branch, overwrite=True
                )
            if self.resume_branch:
                logger.debug(
                    "Pulling in missing revisions from main branch %r", self.main_branch
                )
                try:
                    self.local_tree.pull(self.main_branch, overwrite=False)
                except DivergedBranches:
                    pass
                logger.debug(
                    "Fetching colocated branches: %r",
                    self.additional_colocated_branches,
                )
                for branch_name in self.resume_branch_additional_colocated_branches or []:
                    try:
                        remote_colo_branch = self.main_branch.controldir.open_branch(
                            name=branch_name
                        )
                    except (NotBranchError, NoColocatedBranchSupport):
                        continue
                    self.local_tree.branch.controldir.push_branch(
                        name=branch_name, source=remote_colo_branch, overwrite=True
                    )
                if merge_conflicts(self.main_branch, self.local_tree.branch):
                    logger.info("restarting branch")
                    self.local_tree.update(revision=self.main_branch_revid)
                    self.local_tree.branch.generate_revision_history(
                        self.main_branch_revid
                    )
                    self.resume_branch = None
                    self.resume_branch_additional_colocated_branches = None
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

    def push(
        self,
        hoster: Optional[Hoster] = None,
        dry_run: bool = False,
        tags: Optional[Union[Dict[str, bytes], List[str]]] = None,
        stop_revision: Optional[bytes] = None,
    ) -> None:
        if hoster is None:
            try:
                hoster = get_hoster(self.main_branch)
            except UnsupportedHoster:
                if isinstance(self.main_branch.control_transport, LocalTransport):
                    hoster = None
                else:
                    raise
        return push_changes(
            self.local_tree.branch,
            self.main_branch,
            hoster=hoster,
            additional_colocated_branches=self.additional_colocated_branches,
            dry_run=dry_run,
            tags=tags,
            stop_revision=stop_revision,
        )

    def propose(
        self,
        name: str,
        description: str,
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
        stop_revision: Optional[bytes] = None,
    ) -> MergeProposal:
        if hoster is None:
            hoster = get_hoster(self.main_branch)
        return propose_changes(
            self.local_tree.branch,
            self.main_branch,
            hoster=hoster,
            name=name,
            mp_description=description,
            resume_branch=self.resume_branch,
            resume_proposal=existing_proposal,
            overwrite_existing=(overwrite_existing or False),
            labels=labels,
            dry_run=dry_run,
            commit_message=commit_message,
            reviewers=reviewers,
            owner=owner,
            additional_colocated_branches=self.additional_colocated_branches,
            tags=tags,
            allow_collaboration=allow_collaboration,
            stop_revision=stop_revision,
        )

    def push_derived(
        self,
        name: str,
        hoster: Optional[Hoster] = None,
        overwrite_existing: Optional[bool] = False,
        owner: Optional[str] = None,
        tags: Optional[Union[Dict[str, bytes], List[str]]] = None,
        stop_revision: Optional[bytes] = None,
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
            self.main_branch,
            hoster,
            name,
            overwrite_existing=overwrite_existing,
            owner=owner,
            tags=tags,
            stop_revision=stop_revision,
        )

    def publish_changes(self, *args, **kwargs) -> PublishResult:
        """Publish a set of changes."""
        return _publish_changes(
            self.local_tree.branch,
            self.main_branch,
            self.resume_branch,
            *args,
            **kwargs
        )

    def orig_tree(self) -> Tree:
        return self.local_tree.branch.repository.revision_tree(self.orig_revid)

    def show_diff(
        self, outf: BinaryIO, old_label: str = "old/", new_label: str = "new/"
    ) -> None:
        orig_tree = self.orig_tree()
        show_diff_trees(
            orig_tree,
            self.local_tree.basis_tree(),
            outf,
            old_label=old_label,
            new_label=new_label,
        )

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._destroy:
            self._destroy()
            self._destroy = None
        return False
