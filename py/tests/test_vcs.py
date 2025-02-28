#!/usr/bin/python
# Copyright (C) 2024 Jelmer Vernooij
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

import os

from breezy.tests import TestCaseWithTransport

from silver_platter import BranchMissing, _open_branch


class OpenBranchTests(TestCaseWithTransport):
    def test_simple(self):
        a = self.make_branch("target")
        b = _open_branch(a.base)
        self.assertEqual(a.base, b.base)

    def test_missing(self):
        url = f"file://{os.getcwd()}/nonexistent"
        e = self.assertRaises(BranchMissing, _open_branch, url)
        self.assertIsInstance(e.url, str)
        self.assertIsInstance(e.message, str)
        self.assertEqual(e.url, url)
