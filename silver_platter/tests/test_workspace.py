#!/usr/bin/python
# Copyright (C) 2022 Jelmer Vernooij
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

from breezy.revision import NULL_REVISION
from breezy.tests import (
    TestCaseWithTransport,
)

from silver_platter.workspace import Workspace


class TestWorkspace(TestCaseWithTransport):

    def test_nascent(self):
        tree = self.make_branch_and_tree('origin')
        with Workspace(tree.branch, dir=self.test_dir) as ws:
            self.assertFalse(ws.changes_since_main())
            self.assertFalse(ws.any_branch_changes())
            self.assertFalse(ws.changes_since_base())
            ws.local_tree.commit('A change')
            self.assertEqual(ws.path, os.path.join(ws.local_tree.basedir, '.'))
            self.assertTrue(ws.changes_since_main())
            self.assertTrue(ws.changes_since_base())
            self.assertTrue(ws.any_branch_changes())
            self.assertEqual(
                [('', NULL_REVISION, ws.local_tree.last_revision())],
                ws.result_branches())

    def test_basic(self):
        tree = self.make_branch_and_tree('origin')
        revid1 = tree.commit('first commit')
        with Workspace(tree.branch, dir=self.test_dir) as ws:
            self.assertFalse(ws.changes_since_main())
            self.assertFalse(ws.any_branch_changes())
            self.assertFalse(ws.changes_since_base())
            ws.local_tree.commit('A change')
            self.assertTrue(ws.changes_since_main())
            self.assertTrue(ws.changes_since_base())
            self.assertTrue(ws.any_branch_changes())
            self.assertEqual(
                [('', revid1, ws.local_tree.last_revision())],
                ws.result_branches())

    def test_colocated(self):
        tree = self.make_branch_and_tree('origin')
        revid1 = tree.commit('main')
        colo_branch = tree.branch.controldir.create_branch('colo')
        colo_checkout = colo_branch.create_checkout('../colo')
        colo_revid1 = colo_checkout.commit('Another')
        self.assertEqual(tree.branch.last_revision(), revid1)
        with Workspace(
                tree.branch, dir=self.test_dir,
                additional_colocated_branches=['colo']) as ws:
            self.assertFalse(ws.changes_since_main())
            self.assertFalse(ws.any_branch_changes())
            self.assertFalse(ws.changes_since_base())
            ws.local_tree.commit('A change')
            self.assertTrue(ws.changes_since_main())
            self.assertTrue(ws.changes_since_base())
            self.assertTrue(ws.any_branch_changes())
            self.assertEqual(
                [('', revid1, ws.local_tree.last_revision()),
                 ('colo', colo_revid1, colo_revid1)],
                ws.result_branches())
