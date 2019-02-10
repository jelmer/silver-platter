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
from . import (
    autopropose,
    version_string,
    )


def hosters_main(args):
    from breezy.plugins.propose.propose import hosters

    for name, hoster_cls in hosters.items():
        for instance in hoster_cls.iter_instances():
            print('%s (%s)' % (instance.base_url, name))


subcommands = [
    ('autopropose', autopropose.setup_parser, autopropose.main),
    ('hosters', None, hosters_main),
    ]


def main(argv=None):
    parser = argparse.ArgumentParser(prog='svp')
    parser.add_argument(
        '--version', action='version', version='%(prog)s ' + version_string)
    subparsers = parser.add_subparsers(dest='subcommand')
    callbacks = {}
    for name, setup_parser, run in subcommands:
        subparser = subparsers.add_parser(name)
        if setup_parser is not None:
            setup_parser(subparser)
        callbacks[name] = run
    args = parser.parse_args(argv)
    if args.subcommand is None:
        parser.print_usage()
        sys.exit(1)
    return callbacks[args.subcommand](args)


if __name__ == '__main__':
    sys.exit(main())
