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

from typing import Optional, Dict, List, Callable

import silver_platter  # noqa: F401

import argparse
import sys

from .changer import (
    setup_parser_common,
    run_single_changer,
    changer_subcommands,
    changer_subcommand,
    )

from . import uploader as debian_uploader


def run_changer_subcommand(name, changer_cls, argv):
    parser = argparse.ArgumentParser(prog='debian-svp %s URL|package' % name)
    setup_parser_common(parser)
    parser.add_argument('package', type=str, nargs='?')
    changer_cls.setup_parser(parser)
    args = parser.parse_args(argv)
    if args.package is None:
        parser.print_usage()
        return 1
    changer = changer_cls.from_args(args)
    return run_single_changer(changer, args)


def main(argv: Optional[List[str]] = None) -> Optional[int]:
    import breezy
    breezy.initialize()

    from ..__main__ import subcommands as main_subcommands

    subcommands: Dict[
            str, Callable[[List[str]], Optional[int]]] = {
        'upload-pending': debian_uploader.main,
        }

    parser = argparse.ArgumentParser(prog='debian-svp', add_help=False)
    parser.add_argument(
        '--version', action='version',
        version='%(prog)s ' + silver_platter.version_string)
    parser.add_argument(
        '--help', action='store_true',
        help='show this help message and exit')

    subcommands.update(main_subcommands.items())

    # We have a debian-specific run command
    del subcommands['run']

    parser.add_argument(
        'subcommand', type=str,
        choices=list(subcommands.keys()) + changer_subcommands())
    args, rest = parser.parse_known_args()
    if args.help:
        if args.subcommand is None:
            parser.print_help()
            parser.exit()
        else:
            rest.append('--help')

    if args.subcommand is None:
        parser.print_usage()
        return 1
    if args.subcommand in subcommands:
        return subcommands[args.subcommand](rest)
    try:
        subcmd = changer_subcommand(args.subcommand)
    except KeyError:
        pass
    else:
        return run_changer_subcommand(args.subcommand, subcmd, rest)
    parser.print_usage()
    return 1


if __name__ == '__main__':
    sys.exit(main())
