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

import sys

from breezy.errors import BzrError

from . import (
    open_packaging_branch,
    should_update_changelog,
    DEFAULT_BUILDER,
    NoSuchPackage,
    )
from ..proposal import (
    get_hoster,
    find_existing_proposed,
    enable_tag_pushing,
    publish_changes,
    SUPPORTED_MODES,
    iter_conflicted,
    )
from ..utils import (
    run_pre_check,
    run_post_check,
    PostCheckFailed,
    BranchMissing,
    BranchUnavailable,
    )

from lintian_brush import (
    available_lintian_fixers,
    run_lintian_fixers,
    DEFAULT_MINIMUM_CERTAINTY,
    )
from lintian_brush.config import Config

__all__ = [
    'available_lintian_fixers',
    ]


DEFAULT_ADDON_FIXERS = [
    'file-contains-trailing-whitespace',
    'out-of-date-standards-version',
    'package-uses-old-debhelper-compat-version',
    ]
BRANCH_NAME = "lintian-fixes"


class UnknownFixer(BzrError):
    """The specified fixer is unknown."""

    _fmt = "No such fixer: %s."

    def __init__(self, fixer):
        super(UnknownFixer, self).__init__(fixer=fixer)


def parse_mp_description(description):
    """Parse a merge proposal description.

    Args:
      description: The description to parse
    Returns:
      list of one-line descriptions of changes
    """
    existing_lines = description.splitlines()
    if len(existing_lines) == 1:
        return existing_lines
    else:
        return [l[2:].rstrip('\n')
                for l in existing_lines if l.startswith('* ')]


def create_mp_description(lines):
    """Create a merge proposal description.

    Args:
      lines: List of one-line descriptions of fixes
    Returns:
      A string with a merge proposal description
    """
    if len(lines) > 1:
        mp_description = ["Fix some issues reported by lintian\n"]
        for line in lines:
            line = "* %s\n" % line
            if line not in mp_description:
                mp_description.append(line)
    else:
        mp_description = lines[0]
    return ''.join(mp_description)


def update_proposal_description(existing_proposal, applied):
    if existing_proposal:
        existing_description = existing_proposal.get_description()
        existing_lines = parse_mp_description(existing_description)
    else:
        existing_lines = []
    return create_mp_description(
        existing_lines + [l for r, l in applied])


def update_proposal_commit_message(existing_proposal, applied):
    existing_commit_message = getattr(
        existing_proposal, 'get_commit_message', lambda: None)()
    if existing_commit_message and not existing_commit_message.startswith(
            'Fix lintian issues: '):
        # The commit message is something we haven't set - let's leave it
        # alone.
        return
    if existing_commit_message:
        existing_applied = existing_commit_message.split(':', 1)[1]
    else:
        existing_applied = []
    return "Fix lintian issues: " + (
        ', '.join(sorted(existing_applied + [l for r, l in applied])))


def has_nontrivial_changes(applied, propose_addon_only):
    tags = set()
    for result, unused_summary in applied:
        tags.update(result.fixed_lintian_tags)
    # Is there enough to create a new merge proposal?
    return bool(tags - set(propose_addon_only))


def setup_parser(parser):
    parser.add_argument("packages", nargs='*')
    parser.add_argument(
        "--fixers",
        help="Fixers to run.", type=str, action='append')
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
        '--builder', default=DEFAULT_BUILDER, type=str,
        help='Build command to use when verifying build.')
    parser.add_argument(
        '--refresh',
        help='Discard old branch and apply fixers from scratch.',
        action='store_true')
    parser.add_argument(
        '--committer',
        help='Committer identity',
        type=str)
    parser.add_argument(
        '--mode',
        help='Mode for pushing', choices=SUPPORTED_MODES,
        default="propose", type=str)
    parser.add_argument(
        '--no-update-changelog', action="store_false", default=None,
        dest="update_changelog", help="do not update the changelog")
    parser.add_argument(
        '--update-changelog', action="store_true", dest="update_changelog",
        help="force updating of the changelog", default=None)
    parser.add_argument(
        '--diff', action="store_true",
        help="Output diff of created merge proposal.")
    parser.add_argument(
        '--build-target-dir', type=str,
        help=("Store built Debian files in specified directory "
              "(with --build-verify)"))
    parser.add_argument(
        '--overwrite', action='store_true',
        help='Overwrite existing branches.')
    parser.add_argument(
        '--fix-conflicted', action='store_true',
        help='Fix existing merge proposals that are conflicted.')


def get_fixers(available_fixers, names=None, tags=None):
    """Get the set of fixers to try.

    Args:
      available_fixers: Dictionary mapping fixer names to objects
      names: Optional set of fixers to restrict to
      tags: Optional set of tags to restrict to
    Returns:
      List of fixer objects
    """
    by_tag = {}
    by_name = {}
    for fixer in available_fixers:
        for tag in fixer.lintian_tags:
            by_tag[tag] = fixer
        by_name[fixer.name] = fixer

    # If it's unknown which fixers are relevant, just try all of them.
    if names:
        try:
            return [by_name[name] for name in names]
        except KeyError as e:
            raise UnknownFixer(e.args[0])
    elif tags:
        return [by_tag[tag] for tag in tags]
    else:
        return by_name.values()


def iter_packages(packages, overwrite_unrelated=False, refresh=False):
    from breezy.trace import note, warning
    from breezy.plugins.propose.propose import (
        UnsupportedHoster,
        )

    possible_transports = []
    possible_hosters = []

    for pkg in packages:
        note('Processing: %s', pkg)

        try:
            main_branch = open_packaging_branch(
                pkg, possible_transports=possible_transports)
        except NoSuchPackage:
            note('%s: no such package', pkg)
            continue
        except (BranchMissing, BranchUnavailable):
            note('%s: ignoring, socket error', pkg)
            continue

        overwrite = False

        try:
            hoster = get_hoster(main_branch, possible_hosters=possible_hosters)
        except UnsupportedHoster as e:
            if args.mode != 'push':
                raise
            # We can't figure out what branch to resume from when there's no
            # hoster that can tell us.
            resume_branch = None
            existing_proposal = None
            warning('Unsupported hoster (%s), will attempt to push to %s',
                    e, main_branch.user_url)
            hoster = None
        else:
            (resume_branch, overwrite, existing_proposal) = (
                find_existing_proposed(
                    main_branch, hoster, BRANCH_NAME,
                    overwrite_unrelated=overwrite_unrelated))
        if refresh:
            overwrite = True
            resume_branch = None

        yield (pkg, main_branch, resume_branch, hoster, existing_proposal,
               overwrite)


def main(args):
    import distro_info
    import itertools

    import silver_platter   # noqa: F401
    from . import (
        BuildFailedError,
        MissingUpstreamTarball,
        Workspace,
        )

    from breezy import (
        errors,
        )

    from breezy.plugins.propose.propose import (
        NoSuchProject,
        UnsupportedHoster,
        )
    from breezy.trace import note

    ret = 0

    try:
        fixers = get_fixers(available_lintian_fixers(), names=args.fixers)
    except UnknownFixer as e:
        note('Unknown fixer: %s', e.fixer)
        return 1

    debian_info = distro_info.DebianDistroInfo()

    package_iter = iter_packages(args.packages, args.overwrite, args.refresh)
    if args.fix_conflicted:
        package_iter = itertools.chain(
            package_iter, iter_conflicted(BRANCH_NAME))

    for (pkg, main_branch, resume_branch, hoster, existing_proposal,
         overwrite) in package_iter:
        with Workspace(main_branch, resume_branch=resume_branch) as ws:
            with ws.local_tree.lock_write():
                if ws.refreshed:
                    overwrite = True
                run_pre_check(ws.local_tree, args.pre_check)
                if args.update_changelog is None:
                    update_changelog = should_update_changelog(
                        ws.local_tree.branch)
                else:
                    update_changelog = args.update_changelog

                compat_release = None
                allow_reformatting = None
                minimum_certainty = None
                try:
                    cfg = Config.from_workingtree(ws.local_tree, '')
                except FileNotFoundError:
                    pass
                else:
                    compat_release = cfg.compat_release()
                    if compat_release:
                        compat_release = debian_info.codename(
                            compat_release, default=compat_release)
                    allow_reformatting = cfg.allow_reformatting()
                    minimum_certainty = cfg.minimum_certainty()
                if compat_release is None:
                    compat_release = debian_info.stable()
                if allow_reformatting is None:
                    allow_reformatting = False
                if minimum_certainty is None:
                    minimum_certainty = DEFAULT_MINIMUM_CERTAINTY

                applied, failed = run_lintian_fixers(
                        ws.local_tree, fixers,
                        committer=args.committer,
                        update_changelog=update_changelog,
                        compat_release=compat_release,
                        allow_reformatting=allow_reformatting,
                        minimum_certainty=minimum_certainty)

                if failed:
                    note('%s: some fixers failed to run: %r',
                         pkg, set(failed))
                if not applied:
                    if existing_proposal and not ws.changes_since_main():
                        note('%s: no fixers to apply. Closing proposal.', pkg)
                        existing_proposal.close()
                    else:
                        note('%s: no fixers to apply', pkg)
                    continue

            try:
                run_post_check(ws.local_tree, args.post_check, ws.orig_revid)
            except PostCheckFailed as e:
                note('%s: %s', pkg, e)
                continue
            if args.build_verify:
                try:
                    ws.build(builder=args.builder,
                             result_dir=args.build_target_dir)
                except BuildFailedError:
                    note('%s: build failed', pkg)
                    ret = 1
                    continue
                except MissingUpstreamTarball:
                    note('%s: unable to find upstream source', pkg)
                    ret = 1
                    continue

            enable_tag_pushing(ws.local_tree.branch)

            def get_proposal_description(existing_proposal):
                return update_proposal_description(
                    existing_proposal, applied)

            def get_proposal_commit_message(existing_proposal):
                return update_proposal_commit_message(
                    existing_proposal, applied)

            if not has_nontrivial_changes(applied, args.propose_addon_only):
                note('%s: only add-on fixers found', pkg)
                allow_create_proposal = False
            else:
                allow_create_proposal = True

            try:
                publish_result = publish_changes(
                    ws, args.mode, BRANCH_NAME,
                    get_proposal_description=get_proposal_description,
                    get_proposal_commit_message=get_proposal_commit_message,
                    dry_run=args.dry_run, hoster=hoster,
                    allow_create_proposal=allow_create_proposal,
                    overwrite_existing=overwrite,
                    existing_proposal=existing_proposal)
            except UnsupportedHoster:
                note('%s: Hoster unsupported', pkg)
                ret = 1
                continue
            except NoSuchProject as e:
                note('%s: project %s was not found', pkg, e.project)
                ret = 1
                continue
            except errors.PermissionDenied as e:
                note('%s: %s', pkg, e)
                ret = 1
                continue
            except errors.DivergedBranches:
                note('%s: a branch exists. Use --overwrite to discard it.',
                     pkg)
                ret = 1
                continue

            if publish_result.proposal:
                proposal = publish_result.proposal
                tags = set()
                for brush_result, unused_summary in applied:
                    tags.update(brush_result.fixed_lintian_tags)
                if publish_result.is_new:
                    note('%s: Proposed fixes %r: %s', pkg, tags,
                         proposal.url)
                elif tags:
                    note('%s: Updated proposal %s with fixes %r', pkg,
                         proposal.url, tags)
                else:
                    note('%s: No new fixes for proposal %s', pkg,
                         proposal.url)
            if args.diff:
                ws.show_diff(sys.stdout.buffer)

    return ret


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(prog='propose-lintian-fixes')
    setup_parser(parser)
    args = parser.parse_args()
    main(args)
