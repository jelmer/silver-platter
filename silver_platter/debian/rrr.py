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

from .changer import (
    run_changer,
    DebianChanger,
    )
from breezy import osutils
from breezy.trace import note

from lintian_brush import add_changelog_entry
from lintian_brush.control import ControlUpdater


BRANCH_NAME = 'rules-requires-root'


class RulesRequiresRootResult(object):

    def __init__(self, package=None):
        self.package = package


class RulesRequiresRootChanger(DebianChanger):

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

    def make_changes(self, local_tree, subpath, update_changelog, committer):
        with ControlUpdater.from_tree(local_tree, subpath) as updater:
            updater.source['Rules-Requires-Root'] = 'no'
            result = RulesRequiresRootResult(updater.source['Source'])
        if update_changelog in (True, None):
            add_changelog_entry(
                local_tree,
                osutils.pathjoin(subpath, 'debian/changelog'),
                'Set Rules-Requires-Root: no.', qa=True)
        local_tree.commit(
            'Set Rules-Requires-Root.', committer=committer,
            allow_pointless=False)
        return result

    def get_proposal_description(
            self, applied, description_format, existing_proposal):
        return 'Set Rules-Requires-Root.'

    def get_commit_message(self, applied, existing_proposal):
        return 'Set Rules-Requires-Root.'

    def allow_create_proposal(self, applied):
        return True

    def describe(self, result, publish_result):
        if publish_result.is_new:
            note('Proposed change to enable Rules-Requires-Root: %s',
                 publish_result.proposal.url)
        else:
            note('No changes for package %s', result.package_name)

    def tags(self):
        return []


def main(args):
    changer = RulesRequiresRootChanger.from_args(args)
    return run_changer(changer, args)


def setup_parser(parser):
    from .changer import setup_multi_parser
    setup_multi_parser(parser)
    RulesRequiresRootChanger.setup_parser(parser)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(prog='rules-requires-root')
    setup_parser(parser)
    args = parser.parse_args()
    main(args)
