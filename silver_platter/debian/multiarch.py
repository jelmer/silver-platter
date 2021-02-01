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
from debmutate.reformatting import GeneratedFile, FormattingUnpreservable

from . import control_files_in_root, control_file_present, is_debcargo_package

from .changer import (
    DebianChanger,
    ChangerResult,
    ChangerError,
    run_mutator,
    )

from breezy.trace import note

BRANCH_NAME = 'multi-arch-fixes'


DEFAULT_VALUE_MULTIARCH_HINT = 50
MULTIARCH_HINTS_VALUE = {
    'ma-foreign': 20,
    'file-conflict': 50,
    'ma-foreign-library': 20,
    'dep-any': 20,
    'ma-same': 20,
    'arch-all': 20,
}


def calculate_value(hints):
    return sum(map(MULTIARCH_HINTS_VALUE.__getitem__, hints)) + (
        DEFAULT_VALUE_MULTIARCH_HINT)


class MultiArchHintsChanger(DebianChanger):

    name: str = 'apply-multiarch-hints'

    @classmethod
    def setup_parser(cls, parser: argparse.ArgumentParser) -> None:
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
            cache_download_multiarch_hints,
            multiarch_hints_by_binary,
            parse_multiarch_hints,
            )
        with cache_download_multiarch_hints() as f:
            self.hints = multiarch_hints_by_binary(parse_multiarch_hints(f))
        self.minimum_certainty = minimum_certainty
        self.allow_reformatting = allow_reformatting

    def suggest_branch_name(self):
        return BRANCH_NAME

    def make_changes(self, local_tree, subpath, update_changelog, reporter,
                     committer, base_proposal=None):
        from lintian_brush import NoChanges
        from lintian_brush.multiarch_hints import (
            MultiArchHintFixer,
            )
        base_revid = local_tree.last_revision()
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

        if control_files_in_root(local_tree, subpath):
            raise ChangerError(
                'control-files-in-root',
                'control files live in root rather than debian/ '
                '(LarstIQ mode)')

        if not control_file_present(local_tree, subpath):
            if is_debcargo_package(local_tree, subpath):
                raise ChangerError(
                    'debcargo-package', 'Package uses debcargo')
            raise ChangerError(
                'missing-control-file', 'Unable to find debian/control')

        try:
            with local_tree.lock_write():
                result, summary = run_lintian_fixer(
                    local_tree, MultiArchHintFixer(self.hints),
                    update_changelog=update_changelog,
                    minimum_certainty=minimum_certainty,
                    subpath=subpath, allow_reformatting=allow_reformatting,
                    net_access=True, committer=committer,
                    changes_by='apply-multiarch-hints')
        except NoChanges:
            raise ChangerError('nothing-to-do', 'no hints to apply')
        except FormattingUnpreservable as e:
            raise ChangerError(
                'formatting-unpreservable',
                'unable to preserve formatting while editing %s' % e.path)
        except GeneratedFile as e:
            raise ChangerError(
                'generated-file',
                'unable to edit generated file: %r' % e)

        applied_hints = []
        hint_names = []
        for (binary, hint, description, certainty) in result.changes:
            hint_names.append(hint['link'].split('#')[-1])
            entry = dict(hint.items())
            hint_names.append(entry['link'].split('#')[-1])
            entry['action'] = description
            entry['certainty'] = certainty
            applied_hints.append(entry)
            note('%s: %s' % (binary['Package'], description))

        reporter.report_metadata('applied-hints', applied_hints)

        branches = [
            ('main', None, base_revid,
             local_tree.last_revision())]

        tags = []

        return ChangerResult(
            description="Applied multi-arch hints.", mutator=result,
            branches=branches, tags=tags,
            value=calculate_value(hint_names),
            sufficient_for_proposal=True,
            proposed_commit_message='Apply multi-arch hints.')

    def get_proposal_description(
            self, applied, description_format, existing_proposal):
        ret = ['Apply multi-arch hints.\n']
        for binary, hint, description, certainty in applied.changes:
            ret.append('* %s: %s\n' % (binary['Package'], description))
        return ''.join(ret)

    def describe(self, applied, publish_result):
        note('Applied multi-arch hints.')
        for binary, hint, description, certainty in applied.changes:
            note('* %s: %s', binary['Package'], description)

    @classmethod
    def describe_command(cls, command):
        return "Apply multi-arch hints"


if __name__ == '__main__':
    import sys
    sys.exit(run_mutator(MultiArchHintsChanger))
