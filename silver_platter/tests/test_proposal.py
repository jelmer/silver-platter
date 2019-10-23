#!/usr/bin/python
# Copyright (C) 2019 Jelmer Vernooij
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

from io import BytesIO
import os

from breezy.tests import TestCaseWithTransport

from ..proposal import (
    EmptyMergeProposal,
    check_proposal_diff,
    push_result,
    Workspace,
    )


class PushResultTests(TestCaseWithTransport):

    def test_simple(self):
        target = self.make_branch('target')
        source = self.make_branch_and_tree('source')
        revid = source.commit('Some change')
        push_result(source.branch, target)
        self.assertEqual(target.last_revision(), revid)


class WorkspaceTests(TestCaseWithTransport):

    def test_simple(self):
        b = self.make_branch('target')
        with Workspace(b, dir=self.test_dir) as ws:
            self.assertIs(ws.resume_branch, None)
            self.assertFalse(ws.changes_since_main())
            self.assertFalse(ws.changes_since_resume())
            ws.local_tree.commit('foo')
            self.assertTrue(ws.changes_since_main())
            self.assertTrue(ws.changes_since_main())

    def test_with_resume(self):
        b = self.make_branch_and_tree('target')
        c = b.controldir.sprout('resume').open_workingtree()
        c.commit('some change')
        with Workspace(b.branch, resume_branch=c.branch,
                       dir=self.test_dir) as ws:
            self.assertEqual(
                ws.local_tree.branch.last_revision(),
                c.branch.last_revision())
            self.assertIs(ws.resume_branch, c.branch)
            self.assertTrue(ws.changes_since_main())
            self.assertFalse(ws.changes_since_resume())
            ws.local_tree.commit('foo')
            self.assertTrue(ws.changes_since_main())
            self.assertTrue(ws.changes_since_resume())

    def test_with_resume_conflicting(self):
        b = self.make_branch_and_tree('target')
        self.build_tree_contents([('target/foo', 'somecontents\n')])
        b.add(['foo'])
        b.commit('initial')
        c = b.controldir.sprout('resume').open_workingtree()
        self.build_tree_contents([('target/foo', 'new contents in main\n')])
        b.commit('add conflict in main')
        self.build_tree_contents([('resume/foo', 'new contents in resume\n')])
        c.commit('add conflict in resume')
        with Workspace(b.branch, resume_branch=c.branch,
                       dir=self.test_dir) as ws:
            self.assertIs(ws.resume_branch, None)
            self.assertEqual(
                b.branch.last_revision(), ws.local_tree.branch.last_revision())
            self.assertFalse(ws.changes_since_main())
            self.assertFalse(ws.changes_since_resume())
            ws.local_tree.commit('foo')
            self.assertTrue(ws.changes_since_main())
            self.assertTrue(ws.changes_since_resume())

    def test_orig_tree(self):
        b = self.make_branch_and_tree('target')
        cid = b.commit('some change')
        with Workspace(b.branch, dir=self.test_dir) as ws:
            ws.local_tree.commit('blah')
            self.assertEqual(cid, ws.orig_tree().get_revision_id())

    def test_show_diff(self):
        b = self.make_branch_and_tree('target')
        with Workspace(b.branch, dir=self.test_dir) as ws:
            self.build_tree_contents([
                (os.path.join(ws.local_tree.basedir, 'foo'),
                 'some content\n')])
            ws.local_tree.add(['foo'])
            ws.local_tree.commit('blah')
            self.assertTrue(ws.changes_since_main())
            self.assertTrue(ws.changes_since_resume())
            f = BytesIO()
            ws.show_diff(outf=f)
            self.assertContainsRe(
                f.getvalue().decode('utf-8'), '\\+some content')


class CheckProposalDiffBase(object):

    def test_no_new_commits(self):
        orig = self.make_branch_and_tree('orig', format=self.format)
        self.build_tree(['orig/a'])
        orig.add(['a'])
        orig.commit('blah')

        proposal = orig.controldir.sprout('proposal').open_branch()

        self.addCleanup(proposal.lock_write().unlock)
        self.assertRaises(
            EmptyMergeProposal, check_proposal_diff, proposal, orig.branch)

    def test_no_op_commits(self):
        orig = self.make_branch_and_tree('orig', format=self.format)
        self.build_tree(['orig/a'])
        orig.add(['a'])
        orig.commit('blah')

        proposal = orig.controldir.sprout('proposal').open_workingtree()
        proposal.commit('another commit that is pointless')

        self.addCleanup(proposal.lock_write().unlock)
        self.assertRaises(
            EmptyMergeProposal, check_proposal_diff, proposal.branch,
            orig.branch)

    def test_indep(self):
        orig = self.make_branch_and_tree('orig', format=self.format)
        self.build_tree(['orig/a'])
        orig.add(['a'])
        orig.commit('blah')

        proposal = orig.controldir.sprout('proposal').open_workingtree()
        self.build_tree_contents([('orig/b', 'b'), ('orig/c', 'c')])
        orig.add(['b', 'c'])
        orig.commit('independent')

        self.build_tree_contents([('proposal/b', 'b')])
        if proposal.supports_setting_file_ids():
            proposal.add(['b'], [orig.path2id('b')])
        else:
            proposal.add(['b'])
        proposal.commit('not pointless')

        self.addCleanup(proposal.lock_write().unlock)
        self.assertRaises(
            EmptyMergeProposal, check_proposal_diff, proposal.branch,
            orig.branch)

    def test_changes(self):
        orig = self.make_branch_and_tree('orig', format=self.format)
        self.build_tree(['orig/a'])
        orig.add(['a'])
        orig.commit('blah')

        proposal = orig.controldir.sprout('proposal').open_workingtree()
        self.build_tree(['proposal/b'])
        proposal.add(['b'])
        proposal.commit('not pointless')

        self.addCleanup(proposal.lock_write().unlock)
        check_proposal_diff(proposal.branch, orig.branch)


class CheckProposalDiffGitTests(TestCaseWithTransport, CheckProposalDiffBase):

    format = 'git'


class CheckProposalDiffBzrTests(TestCaseWithTransport, CheckProposalDiffBase):

    format = 'bzr'
