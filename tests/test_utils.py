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

from silver_platter.utils import (
    PostCheckFailed,
    PreCheckFailed,
    TemporarySprout,
    run_post_check,
    run_pre_check,
)


class TemporarySproutTests(TestCaseWithTransport):
    def test_simple(self):
        builder = self.make_branch_builder(".")
        builder.start_series()
        orig_revid = builder.build_snapshot(
            None,
            [
                ("add", ("", None, "directory", "")),
                ("add", ("debian/", None, "directory", "")),
                ("add", ("debian/control", None, "file", b"initial")),
            ],
            message="Initial\n",
        )
        builder.finish_series()
        branch = builder.get_branch()
        with TemporarySprout(branch, dir=self.test_dir) as tree:
            self.assertNotEqual(branch.control_url, tree.branch.control_url)
            tree.commit("blah")
            # Commits in the temporary sprout don't affect the original branch.
            self.assertEqual(branch.last_revision(), orig_revid)

    def test_nonexistent_colocated(self):
        # Colocated branches that are specified but don't exist are ignored.
        builder = self.make_branch_builder(".")
        builder.start_series()
        orig_revid = builder.build_snapshot(
            None,
            [
                ("add", ("", None, "directory", "")),
                ("add", ("debian/", None, "directory", "")),
            ],
            message="Initial\n",
        )
        builder.finish_series()
        branch = builder.get_branch()
        with TemporarySprout(
                branch, {"foo": "foo"}, dir=self.test_dir) as tree:
            self.assertNotEqual(branch.control_url, tree.branch.control_url)
            tree.commit("blah")
            # Commits in the temporary sprout don't affect the original branch.
            self.assertEqual(branch.last_revision(), orig_revid)


class RunPreCheckTests(TestCaseWithTransport):
    def test_none(self):
        tree = self.make_branch_and_tree("tree")
        self.assertIsNone(run_pre_check(tree, None))

    def test_false(self):
        tree = self.make_branch_and_tree("tree")
        self.assertRaises(PreCheckFailed, run_pre_check, tree, "/bin/false")

    def test_true(self):
        tree = self.make_branch_and_tree("tree")
        self.assertIsNone(run_pre_check(tree, "/bin/true"))


class RunPostCheckTests(TestCaseWithTransport):
    def test_none(self):
        tree = self.make_branch_and_tree("tree")
        self.assertIsNone(run_post_check(tree, None, None))

    def test_false(self):
        tree = self.make_branch_and_tree("tree")
        cid = tree.commit("a")
        self.assertRaises(
            PostCheckFailed, run_post_check, tree, "/bin/false",
            since_revid=cid
        )

    def test_true(self):
        tree = self.make_branch_and_tree("tree")
        cid = tree.commit("a")
        self.assertIsNone(run_post_check(tree, "/bin/true", since_revid=cid))
