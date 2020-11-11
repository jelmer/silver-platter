#!/usr/bin/python3
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

"""Support for scrubbing obsolete settings."""

import argparse

from debmutate.reformatting import GeneratedFile, FormattingUnpreservable

import silver_platter  # noqa: F401

from lintian_brush.config import Config

from .changer import (
    DebianChanger,
    ChangerError,
    ChangerResult,
    run_mutator,
    )

from breezy.trace import note

BRANCH_NAME = 'scrub-obsolete'
DEFAULT_VALUE_MULTIARCH_HINT = 30


def calculate_value(result):
    value = DEFAULT_VALUE_MULTIARCH_HINT
    for para, changes in result.control_removed:
        for field, packages in changes:
            value += len(packages) * 2
    for path, removed in result.maintscript_removed:
        value += len(removed)
    return value


class ScrubObsoleteChanger(DebianChanger):

    name: str = 'scrub-obsolete'

    @classmethod
    def setup_parser(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            '--allow-reformatting', default=None, action='store_true',
            help=argparse.SUPPRESS)
        parser.add_argument(
            '--upgrade-release', metavar='UPGRADE-RELEASE',
            help='Release to allow upgrading from.', default='oldstable')

    @classmethod
    def from_args(cls, args):
        import distro_info
        debian_info = distro_info.DebianDistroInfo()
        upgrade_release = debian_info.codename(args.upgrade_release)
        return cls(
            allow_reformatting=args.allow_reformatting,
            upgrade_release=upgrade_release)

    def __init__(self, upgrade_release, allow_reformatting=None):
        self.allow_reformatting = allow_reformatting
        self.upgrade_release = upgrade_release

    def suggest_branch_name(self):
        return BRANCH_NAME

    def make_changes(self, local_tree, subpath, update_changelog,
                     reporter, committer, base_proposal=None):
        from lintian_brush.scrub_obsolete import scrub_obsolete
        base_revid = local_tree.last_revision()
        allow_reformatting = self.allow_reformatting
        try:
            cfg = Config.from_workingtree(local_tree, subpath)
        except FileNotFoundError:
            pass
        else:
            if allow_reformatting is None:
                allow_reformatting = cfg.allow_reformatting()
            if update_changelog is None:
                update_changelog = cfg.update_changelog()

        try:
            result = scrub_obsolete(
                local_tree, subpath, self.upgrade_release,
                update_changelog=update_changelog)
        except FormattingUnpreservable as e:
            raise ChangerError(
                'formatting-unpreservable',
                'unable to preserve formatting while editing %s' % e.path)
        except GeneratedFile as e:
            raise ChangerError(
                'generated-file',
                'unable to edit generated file: %r' % e)

        if not result:
            raise ChangerError('nothing-to-do', 'no obsolete constraints')

        branches = [
            ('main', None, base_revid,
             local_tree.last_revision())]

        tags = []

        return ChangerResult(
            description="Scrub obsolete settings.", mutator=result,
            branches=branches, tags=tags,
            value=calculate_value(result),
            sufficient_for_proposal=True,
            proposed_commit_message='Scrub obsolete settings.')

    def get_proposal_description(
            self, result, description_format, existing_proposal):
        ret = [
            'Remove constraints unnecessary since %s.' % self.upgrade_release,
            ''] + ['* ' + line for line in result.itemized()]
        return ''.join(ret)

    def describe(self, applied, publish_result):
        note('Scrub obsolete settings.')
        for line in applied.itemized():
            note('* %s', line)

    @classmethod
    def describe_command(cls, command):
        return "Remove obsolete dependencies"


if __name__ == '__main__':
    import sys
    sys.exit(run_mutator(ScrubObsoleteChanger))
