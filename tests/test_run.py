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

import os

from breezy.tests import TestCaseWithTransport

from silver_platter.run import ScriptMadeNoChanges, script_runner


class ScriptRunnerTests(TestCaseWithTransport):
    def setUp(self):
        super().setUp()
        self.tree = self.make_branch_and_tree("tree")

        with open("foo.sh", "w") as f:
            f.write(
                """\
#!/bin/sh
echo Foo > bar
echo "Some message"
brz add --quiet bar
"""
            )
        os.chmod("foo.sh", 0o755)

    def test_simple_with_commit(self):
        result = script_runner(
            self.tree, os.path.abspath("foo.sh"), commit_pending=True
        )
        self.assertEqual(result.description, "Some message\n")

    def test_simple_with_autocommit(self):
        result = script_runner(self.tree, os.path.abspath("foo.sh"))
        r = self.tree.branch.repository.get_revision(self.tree.last_revision())
        self.assertEqual(r.message, "Some message\n")
        self.assertEqual(result.description, "Some message\n")

    def test_simple_with_autocommit_and_script_commits(self):
        with open("foo.sh", "w") as f:
            f.write(
                """\
#!/bin/sh
echo Foo > bar
echo "Some message"
brz add --quiet bar
brz commit --quiet -m blah
"""
            )
        os.chmod("foo.sh", 0o755)
        result = script_runner(self.tree, os.path.abspath("foo.sh"))
        rev = self.tree.branch.repository.get_revision(
            self.tree.last_revision())
        self.assertEqual(rev.message, "blah")
        self.assertEqual(result.description, "Some message\n")

    def test_simple_without_commit(self):
        self.assertRaises(
            ScriptMadeNoChanges,
            script_runner,
            self.tree,
            os.path.abspath("foo.sh"),
            commit_pending=False,
        )

    def test_no_changes(self):
        with open("foo.sh", "w") as f:
            f.write(
                """\
#!/bin/sh
echo "Some message"
"""
            )
        self.assertRaises(
            ScriptMadeNoChanges,
            script_runner,
            self.tree,
            os.path.abspath("foo.sh"),
            commit_pending=True,
        )
