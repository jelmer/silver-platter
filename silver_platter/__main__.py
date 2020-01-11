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
    run,
    version_string,
    )

from breezy.trace import show_error


def hosters_main(args):
    try:
        from breezy.propose import hosters
    except ImportError:
        from breezy.plugins.propose.propose import hosters

    for name, hoster_cls in hosters.items():
        for instance in hoster_cls.iter_instances():
            print('%s (%s)' % (instance.base_url, name))


def login_setup_parser(parser):
    parser.add_argument('url', help='URL of branch to work on.', type=str)


def login_main(args):
    from launchpadlib import uris as lp_uris

    hoster = None
    # TODO(jelmer): Don't special case various hosters here
    if args.url.startswith('https://github.com'):
        hoster = 'github'
    for key, root in lp_uris.web_roots.items():
        if args.url.startswith(root) or args.url == root.rstrip('/'):
            hoster = 'launchpad'
            lp_service_root = lp_uris.service_roots[key]
    if hoster is None:
        hoster = 'gitlab'

    from breezy.plugins.propose.cmds import cmd_github_login, cmd_gitlab_login
    if hoster == 'gitlab':
        cmd = cmd_gitlab_login()
        cmd._setup_outf()
        return cmd.run(args.url)
    elif hoster == 'github':
        cmd = cmd_github_login()
        cmd._setup_outf()
        return cmd.run()
    elif hoster == 'launchpad':
        from breezy.plugins.launchpad.cmds import cmd_launchpad_login
        cmd = cmd_launchpad_login()
        cmd._setup_outf()
        cmd.run()
        from breezy.plugins.launchpad import lp_api
        lp_api.connect_launchpad(lp_service_root, version='devel')
    else:
        show_error('Unknown hoster %r.', hoster)
        return 1


def proposals_setup_parser(parser):
    parser.add_argument(
        '--status', default='open', choices=['open', 'merged', 'closed'],
        type=str, help='Only display proposals with this status.')


def proposals_main(args):
    from .proposal import iter_all_mps
    for hoster, proposal, status in iter_all_mps([args.status]):
        print(proposal.url)


subcommands = [
    ('hosters', None, hosters_main),
    ('login', login_setup_parser, login_main),
    ('proposals', proposals_setup_parser, proposals_main),
    ('run', run.setup_parser, run.main),
    ]


def main(argv=None):
    parser = argparse.ArgumentParser(prog='svp')
    parser.add_argument(
        '--version', action='version', version='%(prog)s ' + version_string)
    subparsers = parser.add_subparsers(dest='subcommand')
    callbacks = {}
    for name, setup_parser, run_fn in subcommands:
        subparser = subparsers.add_parser(name)
        if setup_parser is not None:
            setup_parser(subparser)
        callbacks[name] = run_fn
    args = parser.parse_args(argv)
    if args.subcommand is None:
        parser.print_usage()
        return 1
    return callbacks[args.subcommand](args)


if __name__ == '__main__':
    sys.exit(main())
