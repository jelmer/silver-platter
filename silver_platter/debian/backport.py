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

from .changer import (
    run_mutator,
    DebianChanger,
    ChangerResult,
    )
from breezy.trace import note


# See https://backports.debian.org/Contribute/


class BackportResult(object):

    def __init__(self, target_release):
        self.target_release = target_release


class BackportChanger(DebianChanger):

    name = 'backport'

    def __init__(self, dry_run=False, target_release=None, sloppy=False):
        self.dry_run = dry_run
        self.target_release = target_release
        self.sloppy = sloppy

    @classmethod
    def setup_parser(cls, parser):
        distro_info = DebianDistroInfo()
        parser.add_argument(
            '--target-release', type=str,
            help='Target release', default=distro_info.stable())
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Do a dry run.')

    @classmethod
    def from_args(cls, args):
        return cls(target_release=args.target_release)

    def suggest_branch_name(self):
        return 'backport-%s' % self.target_release

    def target_suite(self):
        suite = '%s-backports' % self.target_release
        if self.sloppy:
            suite += '-sloppy'
        return suite

    def make_changes(self, local_tree, subpath, update_changelog, reporter,
                     committer, base_proposal=None):
        # TODO(jelmer): Check that package has a high enough popcon count,
        # and warn otherwise?
        # TODO(jelmer): Iterate Build-Depends and verify that depends are
        # satisfied by self.target_suite()
        # TODO(jelmer): Update changelog
        # TODO(jelmer): Try to build
        return ChangerResult(
            description=None, mutator=None,
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
