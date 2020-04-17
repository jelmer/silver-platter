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

from breezy.errors import BzrError
from breezy.trace import note

from lintian_brush import (
    available_lintian_fixers,
    run_lintian_fixers,
    DEFAULT_MINIMUM_CERTAINTY,
    )
from lintian_brush.config import Config

from .changer import (
    run_changer,
    DebianChanger,
    setup_multi_parser as setup_changer_parser,
    )

__all__ = [
    'available_lintian_fixers',
    ]


DEFAULT_ADDON_FIXERS = [
    'file-contains-trailing-whitespace',
    'out-of-date-standards-version',
    'package-uses-old-debhelper-compat-version',
    'public-upstream-key-not-minimal',
    'no-dh-sequencer',
    ]
BRANCH_NAME = "lintian-fixes"


class UnknownFixer(BzrError):
    """The specified fixer is unknown."""

    _fmt = "No such fixer: %s."

    def __init__(self, fixer):
        super(UnknownFixer, self).__init__(fixer=fixer)


def parse_mp_description(description):
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
        return [l[2:].rstrip('\n')
                for l in existing_lines if l.startswith('* ')]


def create_mp_description(description_format, lines):
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
        mp_description = lines[0]
    return ''.join(mp_description)


def applied_entry_as_line(description_format, fixed_lintian_tags, l):
    if not fixed_lintian_tags:
        return l
    if description_format == 'markdown':
        return '%s (%s)' % (l, ', '.join(
            ['[%s](https://lintian.debian.org/tags/%s.html)' % (tag, tag)
             for tag in fixed_lintian_tags]))
    return '%s (%s)' % (l, ', '.join(fixed_lintian_tags))


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


def has_nontrivial_changes(applied, propose_addon_only):
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

    def __init__(self, names=None, exclude=None, propose_addon_only=None):
        self.fixers = get_fixers(
            available_lintian_fixers(), names=names,
            exclude=exclude)
        self.propose_addon_only = propose_addon_only or []

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

    @classmethod
    def from_args(cls, args):
        return cls(names=args.fixers, exclude=args.exclude,
                   propose_addon_only=args.propose_addon_only)

    def suggest_branch_name(self):
        return BRANCH_NAME

    def make_changes(self, local_tree, subpath, update_changelog, committer):
        import distro_info
        debian_info = distro_info.DebianDistroInfo()

        compat_release = None
        allow_reformatting = None
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

        applied, failed = run_lintian_fixers(
                local_tree, self.fixers,
                committer=committer,
                update_changelog=update_changelog,
                compat_release=compat_release,
                allow_reformatting=allow_reformatting,
                minimum_certainty=minimum_certainty,
                subpath=subpath)

        if failed:
            note('some fixers failed to run: %r', set(failed))

        return applied

    def get_proposal_description(
            self, applied, description_format, existing_proposal):
        return update_proposal_description(
            description_format, existing_proposal, applied)

    def get_commit_message(self, applied, existing_proposal):
        return update_proposal_commit_message(
            existing_proposal, applied)

    def allow_create_proposal(self, applied):
        if not has_nontrivial_changes(applied, self.propose_addon_only):
            note('only add-on fixers found')
            return False
        else:
            return True

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

    def tags(self, applied):
        return []


def setup_parser(parser):
    LintianBrushChanger.setup_parser(parser)
    setup_changer_parser(parser)


def main(args):
    try:
        changer = LintianBrushChanger.from_args(args)
    except UnknownFixer as e:
        note('Unknown fixer: %s', e.fixer)
        return 1

    return run_changer(changer, args)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(prog='propose-lintian-fixes')
    setup_parser(parser)
    args = parser.parse_args()
    main(args)
