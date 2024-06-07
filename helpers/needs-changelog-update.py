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

import argparse

from breezy.branch import Branch

import silver_platter  # noqa: F401
from silver_platter.debian import _changelog_stats

parser = argparse.ArgumentParser()
parser.add_argument(
    "location", help="Branch location to check.", type=str, default="."
)
args = parser.parse_args()

branch = Branch.open(args.location)
print(_changelog_stats(branch, 200))
