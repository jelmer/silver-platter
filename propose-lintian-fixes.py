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
    urlutils,
    )

from breezy.branch import Branch
from breezy.commit import PointlessCommit
from breezy.trace import note
from breezy.transport import get_transport

from breezy.plugins.propose.propose import (
    get_hoster,
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
    pkg_source = Deb822(source.record)
    vcs_url = source_package_vcs_url(pkg_source)

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
        try:
            hoster = get_hoster(main_branch)
        except UnsupportedHoster:
            note('%s: Hoster unsupported', pkg)
            continue
        try:
            existing_branch = hoster.get_derived_branch(main_branch, name=name)
        except errors.NotBranchError:
            pass
        else:
            # TODO(jelmer): Verify that all available fixers were included?
            note('%s: Already proposed: %s', pkg, name)
            continue
        td = tempfile.mkdtemp()
        try:
            # preserve whatever source format we have.
            to_dir = main_branch.controldir.sprout(
                    get_transport(td).base, None, create_tree_if_local=False,
                    source_branch=main_branch, stacked=main_branch._format.supports_stacking())
            local_branch = to_dir.open_branch()
            orig_revid = local_branch.last_revision()
            if propose_owners is None:
                mode = 'propose'
            else:
                mode = None
            try:
                emails = list(get_maintainer_and_uploader_emails(pkg_source))
            except errors.NoSuchFile:
                note('%s: no debian/control file', pkg)
                continue
            for email in emails:
                if email in just_push_maintainers:
                    mode = 'push'
                    break
                elif propose_owners is None or email in propose_owners:
                    mode = 'propose'
            if not mode:
                continue
            update_changelog = should_update_changelog(local_branch)
            local_tree = local_branch.controldir.create_workingtree()
            applied = run_lintian_fixers(
                    local_tree, [fixer_scripts[fixer] for fixer in fixers], update_changelog)
            if mode == 'propose' and not (set(f for f, d in applied) - propose_addon_only):
                note('%s: only add-on fixers found', pkg)
                continue
            if local_branch.last_revision() == orig_revid:
                continue
            if not mode:
                continue
            elif mode == 'push':
                push_url = hoster.get_push_url(main_branch)
                note('%s: pushing to %s', pkg, push_url)
                local_branch.push(Branch.open(push_url))
            else:
                try:
                    remote_branch, public_branch_url = hoster.publish_derived(
                        local_branch, main_branch, name=name, overwrite=False)
                except errors.DivergedBranches:
                    note('%s: Already proposed: %s', pkg, name)
                    continue
                proposal_builder = hoster.get_proposer(remote_branch, main_branch)
                if len(applied) > 1:
                    mp_description = ["Fix some issues reported by lintian\n"] + [
                            ("* %s\n" % l) for f, l in applied]
                else:
                    mp_description = applied[0][1]
                mp = proposal_builder.create_proposal(
                    description=''.join(mp_description), labels=[])
                note('%s: Proposed fix for %r: %s', pkg, name, mp.url)
        finally:
            shutil.rmtree(td)
