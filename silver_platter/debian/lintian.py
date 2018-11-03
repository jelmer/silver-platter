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

from __future__ import absolute_import

__all__ = [
    'available_lintian_fixers',
    'PostCheckFailed',
    'LintianFixer',
    ]

from breezy.errors import BzrError
from breezy.trace import note
from lintian_brush import (
    available_lintian_fixers,
    run_lintian_fixers,
    )

from . import (
    build,
    should_update_changelog,
    )
from ..proposal import BranchChanger


class PostCheckFailed(BzrError):
    """The post check failed."""

    _fmt = "Running post-check failed."

    def __init__(self):
        super(PostCheckFailed, self).__init__()


def read_lintian_log(f):
    """Read a lintian log file.

    Args:
      f: file-like object to read from
    Returns:
      dictionary mapping packages to sets of lintian tags
    """
    lintian_errs = {}
    for l in f:
        cs = l.split(' ')
        if cs[0] not in ('E:', 'W:', 'I:', 'P:'):
            continue
        pkg = cs[1]
        err = cs[5].strip()
        lintian_errs.setdefault(pkg, set()).add(err)
    return lintian_errs


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


def create_mp_description(lines):
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


class LintianFixer(BranchChanger):
    """BranchChanger that fixes lintian issues."""

    def __init__(self, pkg, fixers, update_changelog, build_verify=False,
                 pre_check=None, post_check=None, propose_addon_only=None,
                 committer=None):
        self._pkg = pkg
        self._update_changelog = update_changelog
        self._build_verify = build_verify
        self._pre_check = pre_check
        self._post_check = post_check
        self._fixers = fixers
        self._propose_addon_only = propose_addon_only
        self._committer = committer

    def __repr__(self):
        return "LintianFixer(%r)" % (self._pkg, )

    def make_changes(self, local_tree):
        with local_tree.lock_write():
            if not local_tree.has_filename('debian/control'):
                note('%r: missing control file', self)
                return
            since_revid = local_tree.last_revision()
            if self._pre_check:
                if not self._pre_check(local_tree):
                    return
            if self._update_changelog == 'auto':
                update_changelog = should_update_changelog(local_tree.branch)
            elif self._update_changelog == 'update':
                update_changelog = True
            elif self._update_changelog == 'leave':
                update_changelog = False

            self.applied, failed = run_lintian_fixers(
                    local_tree, self._fixers,
                    committer=self._committer,
                    update_changelog=update_changelog)
            if failed:
                note('%r: some fixers failed to run: %r',
                     self, failed)
            if not self.applied:
                note('%r: no fixers to apply', self)
                return

        if self._post_check:
            if not self._post_check(local_tree, since_revid):
                raise PostCheckFailed()

        if self._build_verify:
            build(local_tree.basedir)

    def get_proposal_description(self, existing_proposal):
        if existing_proposal:
            existing_description = existing_proposal.get_description()
            existing_lines = parse_mp_description(existing_description)
        else:
            existing_lines = []
        return create_mp_description(
            existing_lines + [l for f, l in self.applied])

    def should_create_proposal(self):
        tags = set()
        for fixed_tags, unused_summary in self.applied:
            tags.update(fixed_tags)
        # Is there enough to create a new merge proposal?
        if not tags - self._propose_addon_only:
            note('%r: only add-on fixers found', self)
            return False
        return True
