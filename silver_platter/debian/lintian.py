#!/usr/bin/python
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

import argparse
import sys
from typing import List, Set

from debian.changelog import ChangelogCreateError

import breezy
from breezy.errors import BzrError
from breezy.trace import note

from lintian_brush import (
    available_lintian_fixers,
    run_lintian_fixers,
    DEFAULT_MINIMUM_CERTAINTY,
    SUPPORTED_CERTAINTIES,
    NotDebianPackage,
    version_string as lintian_brush_version_string,
    )
from lintian_brush.config import Config

import silver_platter

from . import (
    control_files_in_root,
    )
from .changer import (
    DebianChanger,
    ChangerResult,
    run_mutator,
    ChangerError,
    )

__all__ = [
    'available_lintian_fixers',
    'calculate_value',
    ]


DEFAULT_ADDON_FIXERS = [
    'debian-changelog-line-too-long',
    'file-contains-trailing-whitespace',
    'out-of-date-standards-version',
    'package-uses-old-debhelper-compat-version',
    'public-upstream-key-not-minimal',
    'no-dh-sequencer',
    ]

DEFAULT_VALUE_LINTIAN_BRUSH_ADDON_ONLY = 10
DEFAULT_VALUE_LINTIAN_BRUSH = 50
# Base these scores on the importance as set in Debian?
LINTIAN_BRUSH_TAG_VALUES = {
    'file-contains-trailing-whitespace': 0,
    }
LINTIAN_BRUSH_TAG_DEFAULT_VALUE = 5


BRANCH_NAME = "lintian-fixes"


class UnknownFixer(BzrError):
    """The specified fixer is unknown."""

    _fmt = "No such fixer: %s."

    def __init__(self, fixer):
        super(UnknownFixer, self).__init__(fixer=fixer)


def calculate_value(tags: Set[str]) -> int:
    if not (set(tags) - set(DEFAULT_ADDON_FIXERS)):
        value = DEFAULT_VALUE_LINTIAN_BRUSH_ADDON_ONLY
    else:
        value = DEFAULT_VALUE_LINTIAN_BRUSH
    for tag in tags:
        value += LINTIAN_BRUSH_TAG_VALUES.get(
            tag, LINTIAN_BRUSH_TAG_DEFAULT_VALUE)
    return value


def parse_mp_description(description: str) -> List[str]:
    """Parse a merge proposal description.

    Args:
      description: The description to parse
    Returns:
      list of one-line descriptions of changes
    """
    existing_lines = description.splitlines()
    if len(existing_lines) == 1:
        return existing_lines
    else:
        return [line[2:].rstrip('\n')
                for line in existing_lines if line.startswith('* ')]


def create_mp_description(description_format: str, lines: List[str]) -> str:
    """Create a merge proposal description.

    Args:
      lines: List of one-line descriptions of fixes
    Returns:
      A string with a merge proposal description
    """
    if len(lines) > 1:
        mp_description = ["Fix some issues reported by lintian\n"]
        for line in lines:
            line = "* %s\n" % line
            if line not in mp_description:
                mp_description.append(line)
    else:
        mp_description = [lines[0]]
    return ''.join(mp_description)


def applied_entry_as_line(description_format, fixed_lintian_tags, line):
    if not fixed_lintian_tags:
        return line
    if description_format == 'markdown':
        return '%s (%s)' % (line, ', '.join(
            ['[%s](https://lintian.debian.org/tags/%s.html)' % (tag, tag)
             for tag in fixed_lintian_tags]))
    return '%s (%s)' % (line, ', '.join(fixed_lintian_tags))


def update_proposal_description(
        description_format, existing_proposal, applied):
    if existing_proposal:
        existing_description = existing_proposal.get_description()
        existing_lines = parse_mp_description(existing_description)
    else:
        existing_lines = []
    return create_mp_description(
        description_format, existing_lines +
        [applied_entry_as_line(description_format, r.fixed_lintian_tags, l)
         for r, l in applied])


def update_proposal_commit_message(existing_proposal, applied):
    existing_commit_message = getattr(
        existing_proposal, 'get_commit_message', lambda: None)()
    if existing_commit_message and not existing_commit_message.startswith(
            'Fix lintian issues: '):
        # The commit message is something we haven't set - let's leave it
        # alone.
        return
    if existing_commit_message:
        existing_applied = existing_commit_message.split(':', 1)[1]
    else:
        existing_applied = []
    return "Fix lintian issues: " + (
        ', '.join(sorted(existing_applied + [l for r, l in applied])))


def has_nontrivial_changes(applied, propose_addon_only: Set[str]) -> bool:
    tags = set()
    for result, unused_summary in applied:
        tags.update(result.fixed_lintian_tags)
    # Is there enough to create a new merge proposal?
    return bool(tags - set(propose_addon_only))


def get_fixers(available_fixers, names=None, tags=None, exclude=None):
    """Get the set of fixers to try.

    Args:
      available_fixers: Dictionary mapping fixer names to objects
      names: Optional set of fixers to restrict to
      tags: Optional set of tags to restrict to
      exclude: Optional set of fixers to exclude
    Returns:
      List of fixer objects
    """
    if exclude is None:
        exclude = set()
    by_tag = {}
    by_name = {}
    for fixer in available_fixers:
        for tag in fixer.lintian_tags:
            by_tag[tag] = fixer
        by_name[fixer.name] = fixer

    # If it's unknown which fixers are relevant, just try all of them.
    if names:
        try:
            fixers = [by_name[name] for name in names]
        except KeyError as e:
            raise UnknownFixer(e.args[0])
    elif tags:
        fixers = [by_tag[tag] for tag in tags]
    else:
        fixers = list(by_name.values())

    if exclude:
        fixers = [fixer for fixer in fixers if fixer.name not in exclude]
    return fixers


class LintianBrushChanger(DebianChanger):

    name = 'lintian-brush'

    def __init__(
            self, names=None, exclude=None, propose_addon_only=None,
            compat_release=None, allow_reformatting=None,
            minimum_certainty=None, tags=None,
            opinionated=False, trust_package=False, diligence=0):
        self.fixers = get_fixers(
            available_lintian_fixers(), names=names,
            tags=tags, exclude=exclude)
        self.propose_addon_only = propose_addon_only or []
        self.compat_release = compat_release
        self.allow_reformatting = allow_reformatting
        self.minimum_certainty = minimum_certainty
        self.opinionated = opinionated
        self.trust_package = trust_package
        self.diligence = diligence

    @classmethod
    def setup_parser(cls, parser):
        parser.add_argument(
            "--fixers",
            help="Fixers to run.", type=str, action='append')
        parser.add_argument(
            '--exclude',
            help='Fixers to exclude.', type=str, action='append')
        parser.add_argument(
            '--propose-addon-only',
            help='Fixers that should be considered add-on-only.',
            type=str, action='append',
            default=DEFAULT_ADDON_FIXERS)
        parser.add_argument(
            '--compat-release', type=str, default=None,
            help='Oldest Debian release to be compatible with.')
        parser.add_argument(
            '--allow-reformatting', default=None, action='store_true',
            help='Whether to allow reformatting.')
        parser.add_argument(
            '--minimum-certainty',
            type=str,
            choices=SUPPORTED_CERTAINTIES,
            default=None,
            help=argparse.SUPPRESS)
        parser.add_argument(
            '--opinionated', action='store_true',
            help='Make opinionated changes')
        parser.add_argument(
            '--diligence', type=int, default=10,
            help=argparse.SUPPRESS)
        parser.add_argument(
            '--trust-package', action='store_true',
            help='Trust package.')
        parser.add_argument("tags", nargs='*')

    @classmethod
    def from_args(cls, args):
        return cls(names=args.fixers, exclude=args.exclude,
                   propose_addon_only=args.propose_addon_only,
                   compat_release=args.compat_release,
                   allow_reformatting=args.allow_reformatting,
                   minimum_certainty=args.minimum_certainty,
                   tags=args.tags, opinionated=args.opinionated,
                   diligence=args.diligence,
                   trust_package=args.trust_package)

    def suggest_branch_name(self):
        return BRANCH_NAME

    def make_changes(self, local_tree, subpath, update_changelog,
                     reporter, committer, base_proposal=None):
        base_revid = local_tree.last_revision()

        reporter.report_metadata('versions', {
            'lintian-brush': lintian_brush_version_string,
            'silver-platter': silver_platter.version_string,
            'breezy': breezy.version_string,
        })

        import distro_info
        debian_info = distro_info.DebianDistroInfo()

        compat_release = self.compat_release
        allow_reformatting = self.allow_reformatting
        minimum_certainty = None
        try:
            cfg = Config.from_workingtree(local_tree, subpath)
        except FileNotFoundError:
            pass
        else:
            compat_release = cfg.compat_release()
            if compat_release:
                compat_release = debian_info.codename(
                    compat_release, default=compat_release)
            allow_reformatting = cfg.allow_reformatting()
            minimum_certainty = cfg.minimum_certainty()
        if compat_release is None:
            compat_release = debian_info.stable()
        if allow_reformatting is None:
            allow_reformatting = False
        if minimum_certainty is None:
            minimum_certainty = DEFAULT_MINIMUM_CERTAINTY

        with local_tree.lock_write():
            if control_files_in_root(local_tree, subpath):
                raise ChangerError(
                    'control-files-in-root',
                    'control files live in root rather than debian/ '
                    '(LarstIQ mode)')

            try:
                overall_result = run_lintian_fixers(
                        local_tree, self.fixers,
                        committer=committer,
                        update_changelog=update_changelog,
                        compat_release=compat_release,
                        allow_reformatting=allow_reformatting,
                        minimum_certainty=minimum_certainty,
                        subpath=subpath, diligence=self.diligence,
                        opinionated=self.opinionated,
                        trust_package=self.trust_package)
            except NotDebianPackage:
                raise ChangerError(
                    'not-debian-package', 'Not a Debian package')
            except ChangelogCreateError as e:
                raise ChangerError(
                    'changelog-create-error',
                    'Error creating changelog entry: %s' % e)

        applied = []
        base_applied = reporter.get_base_metadata('applied', [])
        if base_applied:
            applied.extend(base_applied)
        for result, summary in overall_result.success:
            applied.append({
                'summary': summary,
                'description': result.description,
                'fixed_lintian_tags': result.fixed_lintian_tags,
                'revision_id': result.revision_id.decode('utf-8'),
                'certainty': result.certainty})
        reporter.report_metadata('applied', applied)

        if overall_result.failed_fixers:
            for fixer_name, failure in overall_result.failed_fixers.items():
                note('Fixer %r failed to run:', fixer_name)
                sys.stderr.write(str(failure))
        reporter.report_metadata(
            'failed', {
                name: str(e)
                for (name, e) in overall_result.failed_fixers.items()})

        if not overall_result.success:
            raise ChangerError('nothing-to-do', 'no fixers to apply')

        fixed_lintian_tags = set()
        for result, summary in overall_result.success:
            fixed_lintian_tags.update(result.fixed_lintian_tags)

        add_on_only = not has_nontrivial_changes(
            overall_result.success, self.propose_addon_only)

        if not reporter.get_base_metadata('add_on_only', False):
            add_on_only = False

        if not add_on_only:
            if overall_result.success:
                note('only add-on fixers found')
            sufficient_for_proposal = False
            reporter.report_metadata('add_on_only', True)
        else:
            sufficient_for_proposal = True
            reporter.report_metadata('add_on_only', False)

        branches = [
            ('main', None, base_revid,
             local_tree.last_revision())]

        return ChangerResult(
            description='Applied fixes for %r' % fixed_lintian_tags,
            mutator=overall_result.success,
            branches=branches, tags=[],
            value=calculate_value(fixed_lintian_tags),
            sufficient_for_proposal=sufficient_for_proposal,
            proposed_commit_message=update_proposal_commit_message(
                base_proposal, overall_result.success))

    def get_proposal_description(
            self, applied, description_format, existing_proposal):
        return update_proposal_description(
            description_format, existing_proposal, applied)

    def describe(self, applied, publish_result):
        tags = set()
        for brush_result, unused_summary in applied:
            tags.update(brush_result.fixed_lintian_tags)
        if publish_result.is_new:
            note('Proposed fixes %r: %s', tags, publish_result.proposal.url)
        elif tags:
            note('Updated proposal %s with fixes %r',
                 publish_result.proposal.url, tags)
        else:
            note('No new fixes for proposal %s', publish_result.proposal.url)


if __name__ == '__main__':
    sys.exit(run_mutator(LintianBrushChanger))
