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

import unittest

from io import StringIO

from ..debian.lintian import (
    parse_mp_description,
    create_mp_description,
    read_lintian_log,
    )


class ParseMPDescriptionTests(unittest.TestCase):

    def test_single_line(self):
        self.assertEqual(['some change'], parse_mp_description('some change'))

    def test_multiple_lines(self):
        self.assertEqual(
                ['some change', 'some other change'],
                parse_mp_description("""Lintian fixes:
* some change
* some other change
"""))


class CreateMPDescription(unittest.TestCase):

    def test_single_line(self):
        self.assertEqual("some change", create_mp_description(['some change']))

    def test_multiple_lines(self):
        self.assertEqual("""\
Fix some issues reported by lintian
* some change
* some other change
""", create_mp_description(['some change', 'some other change']))


LINTIAN_LOG_EXAMPLE = """\
N: Using profile debian/main.
N: Setting up lab in /srv/org/scratch/temp-lintian-lab-Aitc_iD_k8 ...
N: Starting on group 4digits/1.1.4-1
N: Unpacking packages in group 4digits/1.1.4-1
N: ----
N: Processing source package 4digits (version 1.1.4-1, arch source) ...
C: 4digits source (1.1.4-1) [source]: rules-requires-root-implicitly
C: 4digits source (1.1.4-1) [source]: debian-build-system dh
P: 4digits source (1.1.4-1) [source]: insecure-copyright-format-uri \
http://www.debian.org/doc/packaging-manuals/copyright-format/1.0
W: 4digits source (1.1.4-1) [source]: ancient-standards-version 3.9.5 \
(released 2013-10-28) (current is 4.2.1)
I: 4digits binary (1.1.4-1+b1) [i386]: hardening-no-bindnow \
usr/games/4digits-text
C: 4digits binary (1.1.4-1+b1) [i386]: ctrl-script postinst
C: 4pane source (5.0-1) [source]: source-format 3.0 (quilt)
P: 4pane source (5.0-1) [source]: insecure-copyright-format-uri \
http://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
"""


class LintianLogReaderTests(unittest.TestCase):

    def test_simple(self):
        f = StringIO(LINTIAN_LOG_EXAMPLE)
        self.assertEqual(
                read_lintian_log(f),
                {'4digits': {
                    'ancient-standards-version',
                    'hardening-no-bindnow',
                    'insecure-copyright-format-uri'},
                 '4pane': {'insecure-copyright-format-uri'}})
