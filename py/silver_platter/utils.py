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

__all__ = [
    "create_temp_sprout",
    "open_branch",
    "open_branch_containing",
    "full_branch_url",
    "BranchUnavailable",
    "BranchTemporarilyUnavailable",
    "BranchRateLimited",
    "BranchMissing",
    "BranchUnsupported",
]


from typing import Optional

from . import _svp_rs

create_temp_sprout = _svp_rs.create_temp_sprout


open_branch = _svp_rs.open_branch
open_branch_containing = _svp_rs.open_branch_containing
full_branch_url = _svp_rs.full_branch_url


class BranchUnavailable(Exception):
    """Opening branch failed."""

    def __init__(self, url: str, description: str) -> None:
        self.url = url
        self.description = description

    def __str__(self) -> str:
        return self.description


class BranchTemporarilyUnavailable(BranchUnavailable):
    """Branch unavailable for temporary reasons, e.g. DNS failed."""


class BranchRateLimited(Exception):
    """Opening branch was rate-limited."""

    def __init__(
        self, url: str, description: str, retry_after: Optional[int] = None
    ) -> None:
        self.url = url
        self.description = description
        self.retry_after = retry_after

    def __str__(self) -> str:
        if self.retry_after is not None:
            return f"{self.description} (retry after {self.retry_after})"
        else:
            return self.description


class BranchMissing(Exception):
    """Branch did not exist."""

    def __init__(self, url: str, description: str) -> None:
        self.url = url
        self.description = description

    def __str__(self) -> str:
        return self.description


class BranchUnsupported(Exception):
    """The branch uses a VCS or protocol that is unsupported."""

    def __init__(
        self, url: str, description: str, vcs: Optional[str] = None
    ) -> None:
        self.url = url
        self.description = description
        self.vcs = vcs

    def __str__(self) -> str:
        return self.description
