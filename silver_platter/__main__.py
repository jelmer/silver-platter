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
import logging
import silver_platter  # noqa: F401
import sys
from typing import Optional, List, Callable, Dict
from . import (
    apply,
    run,
    version_string,
)


def hosters_main(argv: List[str]) -> Optional[int]:
    from breezy.propose import hosters

    parser = argparse.ArgumentParser(prog="svp hosters")
    parser.parse_args(argv)

    for name, hoster_cls in hosters.items():
        for instance in hoster_cls.iter_instances():
            print("%s (%s)" % (instance.base_url, name))

    return None


def login_main(argv: List[str]) -> Optional[int]:
    parser = argparse.ArgumentParser(prog="svp login")
    parser.add_argument("url", help="URL of branch to work on.", type=str)
    args = parser.parse_args(argv)

    from launchpadlib import uris as lp_uris

    hoster = None
    # TODO(jelmer): Don't special case various hosters here
    if args.url.startswith("https://github.com"):
        hoster = "github"
    for key, root in lp_uris.web_roots.items():
        if args.url.startswith(root) or args.url == root.rstrip("/"):
            hoster = "launchpad"
            lp_service_root = lp_uris.service_roots[key]
    if hoster is None:
        hoster = "gitlab"

    if hoster == "gitlab":
        from breezy.plugins.gitlab.cmds import cmd_gitlab_login

        cmd_gl = cmd_gitlab_login()
        cmd_gl._setup_outf()
        return cmd_gl.run(args.url)
    elif hoster == "github":
        from breezy.plugins.github.cmds import cmd_github_login

        cmd_gh = cmd_github_login()
        cmd_gh._setup_outf()
        return cmd_gh.run()
    elif hoster == "launchpad":
        from breezy.plugins.launchpad.cmds import cmd_launchpad_login

        cmd_lp = cmd_launchpad_login()
        cmd_lp._setup_outf()
        cmd_lp.run()
        from breezy.plugins.launchpad import lp_api

        lp_api.connect_launchpad(lp_service_root, version="devel")
        return None
    else:
        logging.exception("Unknown hoster %r.", hoster)
        return 1


def proposals_main(argv: List[str]) -> None:
    from .proposal import iter_all_mps

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--status",
        default="open",
        choices=["open", "merged", "closed"],
        type=str,
        help="Only display proposals with this status.",
    )
    args = parser.parse_args(argv)

    for hoster, proposal, status in iter_all_mps([args.status]):
        print(proposal.url)


subcommands: Dict[str, Callable[[List[str]], Optional[int]]] = {
    "hosters": hosters_main,
    "login": login_main,
    "proposals": proposals_main,
    "run": run.main,
    "apply": apply.main,
}


def main(argv: Optional[List[str]] = None) -> Optional[int]:
    import breezy

    breezy.initialize()
    parser = argparse.ArgumentParser(prog="svp", add_help=False)
    parser.add_argument(
        "--version", action="version", version="%(prog)s " + version_string
    )
    parser.add_argument(
        "--help", action="store_true", help="show this help message and exit"
    )
    parser.add_argument("subcommand", type=str, choices=list(subcommands.keys()))
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args, rest = parser.parse_known_args(argv)
    if args.help:
        if args.subcommand is None:
            parser.print_help()
            parser.exit()
        else:
            rest.append("--help")
    if args.subcommand is None:
        parser.print_usage()
        return 1
    return subcommands[args.subcommand](rest)


if __name__ == "__main__":
    sys.exit(main())
