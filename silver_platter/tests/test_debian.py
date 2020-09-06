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

import breezy

from breezy.tests import (
    TestCase,
    )


from breezy.bzr import RemoteBzrProber
from breezy.git import RemoteGitProber

from ..debian import (
    select_probers,
    convert_debian_vcs_url,
    UnsupportedVCSProber,
    )


class SelectProbersTests(TestCase):

    def test_none(self):
        self.assertIs(None, select_probers())
        self.assertIs(None, select_probers(None))

    def test_bzr(self):
        self.assertEqual([RemoteBzrProber], select_probers('bzr'))

    def test_git(self):
        self.assertEqual([RemoteGitProber], select_probers('git'))

    def test_unsupported(self):
        self.assertEqual([UnsupportedVCSProber('foo')], select_probers('foo'))


class ConvertDebianVcsUrlTests(TestCase):

    def test_git(self):
        self.assertEqual(
            'https://salsa.debian.org/jelmer/blah.git',
            convert_debian_vcs_url(
                'Git', 'https://salsa.debian.org/jelmer/blah.git'))

    def test_git_ssh(self):
        if breezy.version_info < (3, 1, 1):
            self.knownFailure('breezy < 3.1.1 can not deal with ssh:// URLs')
        self.assertEqual(
            'ssh://git@git.kali.org/jelmer/blah.git',
            convert_debian_vcs_url(
                'Git', 'ssh://git@git.kali.org/jelmer/blah.git'))
