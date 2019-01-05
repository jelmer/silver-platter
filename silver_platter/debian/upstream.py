#!/usr/bin/python3
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

"""Support for merging new upstream versions."""

from debian.changelog import Changelog

from breezy.plugins.debian.cmds import cmd_merge_upstream
import subprocess

from ..proposal import (
    BranchChanger,
    )

from . import (
    build,
    )


class NewUpstreamMerger(BranchChanger):

    def __init__(self, snapshot=False, build_verify=False):
        self._snapshot = snapshot
        self._build_verify = build_verify

    def make_changes(self, local_tree):
        # TODO(jelmer): Don't call UI implementation, refactor brz-debian
        cmd_merge_upstream().run(directory=local_tree.basedir,
                                 snapshot=self._snapshot)
        if self._build_verify:
            build(local_tree.basedir)
        with local_tree.get_file('debian/changelog') as f:
            cl = Changelog(f.read())
            self._upstream_version = cl.version.upstream_version
        subprocess.check_call(["debcommit", "-a"], cwd=local_tree.basedir)

    def get_proposal_description(self, existing_proposal):
        return "Merge new upstream release %s" % self._upstream_version

    def should_create_proposal(self):
        # There are no upstream merges too small.
        return True
