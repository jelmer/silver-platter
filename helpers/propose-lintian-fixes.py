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

from silver_platter.debian.lintian import (
    DEFAULT_ADDON_FIXERS,
    available_lintian_fixers,
    LintianFixer,
    PostCheckFailed,
    )


def setup_parser(parser):
    parser.add_argument("packages", nargs='*')
    parser.add_argument(
        "--fixers",
        help="Fixers to run.", type=str, action='append')
    parser.add_argument(
        "--policy",
        help="Policy file to read.", type=str,
        default='policy.conf')
    parser.add_argument(
        "--dry-run",
        help="Create branches but don't push or propose anything.",
        action="store_true", default=False)
    parser.add_argument(
        '--propose-addon-only',
        help='Fixers that should be considered add-on-only.',
        type=str, action='append',
        default=DEFAULT_ADDON_FIXERS)
    parser.add_argument(
        '--pre-check',
        help='Command to run to check whether to process package.',
        type=str)
    parser.add_argument(
        '--post-check',
        help='Command to run to check package before pushing.',
        type=str)
    parser.add_argument(
        '--build-verify',
        help='Build package to verify it.', action='store_true')
    parser.add_argument(
        '--shuffle',
        help='Shuffle order in which packages are processed.',
        action='store_true')
    parser.add_argument(
        '--refresh',
        help='Discard old branch and apply fixers from scratch.',
        action='store_true')


def main(args):
    import argparse
    import distro_info
    import socket
    import subprocess

    import silver_platter   # noqa: F401
    from . import (
        propose_or_push,
        BuildFailedError,
        MissingUpstreamTarball,
        )
    from .schedule import schedule_udd

    from breezy import (
        errors,
        )

    from breezy.branch import Branch
    from breezy.trace import note

    from breezy.plugins.propose.propose import (
        NoSuchProject,
        UnsupportedHoster,
        )

    possible_transports = []
    possible_hosters = []

    fixer_scripts = {}
    for fixer in available_lintian_fixers():
        for tag in fixer.lintian_tags:
            fixer_scripts[tag] = fixer

    available_fixers = set(fixer_scripts)
    if args.fixers:
        available_fixers = available_fixers.intersection(set(args.fixers))

    todo = schedule_udd(
        args.policy, args.propose_addon_only, args.packages,
        available_fixers, args.shuffle)

    subparser = argparse.ArgumentParser(prog='lintian-brush')
    subparser.add_argument("fixers", nargs='*')
    subparser.add_argument(
        '--no-update-changelog', action="store_false", default=None,
        dest="update_changelog", help="do not update the changelog")
    subparser.add_argument(
        '--update-changelog', action="store_true", dest="update_changelog",
        help="force updating of the changelog", default=None)

    debian_info = distro_info.DebianDistroInfo()

    for (vcs_url, mode, env, command) in todo:
        pkg = env['PACKAGE']
        committer = env['COMMITTER']
        subargs = subparser.parse_args(command[1:])

        if args.pre_check:
            def pre_check(local_tree):
                try:
                    subprocess.check_call(
                            args.pre_check, shell=True, cwd=local_tree.basedir)
                except subprocess.CalledProcessError:
                    note('%r: pre-check failed, skipping', pkg)
                    return False
                return True
        else:
            pre_check = None

        if args.post_check:
            def post_check(local_tree, since_revid):
                try:
                    subprocess.check_call(
                        args.post_check, shell=True, cwd=local_tree.basedir,
                        env={'SINCE_REVID': since_revid})
                except subprocess.CalledProcessError:
                    note('%r: post-check failed, skipping', pkg)
                    return False
                return True
        else:
            post_check = None

        note('Processing: %s', pkg)

        try:
            main_branch = Branch.open(
                    vcs_url, possible_transports=possible_transports)
        except socket.error:
            note('%s: ignoring, socket error', pkg)
        except errors.NotBranchError as e:
            note('%s: Branch does not exist: %s', pkg, e)
        except errors.UnsupportedProtocol:
            note('%s: Branch available over unsupported protocol', pkg)
        except errors.ConnectionError as e:
            note('%s: %s', pkg, e)
        except errors.PermissionDenied as e:
            note('%s: %s', pkg, e)
        except errors.InvalidHttpResponse as e:
            note('%s: %s', pkg, e)
        except errors.TransportError as e:
            note('%s: %s', pkg, e)
        else:
            # If it's unknown which fixers are relevant, just try all of them.
            if subargs.fixers:
                fixers = subargs.fixers
            else:
                fixers = available_fixers
            branch_changer = LintianFixer(
                    pkg, fixers=[fixer_scripts[fixer] for fixer in fixers],
                    update_changelog=subargs.update_changelog,
                    compat_release=debian_info.stable(),
                    build_verify=args.build_verify,
                    pre_check=pre_check, post_check=post_check,
                    propose_addon_only=args.propose_addon_only,
                    committer=committer)
            try:
                result = propose_or_push(
                        main_branch, "lintian-fixes", branch_changer, mode,
                        possible_transports=possible_transports,
                        possible_hosters=possible_hosters,
                        refresh=args.refresh,
                        dry_run=args.dry_run)
            except UnsupportedHoster:
                note('%s: Hoster unsupported', pkg)
                continue
            except NoSuchProject as e:
                note('%s: project %s was not found', pkg, e.project)
                continue
            except BuildFailedError:
                note('%s: build failed', pkg)
                continue
            except MissingUpstreamTarball:
                note('%s: unable to find upstream source', pkg)
                continue
            except errors.PermissionDenied as e:
                note('%s: %s', pkg, e)
                continue
            except PostCheckFailed as e:
                note('%s: %s', pkg, e)
                continue
            else:
                if result.merge_proposal:
                    tags = set()
                    for brush_result, unused_summary in branch_changer.applied:
                        tags.update(brush_result.fixed_lintian_tags)
                    if result.is_new:
                        note('%s: Proposed fixes %r: %s', pkg, tags,
                             result.merge_proposal.url)
                    elif tags:
                        note('%s: Updated proposal %s with fixes %r', pkg,
                             result.merge_proposal.url, tags)
                    else:
                        note('%s: No new fixes for proposal %s', pkg,
                             result.merge_proposal.url)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(prog='propose-lintian-fixes')
    setup_parser(parser)
    args = parser.parse_args()
    main(args)
