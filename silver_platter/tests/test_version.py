#!/usr/bin/python
# Copyright (C) 2019 Jelmer Vernooij <jelmer@jelmer.uk>
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
import re
from unittest import TestCase

from silver_platter import version_string


class VersionMatchTest(TestCase):
    def test_matches_setup_version(self):
        if not os.path.exists("setup.py"):
            self.skipTest("no setup.py available. " "Running outside of source tree?")
        # TODO(jelmer): Surely there's a better way of doing this?
        with open("setup.py", "r") as f:
            for line in f:
                m = re.match(r'[ ]*version=["\'](.*)["\'],', line)
                if m:
                    setup_version = m.group(1)
                    break
            else:
                raise AssertionError("setup version not found")
        self.assertEqual(version_string, setup_version)
