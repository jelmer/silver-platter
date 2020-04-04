#!/usr/bin/python3
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

"""Support for integration multi-arch hints."""

import argparse

import silver_platter  # noqa: F401

from lintian_brush import run_lintian_fixer, SUPPORTED_CERTAINTIES
from lintian_brush.config import Config

from .changer import (
    DebianChanger,
    run_changer,
    setup_multi_parser as setup_changer_parser,
    )

from breezy.trace import note

BRANCH_NAME = 'multi-arch-fixes'


class MultiArchHintsChanger(DebianChanger):

    name = 'apply-multi-arch-hints'

    @classmethod
    def setup_parser(cls, parser):
        # Hide the minimum-certainty option for the moment.
        parser.add_argument(
            '--minimum-certainty',
            type=str,
            choices=SUPPORTED_CERTAINTIES,
            default=None,
            help=argparse.SUPPRESS)
        parser.add_argument(
            '--allow-reformatting', default=None, action='store_true',
            help=argparse.SUPPRESS)

    @classmethod
    def from_args(cls, args):
        return cls(args.minimum_certainty, args.allow_reformatting)

    def __init__(self, minimum_certainty=None, allow_reformatting=None):
        from lintian_brush.multiarch_hints import (
            download_multiarch_hints,
            multiarch_hints_by_binary,
            parse_multiarch_hints,
            )
        with download_multiarch_hints() as f:
            self.hints = multiarch_hints_by_binary(parse_multiarch_hints(f))
        self.minimum_certainty = minimum_certainty
        self.allow_reformatting = allow_reformatting

    def suggest_branch_name(self):
        return BRANCH_NAME

    def make_changes(self, local_tree, subpath, update_changelog, committer):
        from lintian_brush.multiarch_hints import (
            MultiArchHintFixer,
            )
        minimum_certainty = self.minimum_certainty
        allow_reformatting = self.allow_reformatting
        try:
            cfg = Config.from_workingtree(local_tree, subpath)
        except FileNotFoundError:
            pass
        else:
            if minimum_certainty is None:
                minimum_certainty = cfg.minimum_certainty()
            if allow_reformatting is None:
                allow_reformatting = cfg.allow_reformatting()
            if update_changelog is None:
                update_changelog = cfg.update_changelog()

        result, summary = run_lintian_fixer(
            local_tree, MultiArchHintFixer(self.hints),
            update_changelog=update_changelog,
            minimum_certainty=minimum_certainty,
            subpath=subpath, allow_reformatting=allow_reformatting,
            net_access=True)

        return result

    def get_proposal_description(
            self, applied, description_format, existing_proposal):
        ret = ['Apply multi-arch hints.\n']
        for binary, hint, description, certainty in applied.changes:
            ret.append('* %s: %s\n' % (binary['Package'], description))
        return ''.join(ret)

    def get_commit_message(self, applied, existing_proposal):
        return 'Apply multi-arch hints.'

    def allow_create_proposal(self, applied):
        return True

    def describe(self, applied, publish_result):
        note('Applied multi-arch hints.')
        for binary, hint, description, certainty in applied.changes:
            note('* %s: %s', binary['Package'], description)

    def tags(self, applied):
        return []


def setup_parser(parser):
    setup_changer_parser(parser)
    MultiArchHintsChanger.setup_parser(parser)


def main(args):
    changer = MultiArchHintsChanger()
    return run_changer(changer, args)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(prog='multi-arch-hints')
    setup_parser(parser)
    args = parser.parse_args()
    main(args)
