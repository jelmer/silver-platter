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
from typing import (
    Optional,
    Callable,
    List,
    Union,
    Dict,
    BinaryIO,
    Any,
    Tuple,
)

from breezy.branch import Branch
from breezy.controldir import ControlDir
from breezy.controldir import NoColocatedBranchSupport
from breezy.tree import Tree
from breezy.workingtree import WorkingTree
from breezy.diff import show_diff_trees
from breezy.errors import (
    DivergedBranches,
    NotBranchError,
)
from .proposal import (
    get_forge,
    Forge,
    MergeProposal,
    UnsupportedForge,
)

from breezy.transport.local import LocalTransport

from .publish import (
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


def fetch_colocated(controldir: ControlDir, from_controldir: ControlDir,
                    additional_colocated_branches: Dict[str, str]):
    logger.debug(
        "Fetching colocated branches: %r",
        additional_colocated_branches,
    )
    for (from_branch_name,
         to_branch_name) in additional_colocated_branches.items():
        try:
            remote_colo_branch = from_controldir.open_branch(
                name=from_branch_name
            )
        except (NotBranchError, NoColocatedBranchSupport):
            continue
        controldir.push_branch(
            name=to_branch_name, source=remote_colo_branch, overwrite=True
        )


class Workspace(object):
    """Workspace for creating changes to a branch.

    Args:
        main_branch: The upstream branch
        resume_branch: Optional in-progress branch that we previously made
            changes on, and should ideally continue from.
        resume_branch_additional_colocated_branches:
            Additional list of colocated branches to fetch
        cached_branch: Branch to copy revisions from, if possible.
        local_tree: The tree the user can work in
    """

    _destroy: Optional[Callable[[], None]]
    local_tree: WorkingTree
    main_branch_revid: Optional[bytes]
    main_colo_revid: Dict[Optional[str], bytes]
    additional_colocated_branches: Dict[str, str]
    resume_branch_additional_colocated_branches: Optional[Dict[str, str]]

    @classmethod
    def from_url(cls, url, **kwargs):
        return cls(main_branch=Branch.open(url), **kwargs)

    def __init__(
        self,
        main_branch: Branch,
        resume_branch: Optional[Branch] = None,
        cached_branch: Optional[Branch] = None,
        additional_colocated_branches:
            Optional[Union[List[str], Dict[str, str]]] = None,
        resume_branch_additional_colocated_branches:
            Optional[Union[List[str], Dict[str, str]]] = None,
        dir: Optional[str] = None,
        path: Optional[str] = None,
    ) -> None:
        self.main_branch = main_branch
        self.main_branch_revid = None
        self.refreshed = False
        self.cached_branch = cached_branch
        self.resume_branch = resume_branch
        if additional_colocated_branches is None:
            additional_colocated_branches = {}
        elif isinstance(additional_colocated_branches, list):
            additional_colocated_branches = {
                k: k for k in additional_colocated_branches}
        self.additional_colocated_branches = additional_colocated_branches
        if isinstance(resume_branch_additional_colocated_branches, list):
            resume_branch_additional_colocated_branches = {
                k: k for k in resume_branch_additional_colocated_branches}
        self.resume_branch_additional_colocated_branches = (
            resume_branch_additional_colocated_branches)
        self._destroy = None
        self._dir = dir
        self._path = path

    @property
    def path(self):
        return self.local_tree.abspath('.')

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

    def _inverse_additional_colocated_branches(self):
        return {
            to_name: from_name
            for from_name, to_name in
            self.additional_colocated_branches.items()}

    def __enter__(self) -> Any:
        for (sprout_base, sprout_coloc) in [
                (self.cached_branch, self.additional_colocated_branches),
                (self.resume_branch,
                    self.resume_branch_additional_colocated_branches),
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
        self.main_colo_revid = {}
        for from_name, to_name in self.additional_colocated_branches.items():
            try:
                branch = self.main_branch.controldir.open_branch(
                    name=from_name)  # type: ignore
            except (NotBranchError, NoColocatedBranchSupport):
                continue
            self.main_colo_revid[to_name] = branch.last_revision()

        self.refreshed = False
        if self.cached_branch:
            logger.debug(
                "Pulling in missing revisions from resume/main branch %r",
                self.resume_branch or self.main_branch,
            )
            self.local_tree.pull(
                self.resume_branch or self.main_branch, overwrite=True
            )
        # At this point, we're either on the tip of the main branch or the tip
        # of the resume branch
        if self.resume_branch:
            # If there's a resume branch at play, make sure it's derived from
            # the main branch *or* reset back to the main branch.
            logger.debug(
                "Pulling in missing revisions from main branch %r",
                self.main_branch
            )
            try:
                self.local_tree.pull(self.main_branch, overwrite=False)
            except DivergedBranches:
                logger.info("restarting branch")
                self.refreshed = True
                self.local_tree.pull(self.main_branch, overwrite=True)
                fetch_colocated(
                    self.local_tree.branch.controldir,
                    self.main_branch.controldir,
                    self.additional_colocated_branches)
            else:
                fetch_colocated(
                    self.local_tree.branch.controldir,
                    self.main_branch.controldir,
                    self.additional_colocated_branches)
                if self.resume_branch_additional_colocated_branches:
                    fetch_colocated(
                        self.local_tree.branch.controldir,
                        self.resume_branch.controldir,
                        self.resume_branch_additional_colocated_branches)
                    self.additional_colocated_branches.update(
                        self.resume_branch_additional_colocated_branches)
        else:
            fetch_colocated(
                self.local_tree.branch.controldir,
                self.main_branch.controldir,
                self.additional_colocated_branches)
        self.base_revid = self.local_tree.last_revision()
        return self

    def defer_destroy(self) -> Optional[Callable[[], None]]:
        ret = self._destroy
        self._destroy = None
        return ret

    def changes_since_main(self) -> bool:
        return self.local_tree.branch.last_revision() != self.main_branch_revid

    def changes_since_base(self) -> bool:
        return self.base_revid != self.local_tree.branch.last_revision()

    def any_branch_changes(self):
        """Have any branch changes at all been made?

        Includes changes that already existed in the resume branch.
        """
        for name, br, r in self.result_branches():
            if br != r:
                return True
        return False

    def result_branches(self) -> List[
            Tuple[Optional[str], Optional[bytes], Optional[bytes]]]:
        branches = [
            (self.main_branch.name, self.main_branch_revid,  # type: ignore
             self.local_tree.last_revision())]
        for from_name, to_name in self.additional_colocated_branches.items():
            to_revision: Optional[bytes]
            try:
                to_branch = self.local_tree.controldir.open_branch(
                    name=to_name)
            except NoColocatedBranchSupport:
                continue
            except NotBranchError:
                to_revision = None
            else:
                to_revision = to_branch.last_revision()
            from_revision = self.main_colo_revid.get(from_name)
            if from_revision is None and to_revision is None:
                continue
            branches.append((from_name, from_revision, to_revision))
        return branches

    def push(
        self,
        forge: Optional[Forge] = None,
        dry_run: bool = False,
        tags: Optional[Union[Dict[str, bytes], List[str]]] = None,
        stop_revision: Optional[bytes] = None,
    ) -> None:
        if forge is None:
            try:
                forge = get_forge(self.main_branch)
            except UnsupportedForge:
                if not isinstance(
                        self.main_branch.control_transport, LocalTransport):
                    logging.warning(
                        'Unable to find forge for %s to determine push url, '
                        'trying anyway.', self.main_branch.user_url)
                forge = None
        return push_changes(
            self.local_tree.branch,
            self.main_branch,
            forge=forge,
            additional_colocated_branches=(
                self._inverse_additional_colocated_branches()),
            dry_run=dry_run,
            tags=tags,
            stop_revision=stop_revision,
        )

    def propose(
        self,
        name: str,
        description: str,
        forge: Optional[Forge] = None,
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
    ) -> Tuple[MergeProposal, bool]:
        if forge is None:
            forge = get_forge(self.main_branch)
        return propose_changes(
            self.local_tree.branch,
            self.main_branch,
            forge=forge,
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
            additional_colocated_branches=(
                self._inverse_additional_colocated_branches()),
            tags=tags,
            allow_collaboration=allow_collaboration,
            stop_revision=stop_revision,
        )

    def push_derived(
        self,
        name: str,
        forge: Optional[Forge] = None,
        overwrite_existing: Optional[bool] = False,
        owner: Optional[str] = None,
        tags: Optional[Union[Dict[str, bytes], List[str]]] = None,
        stop_revision: Optional[bytes] = None,
    ) -> Tuple[Branch, str]:
        """Push a derived branch.

        Args:
          name: Branch name
          forge: Optional forge to use
          overwrite_existing: Whether to overwrite an existing branch
          tags: Tags list to push
          owner: Owner name
        Returns:
          tuple with remote_branch and public_branch_url
        """
        if forge is None:
            forge = get_forge(self.main_branch)
        return push_derived_changes(
            self.local_tree.branch,
            self.main_branch,
            forge,
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

    def base_tree(self) -> Tree:
        return self.local_tree.branch.repository.revision_tree(self.base_revid)

    def show_diff(
        self, outf: BinaryIO, old_label: str = "old/", new_label: str = "new/"
    ) -> None:
        base_tree = self.base_tree()
        show_diff_trees(
            base_tree,
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
