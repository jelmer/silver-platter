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

from typing import List, Optional

from breezy import errors
from breezy import merge as _mod_merge  # type: ignore
from breezy import revision as _mod_revision
from breezy.branch import Branch
from breezy.errors import PermissionDenied
from breezy.forge import (
    NoSuchProject,
    SourceNotDerivedFromTarget,
    UnsupportedForge,
    MergeProposal,
    MergeProposalExists,
)
from breezy.memorybranch import MemoryBranch
from breezy.revision import RevisionID

from . import _svp_rs

__all__ = [
    "push_changes",
    "push_derived_changes",
    "propose_changes",
    "EmptyMergeProposal",
    "check_proposal_diff",
    "find_existing_proposed",
    "NoSuchProject",
    "PermissionDenied",
    "UnsupportedForge",
    "SourceNotDerivedFromTarget",
    "MergeProposal",
    "MergeProposalExists",
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


push_result = _svp_rs.push_result
push_changes = _svp_rs.push_changes
push_derived_changes = _svp_rs.push_derived_changes
propose_changes = _svp_rs.propose_changes
publish_changes = _svp_rs.publish_changes
PublishResult = _svp_rs.PublishResult
InsufficientChangesForNewProposal = _svp_rs.InsufficientChangesForNewProposal


class EmptyMergeProposal(Exception):
    """Merge proposal does not have any changes."""

    def __init__(self, local_branch: Branch, main_branch: Branch) -> None:
        self.local_branch = local_branch
        self.main_branch = main_branch


def check_proposal_diff(
    other_branch: Branch, main_branch: Branch,
    stop_revision: Optional[RevisionID] = None
) -> None:
    if stop_revision is None:
        stop_revision = other_branch.last_revision()
    main_revid = main_branch.last_revision()
    other_branch.repository.fetch(main_branch.repository, main_revid)
    with other_branch.lock_read():
        main_tree = other_branch.repository.revision_tree(main_revid)
        revision_graph = other_branch.repository.get_graph()
        tree_branch = MemoryBranch(
            other_branch.repository, (None, main_revid), None)
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


find_existing_proposed = _svp_rs.find_existing_proposed


def merge_conflicts(
    main_branch: Branch, other_branch: Branch,
    other_revision: Optional[RevisionID] = None
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
        _mod_merge.Merger.hooks["merge_file_content"] = (
            old_file_content_mergers)
