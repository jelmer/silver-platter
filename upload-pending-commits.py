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

import silver_platter   # noqa: F401
from silver_platter.debian import (
    get_source_package,
    source_package_vcs_url,
    propose_or_push,
    )
from silver_platter.debian.uploader import (
    PackageUploader,
    get_maintainer_keys,
    )

from breezy import gpg

from breezy.branch import Branch

import argparse
parser = argparse.ArgumentParser(prog='upload-pending-commits')
parser.add_argument("packages", nargs='*')
parser.add_argument('--acceptable-keys',
                    help='List of acceptable GPG keys',
                    action='append', default=[], type=str)
parser.add_argument('--no-gpg-verification',
                    help='Do not verify GPG signatures', action='store_true')
parser.add_argument('--min-commit-age',
                    help='Minimum age of the last commit, in days',
                    type=int, default=7)
# TODO(jelmer): Support requiring that autopkgtest is present and passing
args = parser.parse_args()


for package in args.packages:
    pkg_source = get_source_package(package)
    vcs_type, vcs_url = source_package_vcs_url(pkg_source)
    main_branch = Branch.open(vcs_url)
    with main_branch.lock_read():
        branch_config = main_branch.get_config_stack()
        if args.no_gpg_verification:
            gpg_strategy = None
        else:
            gpg_strategy = gpg.GPGStrategy(branch_config)
            if args.acceptable_keys:
                acceptable_keys = args.acceptable_keys
            else:
                acceptable_keys = list(get_maintainer_keys(
                    gpg_strategy.context))
            gpg_strategy.set_acceptable_keys(','.join(acceptable_keys))

        branch_changer = PackageUploader(
                pkg_source["Package"], pkg_source["Version"], gpg_strategy)

        propose_or_push(main_branch, "new-upload", branch_changer, mode='push')
