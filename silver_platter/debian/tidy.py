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

from breezy.trace import note

from .changer import (
    run_changer,
    DebianChanger,
    setup_multi_parser as setup_changer_parser,
    )

from .lintian import LintianBrushChanger
from .multiarch import MultiArchHintsChanger


BRANCH_NAME = 'tidy'


class TidyChanger(DebianChanger):

    SUBCHANGERS = [
        LintianBrushChanger,
        MultiArchHintsChanger,
        ]

    def __init__(self):
        self.subchangers = [kls() for kls in self.SUBCHANGERS]

    @classmethod
    def setup_parser(cls, parser):
        pass

    @classmethod
    def from_args(cls, args):
        return cls()

    def suggest_branch_name(self):
        return BRANCH_NAME

    def make_changes(self, local_tree, subpath, update_changelog, committer):
        result = {}
        for subchanger in self.subchangers:
            result[subchanger] = (
                subchanger.make_changes(
                    local_tree, subpath, update_changelog, committer))
        return result

    def get_proposal_description(
            self, result, description_format, existing_proposal):
        entries = []
        for subchanger, memo in result.items():
            # TODO(jelmer): Does passing existing proposal in here work?
            entries.append(subchanger.get_proposal_description(
                memo, description_format, existing_proposal))
        return '\n'.join(entries)

    def get_commit_message(self, result, existing_proposal):
        ret = []
        for subchanger in result:
            if isinstance(subchanger, LintianBrushChanger):
                ret.append('fix some lintian tags')
            if isinstance(subchanger, MultiArchHintsChanger):
                ret.append('apply multi-arch hints')
        return (', '.join(ret) + '.').capitalize()

    def allow_create_proposal(self, result):
        for subchanger, memo in result.items():
            if subchanger.allow_create_proposal(memo):
                return True
        else:
            return False

    def describe(self, result, publish_result):
        if publish_result.is_new:
            note('Create merge proposal: %s', publish_result.proposal.url)
        elif result:
            note('Updated proposal %s', publish_result.proposal.url)
        else:
            note('No new fixes for proposal %s', publish_result.proposal.url)

    def tags(self, result):
        ret = []
        for subchanger, memo in result.items():
            subret = subchanger.tags(memo)
            if subret is None:
                return None
            ret.extend(subret)
        return ret


def main(args):
    changer = TidyChanger.from_args(args)

    return run_changer(changer, args)


def setup_parser(parser):
    setup_changer_parser(parser)
    TidyChanger.setup_parser(parser)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(prog='tidy')
    setup_parser(parser)
    args = parser.parse_args()
    main(args)
