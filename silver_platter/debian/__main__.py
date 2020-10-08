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

from typing import Optional, Dict, List, Callable, Type

import silver_platter  # noqa: F401

import argparse
import sys

from .changer import setup_parser_common, DebianChanger, run_single_changer


def changer_subcommand(name, changer_cls, argv, changer_args):
    parser = argparse.ArgumentParser(prog='debian-svp %s URL' % name)
    changer_cls.setup_parser(parser)
    args = parser.parse_args(argv)
    changer = changer_cls.from_args(args)
    return run_single_changer(changer, changer_args)


def main(argv: Optional[List[str]] = None) -> Optional[int]:
    import breezy
    breezy.initialize()

    from . import (
        lintian as debian_lintian,
        cme,
        run as debian_run,
        multiarch,
        orphan,
        rrr,
        scrub_obsolete,
        tidy,
        uncommitted,
        upstream as debian_upstream,
        uploader as debian_uploader,
        )
    from ..__main__ import subcommands as main_subcommands

    subcommands: Dict[
            str, Callable[[List[str]], Optional[int]]] = {
        'upload-pending': debian_uploader.main,
        }

    changer_subcommands: Dict[str, Type[DebianChanger]] = {
        'run': debian_run.ScriptChanger,
        'lintian-brush': debian_lintian.LintianBrushChanger,
        'tidy': tidy.TidyChanger,
        'new-upstream': debian_upstream.NewUpstreamChanger,
        'cme-fix': cme.CMEChanger,
        'apply-multi-arch-hints': multiarch.MultiArchHintsChanger,
        'rules-requires-root': rrr.RulesRequiresRootChanger,
        'orphan': orphan.OrphanChanger,
        'import-upload': uncommitted.UncommittedChanger,
        'scrub-obsolete': scrub_obsolete.ScrubObsoleteChanger,
    }

    parser = argparse.ArgumentParser(prog='debian-svp', add_help=False)
    parser.add_argument(
        '--version', action='version',
        version='%(prog)s ' + silver_platter.version_string)
    parser.add_argument(
        '--help', action='store_true',
        help='show this help message and exit')
    setup_parser_common(parser)

    subcommands.update(main_subcommands.items())

    parser.add_argument(
        'subcommand', type=str,
        choices=list(subcommands.keys()) + list(changer_subcommands.keys()))
    parser.add_argument('package', type=str, nargs='?')
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
    if args.subcommand in changer_subcommands:
        if args.package is None:
            parser.print_usage()
            return 1
        return changer_subcommand(
            args.subcommand,
            changer_subcommands[args.subcommand], rest, args)
    parser.print_usage()
    return 1


if __name__ == '__main__':
    sys.exit(main())
