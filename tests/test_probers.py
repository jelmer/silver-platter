#!/usr/bin/python
# Copyright (C) 2018 Jelmer Vernooij
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

from breezy.bzr import RemoteBzrProber
from breezy.git import RemoteGitProber
from breezy.tests import TestCase

from silver_platter.probers import UnsupportedVCSProber, select_probers


class SelectProbersTests(TestCase):
    def test_none(self):
        self.assertIs(None, select_probers())
        self.assertIs(None, select_probers(None))

    def test_bzr(self):
        self.assertEqual([RemoteBzrProber], select_probers("bzr"))

    def test_git(self):
        self.assertEqual([RemoteGitProber], select_probers("git"))

    def test_unsupported(self):
        self.assertEqual([UnsupportedVCSProber("foo")], select_probers("foo"))
