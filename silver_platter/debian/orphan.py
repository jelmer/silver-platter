#!/usr/bin/python
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

from __future__ import absolute_import

from . import pick_additional_colocated_branches
from .changer import (
    run_changer,
    DebianChanger,
    )
from ..proposal import push_changes
from breezy import osutils
from breezy.trace import note

from lintian_brush import add_changelog_entry
from lintian_brush.control import update_control


BRANCH_NAME = 'orphan'


def _update_control(
        local_tree, subpath, update_changelog, committer,
        source_package_cb, message):
    changed = update_control(
        path=local_tree.abspath(
            osutils.pathjoin(subpath, 'debian/control')),
        source_package_cb=source_package_cb)
    if not changed:
        return False
    if update_changelog in (True, None):
        add_changelog_entry(
            local_tree,
            osutils.pathjoin(subpath, 'debian/changelog'),
            message, qa=True)
    local_tree.commit(message, committer=committer, allow_pointless=False)
    return True


def push_to_salsa(local_tree, user, name, dry_run=False):
    from breezy.branch import Branch
    from breezy.plugins.propose.gitlabs import GitLab
    salsa = GitLab.probe_from_url('https://salsa.debian.org/')
    # TODO(jelmer): Fork if the old branch was hosted on salsa
    salsa.create_project('%s/%s' % (user, name))
    target_branch = Branch.open(
        'git+ssh://git@salsa.debian.org/%s/%s.git' % (user, name))
    additional_colocated_branches = pick_additional_colocated_branches(
        local_tree.branch)
    return push_changes(
        local_tree.branch, target_branch, hoster=salsa,
        additional_colocated_branches=additional_colocated_branches,
        dry_run=dry_run)


class OrphanChanger(DebianChanger):

    def __init__(self, update_vcs=True, salsa_push=True,
                 salsa_user='debian', dry_run=False):
        self.update_vcs = update_vcs
        self.salsa_push = salsa_push
        self.salsa_user = salsa_user
        self.dry_run = dry_run

    @classmethod
    def setup_parser(cls, parser):
        parser.add_argument(
            '--no-update-vcs', action='store_true',
            help='Do not move the VCS repository to the Debian team on Salsa.')
        parser.add_argument(
            '--salsa-user', type=str, default='debian',
            help='Salsa user to push repository to.')
        parser.add_argument(
            '--just-update-headers', action='store_true',
            help='Update the VCS-* headers, but don\'t actually '
            'clone the repository.')

    @classmethod
    def from_args(cls, args):
        return cls(
            update_vcs=not args.no_update_vcs,
            dry_run=args.dry_run,
            salsa_user=args.salsa_user,
            salsa_push=not args.just_update_headers)

    def suggest_branch_name(self):
        return BRANCH_NAME

    def make_changes(self, local_tree, subpath, update_changelog, committer):
        def set_maintainer(source):
            source['Maintainer'] = 'Debian QA Group <packages@qa.debian.org>'
            try:
                del source['Uploaders']
            except KeyError:
                pass
        _update_control(
            local_tree, subpath, update_changelog, committer, set_maintainer,
            'Orphan package.')

        def set_vcs(source):
            global package_name
            package_name = source['Source']
            source['Vcs-Git'] = 'https://salsa.debian.org/%s/%s.git' % (
                self.salsa_user, package_name)
            source['Vcs-Browser'] = 'https://salsa.debian.org/%s/%s' % (
                self.salsa_user, package_name)
        if self.update_vcs:
            changed = _update_control(
                local_tree, subpath, update_changelog, committer,
                set_vcs, 'Point Vcs-* headers at salsa.')
            if not self.salsa_push and changed:
                push_to_salsa(
                    local_tree, self.salsa_user, package_name,
                    dry_run=self.dry_run)
        return {}

    def get_proposal_description(
            self, applied, description_format, existing_proposal):
        return 'Set the package maintainer to the QA team.'

    def get_commit_message(self, applied, existing_proposal):
        return 'Set the package maintainer to the QA team.'

    def allow_create_proposal(self, applied):
        return True

    def describe(self, description, publish_result):
        if publish_result.is_new:
            note('Proposed change of maintainer to QA team: %s',
                 publish_result.proposal.url)
        else:
            note('No changes to proposal %s', publish_result.proposal.url)


def main(args):
    changer = OrphanChanger.from_args(args)
    return run_changer(changer, args)


def setup_parser(parser):
    from .changer import setup_parser
    setup_parser(parser)
    OrphanChanger.setup_parser(parser)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(prog='orphan')
    setup_parser(parser)
    OrphanChanger.setup_parser(parser)
    args = parser.parse_args()
    main(args)
