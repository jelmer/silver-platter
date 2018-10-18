#!/usr/bin/python3
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
    get_source_package,
    source_package_vcs_url,
    propose_or_push,
    BuildFailedError,
    NoSuchPackage,
    MissingUpstreamTarball,
    )
from silver_platter.debian.lintian import (
    available_lintian_fixers,
    read_lintian_log,
    LintianFixer,
    )

from breezy import (
    errors,
    urlutils,
    )

from breezy.branch import Branch
from breezy.trace import note

from breezy.plugins.propose.propose import (
    NoSuchProject,
    UnsupportedHoster,
    )

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
parser.add_argument('--post-check', help='Command to run to check package before pushing.', type=str)
parser.add_argument('--build-verify', help='Build package to verify it.', action='store_true')
parser.add_argument('--shuffle', help='Shuffle order in which packages are processed.', action='store_true')
args = parser.parse_args()

dry_run = args.dry_run


with open(args.lintian_log, 'r') as f:
    lintian_errs = read_lintian_log(f)

with open(args.policy, 'r') as f:
    policy = text_format.Parse(f.read(), policy_pb2.PolicyConfig())

propose_addon_only = set(args.propose_addon_only)

fixer_scripts = {f.tag: f for f in available_lintian_fixers()}
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
    update_changelog = 'auto'
    for policy in config.policy:
        if policy.match and not any([matches(m, control) for m in policy.match]):
            continue
        if policy.mode is not None:
            mode = policy.mode
        if policy.changelog is not None:
            update_changelog = policy.changelog
    return mode, {
        policy_pb2.auto: 'auto',
        policy_pb2.update_changelog: 'update',
        policy_pb2.leave_changelog: 'leave',
        }[update_changelog]


possible_transports = []
possible_hosters = []

todo = list(todo)

if args.shuffle:
    import random
    random.shuffle(todo)
else:
    todo.sort()

for pkg in todo:
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

    if args.pre_check:
        def pre_check(local_tree):
            try:
                subprocess.check_call(args.pre_check, shell=True, cwd=local_tree.basedir)
            except subprocess.CalledProcessError:
                note('%r: pre-check failed, skipping', pkg)
                return False
            return True
    else:
        pre_check = None

    if args.post_check:
        def post_check(local_tree, since_revid):
            try:
                subprocess.check_call(args.post_check, shell=True, cwd=local_tree.basedir,
                    env={'SINCE_REVID': since_revid})
            except subprocess.CalledProcessError:
                note('%r: post-check failed, skipping', pkg)
                return False
            return True
    else:
        post_check = None

    branch_changer = LintianFixer(
            pkg, fixers=[fixer_scripts[fixer] for fixer in fixers],
            update_changelog=update_changelog, build_verify=args.build_verify,
            pre_check=pre_check, post_check=post_check,
            propose_addon_only=propose_addon_only)

    note('Processing: %s', pkg)

    try:
        main_branch = Branch.open(vcs_url, possible_transports=possible_transports)
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
            proposal, is_new = propose_or_push(
                    main_branch, "lintian-fixes", branch_changer, mode,
                    possible_transports=possible_transports,
                    possible_hosters=possible_hosters)
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
        except errors.PermissionDenied as e:
            note('%s: %s', pkg, e)
            continue
        else:
            if proposal:
                if is_new:
                    note('%s: Proposed fixes %r: %s', pkg,
                         [f for f, l in branch_changer.applied], proposal.url)
                else:
                    note('%s: Updated proposal %s with fixes %r', pkg, proposal.url,
                         [f for f, l in branch_changer.applied])
