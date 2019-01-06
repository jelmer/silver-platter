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

import silver_platter  # noqa: F401
from silver_platter.debian import (
    get_source_package,
    open_packaging_branch,
    propose_or_push,
    source_package_vcs_url,
    )
from silver_platter.debian.upstream import (
    NewUpstreamMerger,
    )

from breezy.trace import note

import argparse
parser = argparse.ArgumentParser(prog='propose-new-upstream')
parser.add_argument("packages", nargs='+')
parser.add_argument('--snapshot',
                    help='Merge a new upstream snapshot rather than a release',
                    action='store_true')
parser.add_argument('--no-build-verify',
                    help='Do not build package to verify it.',
                    action='store_true')
parser.add_argument('--pre-check',
                    help='Command to run to check whether to process package.',
                    type=str)
parser.add_argument("--dry-run",
                    help="Create branches but don't push or propose anything.",
                    action="store_true",
                    default=False)
args = parser.parse_args()


for package in args.packages:
    main_branch = open_packaging_branch(package)
    # TODO(jelmer): Work out how to propose pristine-tar changes for merging
    # upstream.
    result = propose_or_push(
            main_branch, "new-upstream", NewUpstreamMerger(args.snapshot),
            mode='propose', dry_run=args.dry_run)
    if result.merge_proposal:
        if result.is_new:
            note('%s: Created new merge proposal %s.',
                 package, result.merge_proposal.url)
        else:
            note('%s: Updated merge proposal %s.',
                 package, result.merge_proposal.url)
