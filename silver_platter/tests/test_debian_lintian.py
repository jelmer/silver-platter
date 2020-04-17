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

from ..debian.lintian import (
    parse_mp_description,
    create_mp_description,
    get_fixers,
    UnknownFixer,
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
        self.assertEqual(
            "some change", create_mp_description('plain', ['some change']))

    def test_multiple_lines(self):
        self.assertEqual("""\
Fix some issues reported by lintian
* some change
* some other change
""", create_mp_description('plain', ['some change', 'some other change']))


class GetFixersTests(unittest.TestCase):

    def setUp(self):
        super(GetFixersTests, self).setUp()
        from lintian_brush import Fixer
        self.fixers = [Fixer('foo', ['atag'])]

    def test_get_all(self):
        self.assertEqual([self.fixers[0]], list(get_fixers(self.fixers)))

    def test_get_specified(self):
        self.assertEqual(
            [self.fixers[0]], list(get_fixers(self.fixers, names=['foo'])))

    def test_get_specified_tag(self):
        self.assertEqual(
            [self.fixers[0]], list(get_fixers(self.fixers, tags=['atag'])))

    def test_get_unknown(self):
        self.assertRaises(UnknownFixer, get_fixers, self.fixers, names=['bar'])
