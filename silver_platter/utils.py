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

import shutil

from breezy import errors, osutils


class TemporarySprout(object):
    """Create a temporary sprout of a branch.

    This attempts to fetch the least amount of history as possible.
    """

    def __init__(self, branch, additional_colocated_branches=None, dir=None):
        self.branch = branch
        self.additional_colocated_branches = additional_colocated_branches
        self.dir = dir

    def __enter__(self):
        self._td = osutils.mkdtemp(dir=self.dir)
        try:
            # preserve whatever source format we have.
            to_dir = self.branch.controldir.sprout(
                self._td, None, create_tree_if_local=True,
                source_branch=self.branch,
                stacked=self.branch._format.supports_stacking())
            # TODO(jelmer): Fetch these during the initial clone
            for branch_name in self.additional_colocated_branches or []:
                try:
                    add_branch = self.branch.controldir.open_branch(
                        name=branch_name)
                except (errors.NotBranchError,
                        errors.NoColocatedBranchSupport):
                    pass
                else:
                    local_add_branch = to_dir.create_branch(
                            name=branch_name)
                    add_branch.push(local_add_branch)
                    assert (add_branch.last_revision() ==
                            local_add_branch.last_revision())
            return to_dir.open_workingtree()
        except BaseException as e:
            shutil.rmtree(self._td)
            raise e

    def __exit__(self, exc_type, exc_val, exc_tb):
        shutil.rmtree(self._td)
        return False
