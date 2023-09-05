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

import argparse
import logging
import sys
from typing import Callable, Dict, List, Optional

import silver_platter  # noqa: F401

from . import apply as debian_apply
from . import batch as debian_batch
from . import run as debian_run
from . import uploader as debian_uploader


def main(argv: Optional[List[str]] = None) -> Optional[int]:
    import breezy

    breezy.initialize()  # type: ignore

    from ..__main__ import subcommands as main_subcommands

    subcommands: Dict[str, Callable[[List[str]], Optional[int]]] = {
        "upload-pending": debian_uploader.main,
        "apply": debian_apply.main,
        "run": debian_run.main,
        "batch": debian_batch.main,
    }

    parser = argparse.ArgumentParser(prog="debian-svp", add_help=False)
    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s " + silver_platter.version_string,
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Be more verbose")
    parser.add_argument(
        "--help", action="store_true", help="show this help message and exit"
    )

    for name, cmd in main_subcommands.items():
        if name not in subcommands:
            subcommands[name] = cmd

    parser.add_argument(
        "subcommand", type=str, choices=list(subcommands.keys())
    )
    args, rest = parser.parse_known_args()
    if args.help:
        if args.subcommand is None:
            parser.print_help()
            parser.exit()
        else:
            rest.append("--help")
    if args.debug:
        level = logging.DEBUG
    else:
        level = logging.INFO
    logging.basicConfig(level=level, format="%(message)s")
    if args.subcommand is None:
        parser.print_usage()
        return 1
    if args.subcommand in subcommands:
        return subcommands[args.subcommand](rest)
    parser.print_usage()
    return 1


if __name__ == "__main__":
    sys.exit(main())
