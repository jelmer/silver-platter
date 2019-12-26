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

import silver_platter  # noqa: F401

from .changer import (
    DebianChanger,
    run_changer,
    setup_parser,
    )

BRANCH_NAME = 'multi-arch-fixes'


class MultiArchHintsChanger(DebianChanger):

    name = 'apply-multi-arch-hints'

    @classmethod
    def setup_parser(cls, parser):
        pass

    @classmethod
    def from_args(cls, args):
        return cls()

    def __init__(self):
        from lintian_brush.multiarch_hints import (
            download_multi_arch_hints,
            )
        self.hints = download_multi_arch_hints()

    def suggest_branch_name(self):
        return BRANCH_NAME

    def make_changes(self, local_tree, subpath, update_changelog, committer):
        from lintian_brush.multiarch_hints import (
            apply_multi_arch_hints,
            )
        applied_hints = apply_multi_arch_hints(
            local_tree, self.hints.get(package, []))
        return applied_hints

    def get_proposal_description(self, applied, existing_proposal):
        return 'Apply multi-arch hints.'

    def get_commit_message(self, applied, existing_proposal):
        return 'Apply multi-arch hints.'

    def allow_create_proposal(self, applied):
        return True

    def describe(self, applied, publish_result):
        raise NotImplementedError(self.describe)


def main(args):
    changer = MultiArchHintsChanger()
    return run_changer(changer, args)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(prog='multi-arch-hints')
    setup_parser(parser)
    MultiArchHintsChanger.setup_parser(parser)
    args = parser.parse_args()
    main(args)
