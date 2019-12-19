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

from __future__ import absolute_import

from breezy.trace import note

from . import (
    get_hoster,
    open_packaging_branch,
    NoSuchPackage,
    )
from ..proposal import (
    find_existing_proposed,
    )
from ..utils import (
    BranchMissing,
    BranchUnavailable,
    BranchUnsupported,
    )


def iter_packages(packages, branch_name, overwrite_unrelated=False,
                  refresh=False):
    from breezy.plugins.propose.propose import (
        UnsupportedHoster,
        )

    possible_transports = []
    possible_hosters = []

    for pkg in packages:
        note('Processing: %s', pkg)

        try:
            main_branch = open_packaging_branch(
                pkg, possible_transports=possible_transports)
        except NoSuchPackage:
            note('%s: no such package', pkg)
            continue
        except (BranchMissing, BranchUnavailable, BranchUnsupported) as e:
            note('%s: ignoring: %s', pkg, e)
            continue

        overwrite = False

        try:
            hoster = get_hoster(main_branch, possible_hosters=possible_hosters)
        except UnsupportedHoster:
            # We can't figure out what branch to resume from when there's no
            # hoster that can tell us.
            resume_branch = None
            existing_proposal = None
            hoster = None
        else:
            (resume_branch, overwrite, existing_proposal) = (
                find_existing_proposed(
                    main_branch, hoster, branch_name,
                    overwrite_unrelated=overwrite_unrelated))
        if refresh:
            overwrite = True
            resume_branch = None

        yield (pkg, main_branch, resume_branch, hoster, existing_proposal,
               overwrite)
