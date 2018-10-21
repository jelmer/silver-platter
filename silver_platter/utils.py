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
import tempfile


class TemporarySprout(object):
    """Create a temporary sprout of a branch."""

    def __init__(self, branch):
        self.branch = branch

    def __enter__(self):
        self._td = tempfile.mkdtemp()
        try:
            # preserve whatever source format we have.
            to_dir = self.branch.controldir.sprout(
                self._td, None, create_tree_if_local=True,
                source_branch=self.branch,
                stacked=self.branch._format.supports_stacking())
            return to_dir.open_workingtree()
        except BaseException as e:
            shutil.rmtree(self._td)
            raise e

    def __exit__(self, exc_type, exc_val, exc_tb):
        shutil.rmtree(self._td)
        return False
