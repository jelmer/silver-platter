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

from breezy.tests import TestCaseWithTransport


from ..debian.upstream import (
    update_packaging,
    )


class UpdatePackagingTests(TestCaseWithTransport):

    def test_autogen_sh(self):
        t = self.make_branch_and_tree('.')
        self.build_tree_contents([
            ('debian/', ),
            ('debian/changelog', """\
lintian-brush (0.37) UNRELEASED; urgency=medium

  * Add various more aliases for salsa team names.

 -- Jelmer Vernooĳ <jelmer@debian.org>  Fri, 18 Oct 2019 17:34:35 +0000
"""),
            ('debian/rules', """\
%:
\tdh %
""")])
        t.add(['debian', 'debian/changelog', 'debian/rules'])
        oldrev = t.commit('Initial')

        self.build_tree_contents([
            ('autogen.sh', '#!/bin/sh\n')])
        t.add(['autogen.sh'])
        t.commit('Add autogen')

        self.addCleanup(t.lock_write().unlock)
        update_packaging(
            t, t.branch.repository.revision_tree(oldrev),
            committer="Jelmer Vernooĳ <jelmer@debian.org>")

        self.assertFileEqual("""\
lintian-brush (0.37) UNRELEASED; urgency=medium

  * Add various more aliases for salsa team names.
  * Invoke autogen.sh from dh_autoreconf.

 -- Jelmer Vernooĳ <jelmer@debian.org>  Fri, 18 Oct 2019 17:34:35 +0000
""", 'debian/changelog')
        self.assertFileEqual("""\
%:
\tdh %

override_dh_autoreconf:
\tdh_autoreconf ./autogen.sh
""", 'debian/rules')
