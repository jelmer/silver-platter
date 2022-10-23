#!/usr/bin/python
# Copyright (C) 2022 Jelmer Vernooij <jelmer@jelmer.uk>
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

from typing import List, Optional


def generate(recipe, candidates):
    raise NotImplementedError


def status():
    raise NotImplementedError


def refresh():
    raise NotImplementedError


def publish():
    raise NotImplementedError


def main(argv: List[str]) -> Optional[int]:  # noqa: C901
    import argparse
    parser = argparse.ArgumentParser("svp bulk")
    subparsers = parser.add_subparsers(dest="command")
    generate_parser = subparsers.add_parser("generate")
    generate_parser.add_argument(
        "--recipe", type=str, help="Recipe to use.")
    generate_parser.add_argument(
        "--candidates", type=str, help="File with candidate list.")
    publish_parser = subparsers.add_parser("publish")
    refresh_parser = subparsers.add_parser("refresh")
    status_parser = subparsers.add_parser("status")
    args = parser.parse_args(argv)
    if args.command == "generate":
        if args.recipe:
            from .recipe import Recipe
            recipe = Recipe.from_path(args.recipe)
        else:
            recipe = None
        if args.candidates:
            from .candidates import CandidateList
            candidates = CandidateList.from_path(args.candidates)
        else:
            candidates = None
        generate(recipe, candidates)
    elif args.command == 'publish':
        publish()
    elif args.command == 'refresh':
        refresh()
    elif args.command == 'status':
        status()
    else:
        parser.print_usage()
    return 0
