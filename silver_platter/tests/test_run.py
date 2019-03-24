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

from breezy.tests import (
    TestCaseWithTransport,
)

from ..run import (
    ScriptMadeNoChanges,
    script_runner,
    )


class ScriptRunnerTests(TestCaseWithTransport):

    def test_simple(self):
        tree = self.make_branch_and_tree('tree')

        with open('foo.sh', 'w') as f:
            f.write("""\
#!/bin/sh
echo Foo > bar
echo "Some message"
brz add --quiet bar
""")
        os.chmod('foo.sh', 0o755)

        description = script_runner(tree, os.path.abspath('foo.sh'))
        self.assertEqual(description, 'Some message\n')

    def test_no_changes(self):
        tree = self.make_branch_and_tree('tree')

        with open('foo.sh', 'w') as f:
            f.write("""\
#!/bin/sh
echo Foo > bar
echo "Some message"
""")
        os.chmod('foo.sh', 0o755)

        self.assertRaises(
            ScriptMadeNoChanges, script_runner, tree,
            os.path.abspath('foo.sh'))
