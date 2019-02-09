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

from __future__ import absolute_import

import silver_platter  # noqa: F401

import argparse
import sys


def autopropose_setup_parser(parser):
    parser.add_argument(
        'package', help='Package name or URL of branch to work on.', type=str)
    parser.add_argument('script', help='Path to script to run.', type=str)
    parser.add_argument('--overwrite', action="store_true",
                        help='Overwrite changes when publishing')
    parser.add_argument('--label', type=str,
                        help='Label to attach', action="append", default=[])
    parser.add_argument('--name', type=str,
                        help='Proposed branch name', default=None)


def autopropose_main(args):
    import os
    from breezy import osutils
    from breezy.plugins.propose import propose as _mod_propose
    from breezy.trace import note, show_error
    from ..autopropose import (
        autopropose,
        script_runner,
        )
    from . import (
        open_packaging_branch,
        )
    main_branch = open_packaging_branch(args.package)
    if args.name is None:
        name = os.path.splitext(osutils.basename(args.script.split(' ')[0]))[0]
    else:
        name = args.name
    script = os.path.abspath(args.script)
    try:
        proposal = autopropose(
                main_branch, lambda tree: script_runner(tree, script),
                name=name, overwrite=args.overwrite, labels=args.label)
    except _mod_propose.UnsupportedHoster as e:
        show_error('No known supported hoster for %s. Run \'svp login\'?',
                   e.branch.user_url)
        return 1
    note('Merge proposal created: %s', proposal.url)


def main(argv=None):
    from . import (
        lintian as debian_lintian,
        upstream as debian_upstream,
        uploader as debian_uploader,
        )

    subcommands = [
        ('autopropose', autopropose_setup_parser, autopropose_main),
        ('new-upstream', debian_upstream.setup_parser, debian_upstream.main),
        ('upload-pending', debian_uploader.setup_parser, debian_uploader.main),
        ('lintian-brush', debian_lintian.setup_parser, debian_lintian.main),
        ]

    parser = argparse.ArgumentParser(prog='debian-svp')
    parser.add_argument(
        '--version', action='version',
        version='%(prog)s ' + silver_platter.version_string)
    subparsers = parser.add_subparsers(dest='subcommand')
    callbacks = {}
    for name, setup_parser, run in subcommands:
        setup_parser(subparsers.add_parser(name))
        callbacks[name] = run
    args = parser.parse_args(argv)
    if args.subcommand is None:
        parser.print_usage()
        sys.exit(1)
    return callbacks[args.subcommand](args)


if __name__ == '__main__':
    sys.exit(main())
