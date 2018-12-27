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

import fnmatch
from io import StringIO

from breezy import trace, urlutils
from silver_platter.debian import (
    get_source_package,
    source_package_vcs_url,
    NoSuchPackage,
    )
from silver_platter.debian.lintian import (
    available_lintian_fixers,
    download_latest_lintian_log,
    read_lintian_log,
    )
from silver_platter.debian.policy import (
    read_policy,
    apply_policy,
    )


import argparse
parser = argparse.ArgumentParser(prog='propose-lintian-fixes')
parser.add_argument("packages", nargs='*')
parser.add_argument('--lintian-log',
                    help="Path to lintian log file.", type=str,
                    default=None)
parser.add_argument("--fixers",
                    help="Fixers to run.", type=str, action='append')
parser.add_argument("--policy",
                    help="Policy file to read.", type=str,
                    default='policy.conf')
parser.add_argument("--dry-run",
                    help="Create branches but don't schedule anything.",
                    action="store_true", default=False)
parser.add_argument('--propose-addon-only',
                    help='Fixers that should be considered add-on-only.',
                    type=str, action='append',
                    default=['file-contains-trailing-whitespace'])
parser.add_argument('--build-verify',
                    help='Build package to verify it.', action='store_true')
parser.add_argument('--shuffle',
                    help='Shuffle order in which packages are processed.',
                    action='store_true')
args = parser.parse_args()

if args.lintian_log:
    f = open(args.lintian_log, 'r')
else:
    log = download_latest_lintian_log()
    f = StringIO(log.decode('utf-8'))

with f:
    lintian_errs = read_lintian_log(f)

with open(args.policy, 'r') as f:
    policy = read_policy(f)

propose_addon_only = set(args.propose_addon_only)

fixer_scripts = {}
for fixer in available_lintian_fixers():
    for tag in fixer.lintian_tags:
        fixer_scripts[tag] = fixer

available_fixers = set(fixer_scripts)
if args.fixers:
    available_fixers = available_fixers.intersection(set(args.fixers))


todo = set()
if not args.packages:
    todo = set(lintian_errs.keys())
else:
    for pkg_match in args.packages:
        todo.update(fnmatch.filter(lintian_errs.keys(), pkg_match))


trace.note("Considering %d packages for automatic change proposals", len(todo))

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
        trace.note('%s: not in apt sources', pkg)
        continue

    try:
        vcs_type, vcs_url = source_package_vcs_url(pkg_source)
    except urlutils.InvalidURL as e:
        trace.note('%s: %s', pkg, e.extra)
    except KeyError:
        trace.note('%s: no VCS URL found', pkg)
        continue

    mode, update_changelog, committer = apply_policy(policy, pkg_source)

    if mode == 'skip':
        trace.note('%s: skipping, per policy', pkg)
        continue

    command = ["lintian-brush"]
    if update_changelog == "update":
        command.append("--update-changelog")
    elif update_changelog == "leave":
        command.append("--no-update-changelog")
    command += list(fixers)
    entry = (vcs_url, mode, command)

    print(entry)
