#!/usr/bin/python
# Copyright (C) 2018 Jelmer Vernooij <jelmer@jelmer.uk>
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

from __future__ import absolute_import

import apt_pkg
from debian.deb822 import Deb822
import itertools

from breezy.plugins.debian.cmds import cmd_builddeb
from breezy.plugins.debian.directory import source_package_vcs_url
from breezy.plugins.debian.errors import (
    BuildFailedError,
    MissingUpstreamTarball,
    )


class NoSuchPackage(Exception):
    """No such package."""


def build(directory, builder='sbuild'):
    """Build a debian package in a directory."""
    cmd_builddeb().run([directory], builder=builder)


def get_source_package(name):
    apt_pkg.init()

    sources = apt_pkg.SourceRecords()

    if not sources.lookup(name):
        raise NoSuchPackage(name)
    return Deb822(sources.record)


def should_update_changelog(branch):
    """Guess whether the changelog should be updated manually.
    """
    with branch.lock_read():
        graph = branch.repository.get_graph()
        revids = itertools.islice(
            graph.iter_lefthand_ancestry(branch.last_revision()), 200)
        for revid, rev in branch.repository.iter_revisions(revids):
            if rev is None:
                # Ghost
                continue
            if 'Git-Dch: ' in rev.message:
                return False
    # Assume yes
    return True
