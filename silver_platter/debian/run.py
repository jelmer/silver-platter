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

"""Support for updating with a script."""

from breezy.trace import note

from .changer import (
    ChangerError,
    DebianChanger,
    run_single_changer,
    setup_single_parser,
    )
from ..run import (
    ScriptMadeNoChanges,
    derived_branch_name,
    script_runner,
    )


def setup_parser(parser):
    setup_single_parser(parser)
    ScriptChanger.setup_parser(parser)


class ScriptChanger(DebianChanger):

    def _init__(self, script, commit_pending=None):
        self.script = script
        self.commit_pending = commit_pending

    @classmethod
    def setup_parser(cls, parser):
        parser.add_argument(
            'script', help='Path to script to run.', type=str)
        parser.add_argument(
            '--commit-pending',
            help='Commit pending changes after script.',
            choices=['yes', 'no', 'auto'],
            default='auto', type=str)

    @classmethod
    def from_args(cls, args):
        commit_pending = {'auto': None, 'yes': True, 'no': False}[
            args.commit_pending]
        return cls(script=args.script, commit_pending=commit_pending)

    def make_changes(self, local_tree, subpath, update_changelog, committer):
        try:
            description = script_runner(
                local_tree, self.script, self.commit_pending)
        except ScriptMadeNoChanges as e:
            raise ChangerError('Script did not make any changes.', e)
        return description

    def get_proposal_description(
            self, description, description_format, existing_proposal):
        if description is not None:
            return description
        if existing_proposal is not None:
            return existing_proposal.get_description()
        raise ValueError("No description available")

    def get_commit_message(self, applied, existing_proposal):
        return None

    def allow_create_proposal(self, applied):
        return True

    def describe(self, description, publish_result):
        note('%s', description)

    def suggest_branch_name(self):
        return derived_branch_name(self.script)

    def tags(self):
        return None


def main(args):
    changer = ScriptChanger(args)
    return run_single_changer(changer, args)
