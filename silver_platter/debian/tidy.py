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
    run_mutator,
    DebianChanger,
    ChangerResult,
    )

from .lintian import LintianBrushChanger
from .multiarch import MultiArchHintsChanger


BRANCH_NAME = 'tidy'


class TidyChanger(DebianChanger):

    name = 'tidy'

    SUBCHANGERS = [
        LintianBrushChanger,
        MultiArchHintsChanger,
        ]

    def __init__(self) -> None:
        self.subchangers = [kls() for kls in self.SUBCHANGERS]

    @classmethod
    def setup_parser(cls, parser):
        pass

    @classmethod
    def from_args(cls, args):
        return cls()

    def suggest_branch_name(self):
        return BRANCH_NAME

    def make_changes(self, local_tree, subpath, update_changelog,
                     reporter, committer, base_proposal=None):
        base_revid = local_tree.last_revision()
        result = {}
        tags = []
        sufficient_for_proposal = False
        branches = []
        for subchanger in self.subchangers:
            subresult = (
                subchanger.make_changes(
                    local_tree, subpath, update_changelog, committer))
            result[subchanger] = subresult.mutator
            if subresult.sufficient_for_proposal:
                sufficient_for_proposal = True
            if subresult.tags:
                tags.extend(subresult.tags)
            if subresult.branches:
                branches.extend(
                    [entry for entry in subresult.branches
                     if entry[0] != 'main'])

        commit_items = []
        for subchanger in result:
            if isinstance(subchanger, LintianBrushChanger):
                commit_items.append('fix some lintian tags')
            if isinstance(subchanger, MultiArchHintsChanger):
                commit_items.append('apply multi-arch hints')
        proposed_commit_message = (', '.join(commit_items) + '.').capitalize()

        branches.insert(
            0, ('main', None, base_revid,
                local_tree.last_revision()))

        return ChangerResult(
            mutator=result,
            description='Fix various small issues.',
            tags=tags, branches=branches,
            sufficient_for_proposal=sufficient_for_proposal,
            proposed_commit_message=proposed_commit_message)

    def get_proposal_description(
            self, result, description_format, existing_proposal):
        entries = []
        for subchanger, memo in result.items():
            # TODO(jelmer): Does passing existing proposal in here work?
            entries.append(subchanger.get_proposal_description(
                memo, description_format, existing_proposal))
        return '\n'.join(entries)

    def describe(self, result, publish_result):
        if publish_result.is_new:
            note('Create merge proposal: %s', publish_result.proposal.url)
        elif result:
            note('Updated proposal %s', publish_result.proposal.url)
        else:
            note('No new fixes for proposal %s', publish_result.proposal.url)


if __name__ == '__main__':
    import sys
    sys.exit(run_mutator(TidyChanger))
