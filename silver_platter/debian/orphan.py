#!/usr/bin/python
# Copyright (C) 2019 Jelmer Vernooij <jelmer@jelmer.uk>
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

from .changer import (
    run_changer,
    DebianChanger,
    setup_parser,
    )
from breezy.trace import note

from lintian_brush.control import update_control


BRANCH_NAME = 'orphan'


class OrphanChanger(DebianChanger):

    def __init__(self):
        pass

    @classmethod
    def setup_parser(cls, parser):
        pass

    @classmethod
    def from_args(cls, args):
        return cls()

    def suggest_branch_name(self):
        return BRANCH_NAME

    def make_changes(self, local_tree, subpath, update_changelog, committer):
        def set_maintainer(source):
            source['Maintainer'] = (
                'Debian QA Group <packages@qa.debian.org>')
        update_control(source_package_cb=set_maintainer)
        return {}

    def get_proposal_description(
            self, applied, description_format, existing_proposal):
        return 'Set the package maintainer to the QA team.'

    def get_commit_message(self, applied, existing_proposal):
        return 'Set the package maintainer to the QA team.'

    def allow_create_proposal(self, applied):
        return True

    def describe(self, description, publish_result):
        if publish_result.is_new:
            note('Proposed change of maintainer to QA team',
                 publish_result.proposal.url)
        else:
            note('No fixes for proposal %s', publish_result.proposal.url)


def main(args):
    changer = OrphanChanger.from_args(args)
    return run_changer(changer, args)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(prog='orphan')
    setup_parser(parser)
    OrphanChanger.setup_parser(parser)
    args = parser.parse_args()
    main(args)
