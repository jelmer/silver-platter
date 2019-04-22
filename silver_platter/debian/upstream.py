#!/usr/bin/python3
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

"""Support for merging new upstream versions."""

import silver_platter  # noqa: F401

from debian.changelog import Changelog

from breezy.plugins.debian.cmds import cmd_merge_upstream
import subprocess

from ..proposal import (
    get_hoster,
    publish_changes,
    UnsupportedHoster,
    SUPPORTED_MODES,
    )
from ..utils import (
    run_pre_check,
    )

from . import (
    open_packaging_branch,
    Workspace,
    )
from breezy.plugins.debian.errors import (
    UpstreamAlreadyImported,
    PackageVersionNotPresent,
    )

from breezy.trace import note, warning


BRANCH_NAME = "new-upstream-release"


from breezy.trace import note
from debian.changelog import Version

from breezy.plugins.debian.errors import 
from breezy.plugins.debian.hooks import run_hook
from breezy.plugins.debian.merge_upstream import (
    do_merge,
    get_tarballs,
    )
from breezy.plugins.debian.upstream import (
    TarfileSource,
    UScanSource,
    )
from breezy.plugins.debian.upstream.branch import (
    UpstreamBranchSource,
    )
from breezy.plugins.debian.util import (
    guess_build_type,
    tree_contains_upstream_source,
    )

def _add_changelog_entry(self, tree, package, version, distribution_name,
        changelog):
    from .merge_upstream import (
        changelog_add_new_version)
    from .errors import (
        DchError,
        )
    try:
        changelog_add_new_version(tree, version, distribution_name,
            changelog, package)
    except DchError as e:
        note(e)
        raise BzrCommandError('Adding a new changelog stanza after the '
                'merge had completed failed. Add the new changelog '
                'entry yourself, review the merge, and then commit.')


def merge_upstream(tree, snapshot=False, location=None):
    """

    Raises:
      PreviousVersionTagMissing
    """
    config = debuild_config(tree, tree)
    (current_version, package, distribution, distribution_name,
     changelog, top_level) = _get_changelog_info(tree, last_version,
         package, distribution)
    if package is None:
        raise AssertionError("You did not specify --package, and "
                "there is no changelog from which to determine the "
                "package name, which is needed to know the name to "
                "give the .orig.tar.gz. Please specify --package.")

    contains_upstream_source = tree_contains_upstream_source(tree)
    if changelog is None:
        changelog_version = None
    else:
        changelog_version = changelog.version
    build_type = config.build_type
    if build_type is None:
        build_type = guess_build_type(tree, changelog_version,
            contains_upstream_source)
    need_upstream_tarball = (build_type != BUILD_TYPE_MERGE)
    if build_type == BUILD_TYPE_NATIVE:
        raise AssertionError('Native packages do not have an upstream.')

    if config.upstream_branch is not None:
        note("Using upstream branch %s (from configuration)",
             config.upstream_branch)
        upstream_branch = Branch.open(config.upstream_branch)
    else:
        upstream_branch = None

    if snapshot:
        if upstream_branch_source is None:
            raise BzrCommandError("--snapshot requires "
                "an upstream branch source")
        primary_upstream_source = UpstreamBranchSource.from_branch(
            upstream_branch, config=config, local_dir=tree.controldir)
    else:
        primary_upstream_source = UScanSource(tree, top_level)

    if upstream_revision is not None:
        upstream_revisions = { None: upstream_revision }
    else:
        upstream_revisions = None

    if version is None and upstream_revisions is not None:
        # Look up the version from the upstream revision
        version = upstream_branch_source.get_version(package,
            current_version, upstream_revisions[None])
    elif version is None and primary_upstream_source is not None:
        version = primary_upstream_source.get_latest_version(
            package, current_version)
    if version is None:
        if upstream_branch_source is not None:
            raise BzrCommandError("You must specify "
                "the version number using --version or specify "
                "--snapshot to merge a snapshot from the upstream "
                "branch.")
        else:
            raise BzrCommandError("You must specify the "
                "version number using --version.")
    note("Using version string %s.", version)
    # Look up the revision id from the version string
    if upstream_revisions is None and upstream_branch_source is not None:
        try:
            upstream_revisions = upstream_branch_source.version_as_revisions(
                package, version)
        except PackageVersionNotPresent:
            raise BzrCommandError(
                "Version %s can not be found in upstream branch %r. "
                "Specify the revision manually using --revision or adjust "
                "'export-upstream-revision' in the configuration." %
                (version, upstream_branch_source))
    if need_upstream_tarball:
        with tempfile.TemporaryDirectory() as target_dir:
            try:
                locations = primary_upstream_source.fetch_tarballs(
                    package, version, target_dir, components=[None])
            except PackageVersionNotPresent:
                if upstream_revisions is not None:
                    locations = upstream_branch_source.fetch_tarballs(
                        package, version, target_dir, components=[None],
                        revisions=upstream_revisions)
                else:
                    raise
            orig_dir = config.orig_dir or default_orig_dir
            try:
                tarball_filenames = get_tarballs(orig_dir, tree, package,
                    version, upstream_branch, upstream_revisions,
                    locations)
            except FileExists:
                raise BzrCommandError(
                    "The target file %s already exists, and is either "
                    "different to the new upstream tarball, or they "
                    "are of different formats. Either delete the target "
                    "file, or use it as the argument to import."
                    % dest_name)
            conflicts = do_merge(tree, tarball_filenames, package,
                version, current_version, upstream_branch, upstream_revisions,
                merge_type, force)
    if (current_version is not None and
        Version(current_version) >= Version(version)):
        raise BzrCommandError(
            "Upstream version %s has already been merged." %
            version)
    if not tree.has_filename("debian"):
        tree.mkdir("debian")
    add_changelog_entry(tree, package, version,
        distribution_name, changelog)
    if not need_upstream_tarball:
        note("An entry for the new upstream version has been "
             "added to the changelog.")
    else:
        note("The new upstream version has been imported.")
        if conflicts:
            note("You should now resolve the conflicts, review "
                 "the changes, and then commit.")
        else:
            note("You should now review the changes and then commit.")

    subprocess.check_call(
        ["debcommit", "-a"], cwd=tree.basedir)
    return version.upstream_version


def setup_parser(parser):
    parser.add_argument("packages", nargs='+')
    parser.add_argument(
        '--snapshot',
        help='Merge a new upstream snapshot rather than a release',
        action='store_true')
    parser.add_argument(
        '--no-build-verify',
        help='Do not build package to verify it.',
        dest='build_verify',
        action='store_false')
    parser.add_argument(
        '--builder', type=str, default='sbuild', help='Build command.')
    parser.add_argument(
        '--pre-check',
        help='Command to run to check whether to process package.',
        type=str)
    parser.add_argument(
        "--dry-run",
        help="Create branches but don't push or propose anything.",
        action="store_true",
        default=False)
    parser.add_argument(
        '--mode',
        help='Mode for pushing', choices=SUPPORTED_MODES,
        default="propose", type=str)
    parser.add_argument(
        '--build-target-dir', type=str,
        help=("Store built Debian files in specified directory "
              "(with --build-verify)"))
    parser.add_argument(
        '--diff', action="store_true",
        help="Output diff of created merge proposal.")


def main(args):
    possible_hosters = []
    for package in args.packages:
        main_branch = open_packaging_branch(package)
        overwrite = False

        try:
            hoster = get_hoster(main_branch, possible_hosters=possible_hosters)
        except UnsupportedHoster as e:
            if args.mode != 'push':
                raise
            # We can't figure out what branch to resume from when there's no
            # hoster that can tell us.
            warning('Unsupported hoster (%s), will attempt to push to %s',
                    e, main_branch.user_url)
        with Workspace(main_branch) as ws:
            run_pre_check(ws.local_tree, args.pre_check)
            try:
                upstream_version = merge_upstream(
                    tree=ws.local_tree, snapshot=args.snapshot)
            except UpstreamAlreadyImported as e:
                note('Last upstream version %s already imported', e.version)
                continue

            if args.build_verify:
                ws.build(builder=args.builder,
                         result_dir=args.build_target_dir)

            def get_proposal_description(existing_proposal):
                return "Merge new upstream release %s" % upstream_version

            (proposal, is_new) = publish_changes(
                ws, args.mode, BRANCH_NAME,
                get_proposal_description=get_proposal_description,
                dry_run=args.dry_run, hoster=hoster,
                overwrite_existing=overwrite)

            if proposal:
                if is_new:
                    note('%s: Created new merge proposal %s.',
                         package, proposal.url)
                else:
                    note('%s: Updated merge proposal %s.',
                         package, proposal.url)
            if args.diff:
                ws.show_diff(sys.stdout.buffer)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(prog='propose-new-upstream')
    setup_parser(parser)
    args = parser.parse_args()
    main(args)
