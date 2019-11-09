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

from breezy.tests import (
    TestCaseWithTransport,
    TestCase,
    )


from breezy.bzr import RemoteBzrProber
from breezy.git import RemoteGitProber

from ..debian import (
    select_probers,
    should_update_changelog,
    UnsupportedVCSProber,
    )


def make_changelog(entries):
    return ("""\
lintian-brush (0.1) UNRELEASED; urgency=medium

""" + ''.join(["  * %s\n" % entry for entry in entries]) + """

 -- Jelmer Vernooij <jelmer@debian.org>  Sat, 13 Oct 2018 11:21:39 +0100
""").encode('utf-8')


class ShouldUpdateChangelogTests(TestCaseWithTransport):

    def test_empty(self):
        branch = self.make_branch('.')
        self.assertTrue(should_update_changelog(branch))

    def test_update_with_change(self):
        builder = self.make_branch_builder('.')
        builder.start_series()
        builder.build_snapshot(None, [
            ('add', ('', None, 'directory', '')),
            ('add', ('upstream', None, 'file', b'upstream')),
            ('add', ('debian/', None, 'directory', '')),
            ('add', ('debian/changelog', None, 'file',
                     make_changelog(['initial release']))),
            ('add', ('debian/control', None, 'file', b'initial'))],
            message='Initial\n')
        changelog_entries = ['initial release']
        for i in range(20):
            builder.build_snapshot(None, [
                ('modify', ('upstream', b'upstream %d' % i))],
                message='Upstream')
            changelog_entries.append('next entry %d' % i)
            builder.build_snapshot(None, [
                ('modify', ('debian/changelog',
                 make_changelog(changelog_entries))),
                ('modify', ('debian/control', b'next %d' % i))],
                message='Next')
        builder.finish_series()
        branch = builder.get_branch()
        self.assertTrue(should_update_changelog(branch))

    def test_changelog_updated_separately(self):
        builder = self.make_branch_builder('.')
        builder.start_series()
        builder.build_snapshot(None, [
            ('add', ('', None, 'directory', '')),
            ('add', ('debian/', None, 'directory', '')),
            ('add', ('debian/changelog', None, 'file',
             make_changelog(['initial release']))),
            ('add', ('debian/control', None, 'file', b'initial'))],
            message='Initial\n')
        changelog_entries = ['initial release']
        for i in range(20):
            changelog_entries.append('next entry %d' % i)
            builder.build_snapshot(None, [
                ('modify', ('debian/control', b'next %d' % i))],
                message='Next\n')
        builder.build_snapshot(None, [
            ('modify', ('debian/changelog',
             make_changelog(changelog_entries)))])
        changelog_entries.append('final entry')
        builder.build_snapshot(None, [
            ('modify', ('debian/control', b'more'))],
            message='Next\n')
        builder.build_snapshot(None, [
            ('modify', ('debian/changelog',
             make_changelog(changelog_entries)))])
        builder.finish_series()
        branch = builder.get_branch()
        self.assertFalse(should_update_changelog(branch))

    def test_has_dch_in_messages(self):
        builder = self.make_branch_builder('.')
        builder.build_snapshot(None, [
            ('add', ('', None, 'directory', ''))],
            message='Git-Dch: ignore\n')
        branch = builder.get_branch()
        self.assertFalse(should_update_changelog(branch))


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
