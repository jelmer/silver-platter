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
import shutil

from breezy.revision import NULL_REVISION
from breezy.tests import TestCaseWithTransport

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

    def test_without_main(self):
        with Workspace(None, dir=self.test_dir) as ws:
            self.assertFalse(ws.changes_since_main())
            self.assertFalse(ws.any_branch_changes())
            self.assertFalse(ws.changes_since_base())
            ws.local_tree.commit('A change')
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

    def test_cached_branch_up_to_date(self):
        tree = self.make_branch_and_tree('origin')
        revid1 = tree.commit('first commit')
        cached = tree.branch.controldir.sprout('cached')
        with Workspace(tree.branch, cached_branch=cached.open_branch(),
                       dir=self.test_dir) as ws:
            self.assertFalse(ws.changes_since_main())
            self.assertFalse(ws.any_branch_changes())
            self.assertFalse(ws.changes_since_base())
            self.assertEqual(ws.local_tree.last_revision(), revid1)

    def test_cached_branch_out_of_date(self):
        tree = self.make_branch_and_tree('origin')
        tree.commit('first commit')
        cached = tree.branch.controldir.sprout('cached')
        revid2 = tree.commit('first commit')
        with Workspace(tree.branch, cached_branch=cached.open_branch(),
                       dir=self.test_dir) as ws:
            self.assertFalse(ws.changes_since_main())
            self.assertFalse(ws.any_branch_changes())
            self.assertFalse(ws.changes_since_base())
            self.assertEqual(ws.local_tree.last_revision(), revid2)

    def commit_on_colo(self, controldir, name, message):
        colo_branch = controldir.create_branch('colo')
        colo_checkout = colo_branch.create_checkout(name)
        try:
            return colo_checkout.commit(message)
        finally:
            shutil.rmtree(name)

    def test_colocated(self):
        tree = self.make_branch_and_tree('origin')
        revid1 = tree.commit('main')
        colo_revid1 = self.commit_on_colo(
            tree.branch.controldir, 'colo', 'Another')
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

    def test_resume_continue(self):
        tree = self.make_branch_and_tree('origin')
        revid1 = tree.commit('first commit')
        resume = tree.branch.controldir.sprout('resume')
        resume_tree = resume.open_workingtree()
        resume_revid1 = resume_tree.commit('resume')
        with Workspace(tree.branch, resume_branch=resume_tree.branch,
                       dir=self.test_dir) as ws:
            self.assertTrue(ws.changes_since_main())
            self.assertTrue(ws.any_branch_changes())
            self.assertFalse(ws.refreshed)
            self.assertFalse(ws.changes_since_base())
            self.assertEqual(ws.local_tree.last_revision(), resume_revid1)
            self.assertEqual([
                ('', revid1, resume_revid1)], ws.result_branches())

    def test_resume_discard(self):
        tree = self.make_branch_and_tree('origin')
        tree.commit('first commit')
        resume = tree.branch.controldir.sprout('resume')
        revid2 = tree.commit('second commit')
        resume_tree = resume.open_workingtree()
        resume_tree.commit('resume')
        with Workspace(tree.branch, resume_branch=resume_tree.branch,
                       dir=self.test_dir) as ws:
            self.assertFalse(ws.changes_since_main())
            self.assertFalse(ws.any_branch_changes())
            self.assertTrue(ws.refreshed)
            self.assertFalse(ws.changes_since_base())
            self.assertEqual(ws.local_tree.last_revision(), revid2)
            self.assertEqual([('', revid2, revid2)], ws.result_branches())

    def test_resume_continue_with_unchanged_colocated(self):
        tree = self.make_branch_and_tree('origin')
        revid1 = tree.commit('first commit')
        colo_revid1 = self.commit_on_colo(
            tree.branch.controldir, 'colo', 'First colo')
        resume = tree.branch.controldir.sprout('resume')
        resume_tree = resume.open_workingtree()
        resume_revid1 = resume_tree.commit('resume')
        with Workspace(tree.branch, resume_branch=resume_tree.branch,
                       dir=self.test_dir,
                       additional_colocated_branches=['colo']) as ws:
            self.assertTrue(ws.changes_since_main())
            self.assertTrue(ws.any_branch_changes())
            self.assertFalse(ws.refreshed)
            self.assertFalse(ws.changes_since_base())
            self.assertEqual(ws.local_tree.last_revision(), resume_revid1)
            self.assertEqual([
                ('', revid1, resume_revid1),
                ('colo', colo_revid1, colo_revid1),
            ], ws.result_branches())

    def test_resume_discard_with_unchanged_colocated(self):
        tree = self.make_branch_and_tree('origin')
        tree.commit('first commit')
        colo_revid1 = self.commit_on_colo(
            tree.branch.controldir, 'colo', 'First colo')
        resume = tree.branch.controldir.sprout('resume')
        self.commit_on_colo(resume, 'colo', 'First colo on resume')
        revid2 = tree.commit('second commit')
        resume_tree = resume.open_workingtree()
        resume_tree.commit('resume')
        with Workspace(tree.branch, resume_branch=resume_tree.branch,
                       dir=self.test_dir,
                       additional_colocated_branches=['colo']) as ws:
            self.assertFalse(ws.changes_since_main())
            self.assertFalse(ws.any_branch_changes())
            self.assertTrue(ws.refreshed)
            self.assertFalse(ws.changes_since_base())
            self.assertEqual(ws.local_tree.last_revision(), revid2)
            self.assertEqual([
                ('', revid2, revid2),
                ('colo', colo_revid1, colo_revid1),
            ], ws.result_branches())
