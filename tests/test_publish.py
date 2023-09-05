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

from breezy.tests import TestCaseWithTransport

from silver_platter.publish import (
    EmptyMergeProposal,
    check_proposal_diff,
    push_result,
)


class PushResultTests(TestCaseWithTransport):
    def test_simple(self):
        target = self.make_branch("target")
        source = self.make_branch_and_tree("source")
        revid = source.commit("Some change")
        push_result(source.branch, target)
        self.assertEqual(target.last_revision(), revid)


class CheckProposalDiffBase:
    def test_no_new_commits(self):
        orig = self.make_branch_and_tree("orig", format=self.format)
        self.build_tree(["orig/a"])
        orig.add(["a"])
        orig.commit("blah")

        proposal = orig.controldir.sprout("proposal").open_branch()

        self.addCleanup(proposal.lock_write().unlock)
        self.assertRaises(
            EmptyMergeProposal, check_proposal_diff, proposal, orig.branch
        )

    def test_no_op_commits(self):
        orig = self.make_branch_and_tree("orig", format=self.format)
        self.build_tree(["orig/a"])
        orig.add(["a"])
        orig.commit("blah")

        proposal = orig.controldir.sprout("proposal").open_workingtree()
        proposal.commit("another commit that is pointless")

        self.addCleanup(proposal.lock_write().unlock)
        self.assertRaises(
            EmptyMergeProposal, check_proposal_diff, proposal.branch,
            orig.branch
        )

    def test_indep(self):
        orig = self.make_branch_and_tree("orig", format=self.format)
        self.build_tree(["orig/a"])
        orig.add(["a"])
        orig.commit("blah")

        proposal = orig.controldir.sprout("proposal").open_workingtree()
        self.build_tree_contents([("orig/b", "b"), ("orig/c", "c")])
        orig.add(["b", "c"])
        orig.commit("independent")

        self.build_tree_contents([("proposal/b", "b")])
        if proposal.supports_setting_file_ids():
            proposal.add(["b"], ids=[orig.path2id("b")])
        else:
            proposal.add(["b"])
        proposal.commit("not pointless")

        self.addCleanup(proposal.lock_write().unlock)
        self.assertRaises(
            EmptyMergeProposal, check_proposal_diff, proposal.branch,
            orig.branch)

    def test_changes(self):
        orig = self.make_branch_and_tree("orig", format=self.format)
        self.build_tree(["orig/a"])
        orig.add(["a"])
        orig.commit("blah")

        proposal = orig.controldir.sprout("proposal").open_workingtree()
        self.build_tree(["proposal/b"])
        proposal.add(["b"])
        proposal.commit("not pointless")

        self.addCleanup(proposal.lock_write().unlock)
        check_proposal_diff(proposal.branch, orig.branch)


class CheckProposalDiffGitTests(TestCaseWithTransport, CheckProposalDiffBase):

    format = "git"


class CheckProposalDiffBzrTests(TestCaseWithTransport, CheckProposalDiffBase):

    format = "bzr"
