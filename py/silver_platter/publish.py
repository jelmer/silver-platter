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

from typing import List

from breezy.errors import PermissionDenied
from breezy.forge import (
    MergeProposal,
    MergeProposalExists,
    NoSuchProject,
    SourceNotDerivedFromTarget,
    UnsupportedForge,
)

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
check_proposal_diff = _svp_rs.check_proposal_diff
EmptyMergeProposal = _svp_rs.EmptyMergeProposal
find_existing_proposed = _svp_rs.find_existing_proposed
merge_conflicts = _svp_rs.merge_conflicts
