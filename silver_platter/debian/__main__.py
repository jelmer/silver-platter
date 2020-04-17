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

import silver_platter  # noqa: F401

import argparse
import sys


def main(argv=None):
    from . import (
        lintian as debian_lintian,
        run as debian_run,
        multiarch,
        orphan,
        rrr,
        tidy,
        uncommitted,
        upstream as debian_upstream,
        uploader as debian_uploader,
        )
    from ..__main__ import subcommands as main_subcommands

    subcommands = [
        ('run', debian_run.setup_parser, debian_run.main),
        ('new-upstream', debian_upstream.setup_parser, debian_upstream.main),
        ('upload-pending', debian_uploader.setup_parser, debian_uploader.main),
        ('lintian-brush', debian_lintian.setup_parser, debian_lintian.main),
        ('apply-multi-arch-hints', multiarch.setup_parser, multiarch.main),
        ('orphan', orphan.setup_parser, orphan.main),
        ('tidy', tidy.setup_parser, tidy.main),
        ('import-upload', uncommitted.setup_parser, uncommitted.main),
        ('rules-requires-root', rrr.setup_parser, rrr.main),
        ]

    for cmd in main_subcommands:
        if cmd[0] not in [subcmd[0] for subcmd in subcommands]:
            subcommands.append(cmd)

    parser = argparse.ArgumentParser(prog='debian-svp')
    parser.add_argument(
        '--version', action='version',
        version='%(prog)s ' + silver_platter.version_string)
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
