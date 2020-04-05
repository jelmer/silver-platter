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

from urllib.parse import urlparse

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


class OrphanResult(object):

    def __init__(self, package=None, old_vcs_url=None, new_vcs_url=None,
                 salsa_user=None):
        self.package = package
        self.old_vcs_url = old_vcs_url
        self.new_vcs_url = new_vcs_url
        self.pushed = False
        self.salsa_user = salsa_user


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
        update_control(
            path=local_tree.abspath(
                osutils.pathjoin(subpath, 'debian/control')),
            source_package_cb=set_maintainer)

        result = OrphanResult()

        def set_vcs(source):
            result.package_name = source['Source']
            result.old_vcs_url = source.get('Vcs-Git')
            source['Vcs-Git'] = 'https://salsa.debian.org/%s/%s.git' % (
                self.salsa_user, result.package_name)
            result.new_vcs_url = source['Vcs-Git']
            source['Vcs-Browser'] = 'https://salsa.debian.org/%s/%s' % (
                self.salsa_user, result.package_name)
            result.salsa_user = self.salsa_user
        if self.update_vcs:
            update_control(
                path=local_tree.abspath(
                    osutils.pathjoin(subpath, 'debian/control')),
                source_package_cb=set_vcs)
            if result.old_vcs_url == result.new_vcs_url:
                result.old_vcs_url = result.new_vcs_url = None
        if update_changelog in (True, None):
            add_changelog_entry(
                local_tree,
                osutils.pathjoin(subpath, 'debian/changelog'),
                'Move package to QA team.', qa=True)
        local_tree.commit(
            'Move package to QA team.', committer=committer,
            allow_pointless=False)

        if self.update_vcs and self.salsa_push and result.new_vcs_url:
            push_to_salsa(
                local_tree, self.salsa_user, result.package_name,
                dry_run=self.dry_run)
            result.pushed = True
        return result

    def get_proposal_description(
            self, applied, description_format, existing_proposal):
        return 'Set the package maintainer to the QA team.'

    def get_commit_message(self, applied, existing_proposal):
        return 'Set the package maintainer to the QA team.'

    def allow_create_proposal(self, applied):
        return True

    def describe(self, result, publish_result):
        if publish_result.is_new:
            note('Proposed change of maintainer to QA team: %s',
                 publish_result.proposal.url)
        else:
            note('No changes for orphaned package %s', result.package_name)
        if result.pushed:
            note('Pushed new package to %s.', result.new_vcs_url)
        elif result.new_vcs_url:
            for line in move_instructions(
                    result.package_name, result.salsa_user, result.old_vcs_url,
                    result.new_vcs_url):
                note('%s', line)

    def tags(self):
        return []


def move_instructions(package_name, salsa_user, old_vcs_url, new_vcs_url):
    yield 'Please move the repository from %s to %s.' % (
         old_vcs_url, new_vcs_url)
    if urlparse(old_vcs_url).hostname == 'salsa.debian.org':
        path = urlparse(old_vcs_url).path
        if path.endswith('.git'):
            path = path[:-4]
        yield 'If you have the salsa(1) tool installed, run: '
        yield ''
        yield '    salsa fork --group=%s %s' % (
             salsa_user, path)
    else:
        yield 'If you have the salsa(1) tool installed, run: '
        yield ''
        yield '    git clone %s %s' % (old_vcs_url, package_name)
        yield '    salsa --group=%s push_repo %s' % (salsa_user, package_name)


def main(args):
    changer = OrphanChanger.from_args(args)
    return run_changer(changer, args)


def setup_parser(parser):
    from .changer import setup_multi_parser
    setup_multi_parser(parser)
    OrphanChanger.setup_parser(parser)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(prog='orphan')
    setup_parser(parser)
    args = parser.parse_args()
    main(args)
