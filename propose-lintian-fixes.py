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

import apt_pkg
from debian.deb822 import Deb822
from email.utils import parseaddr
import fnmatch
import itertools
import os
import shutil
import socket
import sys
import tempfile

if os.name == "posix":
    import locale
    locale.setlocale(locale.LC_ALL, '')
    # Use better default than ascii with posix filesystems that deal in bytes
    # natively even when the C locale or no locale at all is given. Note that
    # we need an immortal string for the hack, hence the lack of a hyphen.
    sys._brz_default_fs_enc = "utf8"

import breezy
breezy.initialize()
import breezy.git
import breezy.bzr
import breezy.plugins.launchpad
from breezy.plugins.debian.directory import source_package_vcs_url
from breezy import (
    errors,
    merge as _mod_merge,
    urlutils,
    )

from breezy.branch import Branch
from breezy.commit import PointlessCommit
from breezy.trace import note

from breezy.plugins.propose.propose import (
    get_hoster,
    NoMergeProposal,
    UnsupportedHoster,
    )

from lintian_brush import available_lintian_fixers, run_lintian_fixers

import argparse
parser = argparse.ArgumentParser()
parser.add_argument("packages", nargs='*')
parser.add_argument('--lintian-log', help="Path to lintian log file.", type=str, default='lintian.log')
parser.add_argument("--fixers", help="Fixers to run.", type=str, action='append')
parser.add_argument("--ignore", help="Packages to ignore.", type=str, action='append', default=[])
parser.add_argument("--ignore-file", help="File to load packages to ignore from.",
                    type=str, action='append', default=[])
parser.add_argument('--just-push-file', type=str, action='append', default=[],
                    help=('File with maintainer emails for which just to push, '
                          'rather than propose changes.'))
parser.add_argument('--propose-file', type=str, action='append', default=[],
                    help=('File with maintainer emails for which to propose changes. '))
parser.add_argument('--propose-addon-only', help='Fixers that should be considered add-on-only.',
                    type=str, action='append',
                    default=['file-contains-trailing-whitespace'])
args = parser.parse_args()

fixer_scripts = {f.tag: f for f in available_lintian_fixers()}


def read_lintian_log(f):
    lintian_errs = {}
    for l in f:
        cs = l.split(' ')
        if cs[0] not in ('E:', 'W:', 'I:', 'P:'):
            continue
        pkg = cs[1]
        err = cs[5].strip()
        lintian_errs.setdefault(pkg, set()).add(err)
    return lintian_errs


def get_maintainer_and_uploader_emails(control):
    yield parseaddr(control["Maintainer"])[1]
    for uploader in control.get("Uploaders", "").split(","):
        yield parseaddr(uploader)[1]


with open(args.lintian_log, 'r') as f:
    lintian_errs = read_lintian_log(f)


ignore_packages = set()
for ignore_match in args.ignore:
    ignore_packages.update(fnmatch.filter(lintian_errs.keys(), ignore_match))

for ignore_file in args.ignore_file:
    with open(ignore_file, 'rb') as f:
        for l in f:
            ignore_packages.add(l.split('#')[0].strip())

just_push_maintainers = set()
for just_push_file in args.just_push_file:
    with open(just_push_file, 'r') as f:
        for l in f:
            just_push_maintainers.add(l.split('#')[0].strip())

propose_owners = None
if args.propose_file:
    propose_owners = set()
    for propose_file in args.propose_file:
        with open(propose_file, 'r') as f:
            for l in f:
                propose_owners.add(l.split('#')[0].strip())


def should_update_changelog(branch):
    with branch.lock_read():
        graph = branch.repository.get_graph()
        revids = itertools.islice(
            graph.iter_lefthand_ancestry(branch.last_revision()), 200)
        for revid, rev in branch.repository.iter_revisions(revids):
            if rev is None:
                # Ghost
                continue
            if 'Git-Dch: ' in rev.message:
                return False
    # Assume yes
    return True


propose_addon_only = set(args.propose_addon_only)


available_fixers = set(fixer_scripts)
if args.fixers:
    available_fixers = available_fixers.intersection(set(args.fixers))


todo = set()
if not args.packages:
    todo = set(lintian_errs.keys())
else:
    for pkg_match in args.packages:
        todo.update(fnmatch.filter(lintian_errs.keys(), pkg_match))


apt_pkg.init()

sources = apt_pkg.SourceRecords()

todo = todo - ignore_packages

note("Considering %d packages for automatic change proposals", len(todo))

def create_mp_description(lines):
    if len(applied) > 1:
        mp_description = ["Fix some issues reported by lintian\n"]
        for l in lines:
            l = "* %s\n" % l
            if l not in mp_description:
                mp_description.append(l)
    else:
        mp_description = lines[0]
    return ''.join(mp_description)


for pkg in sorted(todo):
    errs = lintian_errs[pkg]

    fixers = available_fixers.intersection(errs)
    if not fixers:
        continue

    if not (fixers - propose_addon_only):
        continue

    if not sources.lookup(pkg):
        note('%s: not in apt sources', pkg)
        continue
    pkg_source = Deb822(sources.record)
    try:
        vcs_url = source_package_vcs_url(pkg_source)
    except KeyError:
        note('%s: no VCS URL found', pkg)
        continue

    if propose_owners is None:
        mode = 'propose'
    else:
        mode = None
    emails = list(get_maintainer_and_uploader_emails(pkg_source))
    for email in emails:
        if email in just_push_maintainers:
            mode = 'push'
            break
        elif propose_owners is None or email in propose_owners:
            mode = 'propose'
    if not mode:
        continue

    try:
        main_branch = Branch.open(vcs_url)
    except socket.error:
        note('%s: ignoring, socket error', pkg)
    except urlutils.InvalidURL as e:
        if ('unsupported VCSes' in e.extra or
            'no URLs found' in e.extra or
            'only Vcs-Browser set' in e.extra):
            note('%s: %s', pkg, e.extra)
        else:
            raise
    except errors.NotBranchError as e:
        note('%s: Branch does not exist: %s', pkg, e)
    except errors.UnsupportedProtocol:
        note('%s: Branch available over unsupported protocol', pkg)
    except errors.ConnectionError as e:
        note('%s: %s', pkg, e)
    except errors.PermissionDenied as e:
        note('%s: %s', pkg, e)
    except errors.InvalidHttpResponse as e:
        note('%s: %s', pkg, e)
    except errors.TransportError as e:
        note('%s: %s', pkg, e)
    else:
        name = "lintian-fixes"
        overwrite = False
        try:
            hoster = get_hoster(main_branch)
        except UnsupportedHoster:
            note('%s: Hoster unsupported', pkg)
            continue
        if mode == 'propose':
            try:
                existing_branch = hoster.get_derived_branch(main_branch, name=name)
            except errors.NotBranchError:
                base_branch = main_branch
                existing_branch = None
                existing_proposal = None
            else:
                note('%s: Already proposed: %s (branch at %s)', pkg, name,
                     existing_branch.user_url)
                base_branch = existing_branch
                try:
                    existing_proposal = hoster.get_proposal(existing_branch, main_branch)
                except NoMergeProposal:
                    existing_proposal = None
        else:
            base_branch = main_branch
        td = tempfile.mkdtemp()
        try:
            # preserve whatever source format we have.
            to_dir = base_branch.controldir.sprout(td, None, create_tree_if_local=True,
                    source_branch=base_branch, stacked=base_branch._format.supports_stacking())
            local_tree = to_dir.open_workingtree()
            main_branch_revid = main_branch.last_revision()
            with local_tree.branch.lock_read():
                if (mode == 'propose' and
                    existing_branch is not None and
                    not local_tree.branch.repository.get_graph().is_ancestor(
                        main_branch_revid, local_tree.branch.last_revision())):
                    local_tree.branch.repository.fetch(
                            main_branch.repository, revision_id=main_branch_revid)
                    # Reset custom merge hooks, since they could make it harder to detect
                    # conflicted merges that would appear on the hosting site.
                    old_file_content_mergers = _mod_merge.Merger.hooks['merge_file_content']
                    _mod_merge.Merger.hooks['merge_file_content'] = []
                    try:
                        merger = _mod_merge.Merger.from_revision_ids(
                                local_tree.branch.basis_tree(), other_branch=local_tree.branch,
                                other=main_branch_revid, tree_branch=local_tree.branch)
                        merger.merge_type = _mod_merge.Merge3Merger
                        tree_merger = merger.make_merger()
                        with tree_merger.make_preview_transform() as tt:
                            if tree_merger.cooked_conflicts:
                                note('%s: branch is conflicted, restarting.', pkg)
                                local_tree.update(revision=main_branch_revid)
                                local_tree.branch.generate_revision_history(main_branch_revid)
                                overwrite = True
                    finally:
                        _mod_merge.Merger.hooks['merge_file_content'] = old_file_content_mergers

            with local_tree.lock_write():
                if not local_tree.has_filename('debian/control'):
                    note('%s: missing control file', pkg)
                    continue
                local_branch = local_tree.branch
                orig_revid = local_branch.last_revision()

                update_changelog = should_update_changelog(local_branch)
                applied = run_lintian_fixers(
                        local_tree, [fixer_scripts[fixer] for fixer in fixers], update_changelog)
            if not applied:
                note('%s: no fixers to apply', pkg)
                continue
            if (mode == 'propose' and
                not existing_branch and
                not (set(f for f, d in applied) - propose_addon_only)):
                note('%s: only add-on fixers found', pkg)
                continue
            if local_branch.last_revision() == orig_revid:
                continue
            if mode == 'push':
                push_url = hoster.get_push_url(main_branch)
                note('%s: pushing to %s', pkg, push_url)
                local_branch.push(Branch.open(push_url))
            if mode == 'propose':
                if existing_branch is not None:
                    local_branch.push(existing_branch, overwrite=overwrite)
                else:
                    remote_branch, public_branch_url = hoster.publish_derived(
                        local_branch, main_branch, name=name, overwrite=False)
            if mode == 'propose':
                if existing_proposal is not None:
                    existing_description = existing_proposal.get_description().splitlines()
                    mp_description = create_mp_description(
                        [l[2:].rstrip('\n') for l in existing_description if l.startswith('* ')] +
                        [l for f, l in applied])
                    existing_proposal.set_description(mp_description)
                    note('%s: Updated proposal %s with fixes %r', pkg, existing_proposal.url,
                         [f for f, l in applied])
                else:
                    mp_description = create_mp_description([l for f, l in applied])
                    proposal_builder = hoster.get_proposer(remote_branch, main_branch)
                    try:
                        mp = proposal_builder.create_proposal(
                            description=mp_description, labels=[])
                    except PermissionDenied:
                        note('%s: Permission denied while trying to create proposal. ', pkg)
                        continue
                    note('%s: Proposed fixes %r: %s', pkg, [f for f, l in applied], mp.url)

        finally:
            shutil.rmtree(td)
