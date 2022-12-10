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

from contextlib import suppress
from typing import (
    List,
    Optional,
    Tuple,
    Iterator,
)

from breezy.branch import Branch
from breezy.errors import (
    PermissionDenied,
)
from breezy.forge import (
    ForgeLoginRequired,
    UnsupportedForge,
    get_forge,
    forges,
    Forge,
    MergeProposal,
    NoSuchProject,
    iter_forge_instances,
    SourceNotDerivedFromTarget,
    )

from breezy.merge_directive import (
    MergeDirective,
    MergeDirective2,
)
from breezy.transport import Transport

import breezy.plugins.gitlab  # noqa: F401
import breezy.plugins.github  # noqa: F401
import breezy.plugins.launchpad  # noqa: F401


from .utils import (
    open_branch,
    full_branch_url,
)
from .publish import (
    push_changes,
    push_derived_changes,
    propose_changes,
    EmptyMergeProposal,
    check_proposal_diff,
    DryRunProposal,
    find_existing_proposed,
    SUPPORTED_MODES,
)


__all__ = [
    "ForgeLoginRequired",
    "UnsupportedForge",
    "PermissionDenied",
    "NoSuchProject",
    "get_forge",
    "forges",
    "iter_all_mps",
    "push_changes",
    "SUPPORTED_MODES",
    "push_derived_changes",
    "propose_changes",
    "DryRunProposal",
    "check_proposal_diff",
    "EmptyMergeProposal",
    "find_existing_proposed",
]

if SourceNotDerivedFromTarget is not None:
    __all__.append("SourceNotDerivedFromTarget")


def enable_tag_pushing(branch: Branch) -> None:
    stack = branch.get_config()
    stack.set_user_option("branch.fetch_tags", True)


def merge_directive_changes(
    local_branch: Branch,
    main_branch: Branch,
    forge: Forge,
    name: str,
    message: str,
    include_patch: bool = False,
    include_bundle: bool = False,
    overwrite_existing: bool = False,
) -> MergeDirective:
    from breezy import osutils
    import time

    remote_branch, public_branch_url = forge.publish_derived(
        local_branch, main_branch, name=name, overwrite=overwrite_existing
    )
    public_branch = open_branch(public_branch_url)
    return MergeDirective2.from_objects(
        local_branch.repository,
        local_branch.last_revision(),
        time.time(),
        osutils.local_time_offset(),
        main_branch,
        public_branch=public_branch,
        include_patch=include_patch,
        include_bundle=include_bundle,
        message=message,
        base_revision_id=main_branch.last_revision(),
    )


def iter_all_mps(
    statuses: Optional[List[str]] = None,
) -> Iterator[Tuple[Forge, MergeProposal, str]]:
    """iterate over all existing merge proposals."""
    if statuses is None:
        statuses = ["open", "merged", "closed"]
    for instance in iter_forge_instances():
        for status in statuses:
            with suppress(ForgeLoginRequired):
                for mp in instance.iter_my_proposals(status=status):
                    yield instance, mp, status


def iter_conflicted(
    branch_name: str,
) -> Iterator[Tuple[str, Branch, str, Branch, Forge, MergeProposal, bool]]:
    """Find conflicted branches owned by the current user.

    Args:
      branch_name: Branch name to search for
    """
    possible_transports: List[Transport] = []
    for forge, mp, _status in iter_all_mps(["open"]):
        try:
            if mp.can_be_merged():
                continue
        except (NotImplementedError, AttributeError):
            # TODO(jelmer): Check some other way that the branch is conflicted?
            continue
        main_branch = open_branch(
            mp.get_target_branch_url(), possible_transports=possible_transports
        )
        resume_branch = open_branch(
            mp.get_source_branch_url(), possible_transports=possible_transports
        )
        if resume_branch.name != branch_name and not (  # type: ignore
            not resume_branch.name
            and resume_branch.user_url.endswith(branch_name)  # type: ignore
        ):
            continue
        # TODO(jelmer): Find out somehow whether we need to modify a subpath?
        subpath = ""
        yield (
            full_branch_url(resume_branch),
            main_branch,
            subpath,
            resume_branch,
            forge,
            mp,
            True,
        )
