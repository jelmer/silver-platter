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

from breezy.tests import TestCase

from silver_platter.debian import (
    convert_debian_vcs_url,
)


class ConvertDebianVcsUrlTests(TestCase):
    def test_git(self):
        self.assertEqual(
            "https://salsa.debian.org/jelmer/blah.git",
            convert_debian_vcs_url(
                "Git", "https://salsa.debian.org/jelmer/blah.git"
            ),
        )

    def test_git_ssh(self):
        self.assertIn(
            convert_debian_vcs_url(
                "Git", "ssh://git@git.kali.org/jelmer/blah.git"
            ),
            (
                "git+ssh://git@git.kali.org/jelmer/blah.git",
                "ssh://git@git.kali.org/jelmer/blah.git",
            ),
        )
