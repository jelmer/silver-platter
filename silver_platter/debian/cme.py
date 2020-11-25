#!/usr/bin/python
# Copyright (C) 2020 Jelmer Vernooij <jelmer@jelmer.uk>
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

import subprocess

from .changer import (
    run_mutator,
    DebianChanger,
    ChangerResult,
    )
from breezy.trace import note


BRANCH_NAME = 'cme'


class CMEResult(object):

    def __init__(self):
        pass


class CMEChanger(DebianChanger):

    name = 'cme'

    def __init__(self, dry_run=False):
        self.dry_run = dry_run

    @classmethod
    def setup_parser(cls, parser):
        pass

    @classmethod
    def from_args(cls, args):
        return cls()

    def suggest_branch_name(self):
        return BRANCH_NAME

    def make_changes(self, local_tree, subpath, update_changelog, reporter,
                     committer, base_proposal=None):
        base_revid = local_tree.last_revision()
        cwd = local_tree.abspath(subpath or '')
        subprocess.check_call(
            ['/usr/bin/cme', 'modify', 'dpkg', '-save'],
            cwd=cwd)
        local_tree.commit('Reformat for cme.')
        subprocess.check_call(
            ['/usr/bin/cme', 'fix', 'dpkg'], cwd=cwd)
        revid = local_tree.commit('Run cme.')
        branches = [('main', None, base_revid, revid)]
        tags = []
        return ChangerResult(
            description=None, mutator=None, branches=branches,
            tags=tags, proposed_commit_message='Run cme.',
            sufficient_for_proposal=True)

    def get_proposal_description(
            self, applied, description_format, existing_proposal):
        return 'Run cme.'

    def describe(self, result, publish_result):
        if publish_result.is_new:
            note('Proposed change from \'cme fix dpkg\': %s',
                 publish_result.proposal.url)
        else:
            note('No changes for package %s', result.package_name)

    @classmethod
    def describe_command(cls, command):
        return "Apply Configuration Model Editor (CME) fixes"


if __name__ == '__main__':
    import sys
    sys.exit(run_mutator(CMEChanger))
