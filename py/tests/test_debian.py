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


from breezy.tests import TestCaseWithTransport

from silver_platter import debian  # type: ignore

pick_additional_colocated_branches = debian.pick_additional_colocated_branches


class AdditionalColocatedBranchesTests(TestCaseWithTransport):
    def test_none(self):
        a = self.make_branch("target")
        self.assertEqual(
            pick_additional_colocated_branches(a),
            {},
        )

    def test_upstream(self):
        a = self.make_branch("target")
        a.controldir.create_branch("upstream")
        self.assertEqual(
            pick_additional_colocated_branches(a),
            {"upstream": "upstream"},
        )

    def test_pristine_tar(self):
        a = self.make_branch("target")
        a.controldir.create_branch("pristine-tar")
        self.assertEqual(
            pick_additional_colocated_branches(a),
            {"pristine-tar": "pristine-tar"},
        )
