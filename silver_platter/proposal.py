#!/usr/bin/python
# Copyright (C) 2018 Jelmer Vernooij <jelmer@jelmer.uk>
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


import breezy.plugins.github  # noqa: F401
import breezy.plugins.gitlab  # noqa: F401
import breezy.plugins.launchpad  # noqa: F401
from breezy.errors import PermissionDenied
from breezy.forge import (
    ForgeLoginRequired,
    NoSuchProject,
    SourceNotDerivedFromTarget,
    UnsupportedForge,
    forges,
    get_forge,
)

from .publish import (
    SUPPORTED_MODES,
    EmptyMergeProposal,
    check_proposal_diff,
    find_existing_proposed,
    propose_changes,
    push_changes,
    push_derived_changes,
)

__all__ = [
    "ForgeLoginRequired",
    "UnsupportedForge",
    "PermissionDenied",
    "NoSuchProject",
    "get_forge",
    "forges",
    "push_changes",
    "SUPPORTED_MODES",
    "push_derived_changes",
    "propose_changes",
    "check_proposal_diff",
    "EmptyMergeProposal",
    "find_existing_proposed",
    "SourceNotDerivedFromTarget",
]
