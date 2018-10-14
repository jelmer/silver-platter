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

from email.utils import parseaddr
import fnmatch
import os
import shutil
import socket
import subprocess
import sys
import tempfile

import silver_platter
from silver_platter.debian import (
    build,
    get_source_package,
    should_update_changelog,
    source_package_vcs_url,
    BuildFailedError,
    NoSuchPackage,
    MissingUpstreamTarball,
    )
from silver_platter.proposal import merge_conflicts
from silver_platter.utils import TemporarySprout

import breezy.plugins.launchpad
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
    NoSuchProject,
    UnsupportedHoster,
    )

from lintian_brush import available_lintian_fixers, run_lintian_fixers

from google.protobuf import text_format
import policy_pb2

import argparse
parser = argparse.ArgumentParser(prog='propose-lintian-fixes')
parser.add_argument("packages", nargs='*')
parser.add_argument('--lintian-log', help="Path to lintian log file.", type=str, default='lintian.log')
parser.add_argument("--fixers", help="Fixers to run.", type=str, action='append')
parser.add_argument("--policy", help="Policy file to read.", type=str, default='policy.conf')
parser.add_argument("--dry-run", help="Create branches but don't push or propose anything.",
                    action="store_true", default=False)
parser.add_argument('--propose-addon-only', help='Fixers that should be considered add-on-only.',
                    type=str, action='append',
                    default=['file-contains-trailing-whitespace'])
parser.add_argument('--pre-check', help='Command to run to check whether to process package.', type=str)
parser.add_argument('--build-verify', help='Build package to verify it.', action='store_true')
args = parser.parse_args()

fixer_scripts = {f.tag: f for f in available_lintian_fixers()}

dry_run = args.dry_run


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


with open(args.lintian_log, 'r') as f:
    lintian_errs = read_lintian_log(f)

with open(args.policy, 'r') as f:
    policy = text_format.Parse(f.read(), policy_pb2.PolicyConfig())

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


note("Considering %d packages for automatic change proposals", len(todo))

def parse_mp_description(description):
    existing_lines = description.splitlines()
    if len(existing_lines) == 1:
        return existing_lines
    else:
        return [l[2:].rstrip('\n') for l in existing_lines if l.startswith('* ')]


def create_mp_description(lines):
    if len(lines) > 1:
        mp_description = ["Fix some issues reported by lintian\n"]
        for l in lines:
            l = "* %s\n" % l
            if l not in mp_description:
                mp_description.append(l)
    else:
        mp_description = lines[0]
    return ''.join(mp_description)


def matches(match, control):
    for maintainer in match.maintainer:
        if maintainer != parseaddr(control["Maintainer"])[1]:
            return False
    uploader_emails = [
            parseaddr(uploader)[1]
            for uploader in control.get("Uploaders", "").split(",")]
    for uploader in match.uploader:
        if uploader not in uploader_emails:
            return False
    for source_package in match.source_package:
        if source_package != control["Package"]:
            return False
    return True


def apply_policy(config, control):
    mode = policy_pb2.skip
    update_changelog = policy_pb2.auto
    for policy in config.policy:
        if policy.match and not any([matches(m, control) for m in policy.match]):
            continue
        if policy.mode is not None:
            mode = policy.mode
        if policy.changelog is not None:
            update_changelog = policy.changelog
    return mode, update_changelog


class LintianFixer(object):

    def __init__(self, update_changelog):
        self._update_changelog = update_changelog

    def make_changes(self, local_tree):
        if not local_tree.has_filename('debian/control'):
            note('%s: missing control file', pkg)
            return
        if args.pre_check:
            try:
                subprocess.check_call(args.pre_check, shell=True, cwd=local_tree.basedir)
            except subprocess.CalledProcessError:
                note('%s: pre-check failed, skipping', pkg)
                return
        if self._update_changelog == policy_pb2.auto:
            update_changelog = should_update_changelog(local_tree.branch)
        elif self._update_changelog == policy_pb2.update_changelog:
            update_changelog = True
        elif self._update_changelog == policy_pb2.leave_changlog:
            update_changelog = False

        self.applied = run_lintian_fixers(
                local_tree, [fixer_scripts[fixer] for fixer in fixers], update_changelog)
        if not self.applied:
            note('%s: no fixers to apply', pkg)
            return

        if args.build_verify:
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
        # Is there enough to create a new merge proposal?
        if not set(f for f, d in self.applied) - propose_addon_only:
            note('%s: only add-on fixers found', pkg)
            return False
        return True


def propose_or_push(main_branch, name, changer, mode, dry_run=False):
    assert mode in ('push', 'propose', 'attempt-push')
    overwrite = False
    hoster = get_hoster(main_branch)
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
    with TemporarySprout(base_branch) as local_tree:
        with local_tree.branch.lock_read():
            if (mode == 'propose' and
                existing_branch is not None and
                merge_conflicts(main_branch, local_tree.branch)):
                note('%s: branch is conflicted, restarting.', pkg)
                main_branch_revid = main_branch.last_revision()
                local_tree.update(revision=main_branch_revid)
                local_tree.branch.generate_revision_history(main_branch_revid)
                overwrite = True

        with local_tree.lock_write():
            local_branch = local_tree.branch
            orig_revid = local_branch.last_revision()

            branch_changer.make_changes(local_tree)

        if local_branch.last_revision() == main_branch.last_revision():
            if existing_proposal is not None:
                note('%s: closing existing merge proposal - no new revisions', pkg)
                # TODO(jelmer): existing_proposal.close()
            return
        if orig_revid == local_branch.last_revision():
            # No new revisions added on this iteration, but still diverged from main branch.
            return
        if mode in ('push', 'attempt-push'):
            push_url = hoster.get_push_url(main_branch)
            note('%s: pushing to %s', pkg, push_url)
            if not dry_run:
                try:
                    local_branch.push(Branch.open(push_url))
                except (errors.PermissionDenied, errors.LockFailed):
                    if mode == 'attempt-push':
                        note('push access denied, falling back to propose')
                        mode = 'propose'
                    else:
                        note('permission denied during push')
                        raise
        if mode == 'propose':
            if not existing_branch and not branch_changer.should_create_proposal():
                return
            if not dry_run:
                if existing_branch is not None:
                    local_branch.push(existing_branch, overwrite=overwrite)
                    remote_branch = existing_branch
                else:
                    remote_branch, public_branch_url = hoster.publish_derived(
                        local_branch, main_branch, name=name, overwrite=False)
            mp_description = branch_changer.get_proposal_description(existing_proposal)
            if existing_proposal is not None:
                if not dry_run:
                    existing_proposal.set_description(mp_description)
                note('%s: Updated proposal %s with fixes %r', pkg, existing_proposal.url,
                     [f for f, l in branch_changer.applied])
            else:
                if not dry_run:
                    proposal_builder = hoster.get_proposer(remote_branch, main_branch)
                    try:
                        mp = proposal_builder.create_proposal(
                            description=mp_description, labels=[])
                    except errors.PermissionDenied:
                        note('%s: Permission denied while trying to create proposal.', pkg)
                        raise
                note('%s: Proposed fixes %r: %s', pkg, [f for f, l in branch_changer.applied], mp.url)


for pkg in sorted(todo):
    errs = lintian_errs[pkg]

    fixers = available_fixers.intersection(errs)
    if not fixers:
        continue

    if not (fixers - propose_addon_only):
        continue

    try:
        pkg_source = get_source_package(pkg)
    except NoSuchPackage:
        note('%s: not in apt sources', pkg)
        continue

    try:
        vcs_url = source_package_vcs_url(pkg_source)
    except urlutils.InvalidURL as e:
        note('%s: %s', pkg, e.extra)
    except KeyError:
        note('%s: no VCS URL found', pkg)
        continue

    mode, update_changelog = apply_policy(policy, pkg_source)

    if mode == policy_pb2.skip:
        note('%s: skipping, per policy', pkg)
        continue

    branch_changer = LintianFixer(update_changelog)

    try:
        main_branch = Branch.open(vcs_url)
    except socket.error:
        note('%s: ignoring, socket error', pkg)
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
        mode = {
            policy_pb2.propose: 'propose',
            policy_pb2.attempt_push: 'attempt-push',
            policy_pb2.push: 'push',
            }[mode]
        try:
            propose_or_push(main_branch, "lintian-fixes", branch_changer, mode)
        except UnsupportedHoster:
            note('%s: Hoster unsupported', pkg)
            continue
        except NoSuchProject as e:
            note('%s: project %s was not found', pkg, e.project)
            continue
        except BuildFailedError:
            note('%s: build failed', pkg)
            continue
        except MissingUpstreamTarball:
            note('%s: unable to find upstream source', pkg)
            continue
