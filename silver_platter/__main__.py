#!/usr/bin/python
# Copyright (C) 2018-2019 Jelmer Vernooij <jelmer@jelmer.uk>
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
import silver_platter   # noqa: F401
import sys
from . import autopropose
from .debian import (
    lintian as debian_lintian,
    upstream as debian_upstream,
    uploader as debian_uploader,
    )


subcommands = [
    ('autopropose', autopropose),
    ('propose-new-upstream', debian_upstream),
    ('upload-pending', debian_uploader),
    ('lintian-brush', debian_lintian),
    ]


def main(argv=None):
    parser = argparse.ArgumentParser(prog='svp')
    subparsers = parser.add_subparsers(dest='subcommand')
    callbacks = {}
    for name, mod in subcommands:
        getattr(mod, 'setup_parser')(subparsers.add_parser(name))
        callbacks[name] = getattr(mod, 'main')
    args = parser.parse_args(argv)
    if args.subcommand is None:
        parser.print_usage()
        sys.exit(1)
    return callbacks[args.subcommand](args)


if __name__ == '__main__':
    sys.exit(main())
