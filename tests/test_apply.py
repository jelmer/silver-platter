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

from breezy.tests import TestCaseWithTransport

from silver_platter.apply import ScriptMadeNoChanges, script_runner


class TestScriptRunner(TestCaseWithTransport):

    def test_no_api(self):
        tree = self.make_branch_and_tree('t')
        self.build_tree_contents([
            ('script.sh', """\
#!/bin/sh
echo foo > bar
echo Did a thing
"""),
            ('t/bar', 'initial')])
        os.chmod('script.sh', 0o755)
        tree.add('bar')
        old_revid = tree.commit('initial')
        result = script_runner(
            tree,
            script=os.path.abspath('script.sh'),
            committer='Joe Example <joe@example.com>')
        self.assertFalse(tree.has_changes())
        self.assertEqual(result.old_revision, old_revid)
        self.assertEqual(result.new_revision, tree.last_revision())
        self.assertEqual(result.description, 'Did a thing\n')

    def test_api(self):
        tree = self.make_branch_and_tree('t')
        self.build_tree_contents([
            ('script.sh', """\
#!/bin/sh
echo foo > bar
echo '{"description": "Did a thing", "code": "success"}' > $SVP_RESULT
"""),
            ('t/bar', 'initial')])
        os.chmod('script.sh', 0o755)
        tree.add('bar')
        old_revid = tree.commit('initial')
        result = script_runner(
            tree,
            script=os.path.abspath('script.sh'),
            committer='Joe Example <joe@example.com>')
        self.assertFalse(tree.has_changes())
        self.assertEqual(result.old_revision, old_revid)
        self.assertEqual(result.new_revision, tree.last_revision())
        self.assertEqual(result.description, 'Did a thing')

    def test_new_file(self):
        tree = self.make_branch_and_tree('t')
        self.build_tree_contents([
            ('script.sh', """\
#!/bin/sh
echo foo > bar
echo Did a thing
""")])
        os.chmod('script.sh', 0o755)
        old_revid = tree.commit('initial')
        result = script_runner(
            tree,
            script=os.path.abspath('script.sh'),
            committer='Joe Example <joe@example.com>')
        self.assertFalse(tree.has_changes())
        self.assertEqual(result.old_revision, old_revid)
        self.assertEqual(result.new_revision, tree.last_revision())
        self.assertEqual(result.description, 'Did a thing\n')

    def test_no_changes(self):
        tree = self.make_branch_and_tree('t')
        self.build_tree_contents([
            ('script.sh', """\
#!/bin/sh
echo Did a thing
"""),
            ('t/bar', 'initial')])
        os.chmod('script.sh', 0o755)
        tree.add('bar')
        tree.commit('initial')
        self.assertRaises(
            ScriptMadeNoChanges, script_runner,
            tree,
            script=os.path.abspath('script.sh'),
            committer='Joe Example <joe@example.com>')
