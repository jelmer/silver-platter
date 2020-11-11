#!/usr/bin/python
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

from distro_info import DebianDistroInfo

import os
import re
import tempfile

from . import (
    DEFAULT_BUILDER,
    )
from .changer import (
    run_mutator,
    DebianChanger,
    ChangerResult,
    )
from breezy.plugins.debian.cmds import _build_helper
from breezy.plugins.debian.util import (
    dput_changes,
    debsign,
    )
from breezy.trace import note

from debian.changelog import format_date, get_maintainer
from debmutate.changelog import ChangelogEditor, changeblock_add_line


# See https://backports.debian.org/Contribute/


class BackportResult(object):

    def __init__(self, target_release):
        self.target_release = target_release


def backport_suffix(release):
    distro_info = DebianDistroInfo()
    version = distro_info.version(release)
    return 'bpo%s' % version


def backport_distribution(release):
    distro_info = DebianDistroInfo()
    if distro_info.codename('stable') == release:
        return '%s-backports' % release
    elif distro_info.codename('oldstable') == release:
        return '%s-backports-sloppy' % release
    else:
        raise Exception('unable to determine target suite for %s' % release)


def create_bpo_version(orig_version, bpo_suffix):
    m = re.fullmatch(r'(.*)\~' + bpo_suffix + r'\+([0-9]+)', str(orig_version))
    if m:
        base = m.group(1)
        buildno = int(m.group(2)) + 1
    else:
        base = str(orig_version)
        buildno = 1
    return '%s~%s+%d' % (base, bpo_suffix, buildno)


def backport_package(local_tree, subpath, target_release, author=None):
    changes = []
    # TODO(jelmer): Check that package has a high enough popcon count,
    # and warn otherwise?
    # TODO(jelmer): Iterate Build-Depends and verify that depends are
    # satisfied by target_distribution
    # TODO(jelmer): Update Vcs-Git/Vcs-Browser header?
    target_distribution = backport_distribution(target_release)
    version_suffix = backport_suffix(target_release)
    note('Using target distribution %s, version suffix %s',
         target_distribution, version_suffix)
    clp = local_tree.abspath(os.path.join(subpath, 'debian/changelog'))

    if author is None:
        author = '%s <%s>' % get_maintainer()

    with ChangelogEditor(clp) as cl:
        # TODO(jelmer): If there was an existing backport, use that version
        since_version = cl[0].version
        cl.new_block(
            package=cl[0].package,
            distributions=target_distribution,
            urgency='low',
            author=author,
            date=format_date(),
            version=create_bpo_version(since_version, version_suffix))
        block = cl[0]
        changeblock_add_line(
            block,
            ['Backport to %s.' % target_release] +
            [' +' + line for line in changes])

    return since_version


class BackportChanger(DebianChanger):

    name = 'backport'

    def __init__(self, dry_run=False, target_release=None, builder=None):
        self.dry_run = dry_run
        self.target_release = target_release
        self.builder = builder

    @classmethod
    def setup_parser(cls, parser):
        distro_info = DebianDistroInfo()
        parser.add_argument(
            '--target-release', type=str,
            help='Target release', default=distro_info.stable())
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Do a dry run.')
        parser.add_argument(
            '--builder',
            type=str,
            help='Build command',
            default=(DEFAULT_BUILDER + ' --source --source-only-changes '
                     '--debbuildopt=-v${LAST_VERSION}'))

    @classmethod
    def from_args(cls, args):
        return cls(target_release=args.target_release, dry_run=args.dry_run,
                   builder=args.builder)

    def suggest_branch_name(self):
        return backport_distribution(self.target_release)

    def make_changes(self, local_tree, subpath, update_changelog, reporter,
                     committer, base_proposal=None):

        base_revision = local_tree.last_revision()

        since_version = backport_package(
            local_tree, subpath, self.target_release, author=committer)

        with tempfile.TemporaryDirectory() as td:
            builder = self.builder.replace(
                "${LAST_VERSION}", str(since_version))
            target_changes = _build_helper(
                local_tree, subpath, local_tree.branch, td, builder=builder)
            debsign(target_changes)

            if not self.dry_run:
                dput_changes(target_changes)

        branches = [
            ('main', None, base_revision,
             local_tree.last_revision())]

        # TODO(jelmer): Add debian/... tag
        tags = []

        return ChangerResult(
            description=None, mutator=None, branches=branches,
            tags=tags,
            proposed_commit_message='Backport to %s.' % self.target_release,
            sufficient_for_proposal=True)

    def get_proposal_description(
            self, applied, description_format, existing_proposal):
        return 'Backport to %s.' % self.target_release

    def describe(self, result, publish_result):
        if publish_result.is_new:
            note('Proposed backportg to %s: %s',
                 result.target_release,
                 publish_result.proposal.url)
        else:
            note('No changes for package %s', result.package_name)


if __name__ == '__main__':
    import sys
    sys.exit(run_mutator(BackportChanger))
