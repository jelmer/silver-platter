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

from debian.changelog import Version
import sys
import tempfile

from ..proposal import (
    get_hoster,
    publish_changes,
    UnsupportedHoster,
    SUPPORTED_MODES,
    )
from ..utils import (
    open_branch,
    run_pre_check,
    BranchUnavailable,
    )

from . import (
    open_packaging_branch,
    Workspace,
    DEFAULT_BUILDER,
    debcommit,
    )
from breezy.commit import (
    PointlessCommit,
    )
from breezy.errors import (
    FileExists,
    PointlessMerge,
    )
from breezy.plugins.debian.errors import (
    UpstreamAlreadyImported,
    PackageVersionNotPresent,
    UpstreamBranchAlreadyMerged,
    )

from breezy.trace import note, show_error, warning

from breezy.plugins.debian.merge_upstream import (
    changelog_add_new_version,
    do_merge,
    get_tarballs,
    PreviousVersionTagMissing,
    )
from breezy.plugins.debian.upstream.pristinetar import (
    PristineTarError,
    PristineTarSource,
    )
from breezy.plugins.debian.quilt.quilt import (
    QuiltError,
    QuiltPatches,
    )


from breezy.plugins.debian.util import (
    debuild_config,
    guess_build_type,
    tree_contains_upstream_source,
    BUILD_TYPE_MERGE,
    BUILD_TYPE_NATIVE,
    find_changelog,
    MissingChangelogError,
)

from breezy.plugins.debian.upstream import (
    UScanSource,
    TarfileSource,
    )
from breezy.plugins.debian.upstream.branch import (
    UpstreamBranchSource,
    )


__all__ = [
    'PreviousVersionTagMissing',
    'merge_upstream',
    'MissingChangelogError',
    'NewUpstreamMissing',
    'UpstreamBranchUnavailable',
    'UpstreamAlreadyMerged',
    'UpstreamAlreadyImported',
    'UpstreamMergeConflicted',
    'QuiltError',
    'UpstreamVersionMissingInUpstreamBranch',
    'UpstreamBranchUnknown',
    'PackageIsNative',
]


class NewUpstreamMissing(Exception):
    """Unable to find upstream version to merge."""


class UpstreamBranchUnavailable(Exception):
    """Snapshot merging was requested by upstream branch is unavailable."""


class UpstreamMergeConflicted(Exception):
    """The upstream merge resulted in conflicts."""

    def __init__(self, upstream_version):
        self.version = upstream_version


class UpstreamAlreadyMerged(Exception):
    """Upstream release (or later version) has already been merged."""

    def __init__(self, upstream_version):
        self.version = upstream_version


class UpstreamVersionMissingInUpstreamBranch(Exception):
    """The upstream version is missing in the upstream branch."""

    def __init__(self, upstream_branch, upstream_version):
        self.branch = upstream_branch
        self.version = upstream_version


class UpstreamBranchUnknown(Exception):
    """The location of the upstream branch is unknown."""


class PackageIsNative(Exception):
    """Unable to merge upstream version."""

    def __init__(self, package, version):
        self.package = package
        self.version = version


RELEASE_BRANCH_NAME = "new-upstream-release"
SNAPSHOT_BRANCH_NAME = "new-upstream-snapshot"
ORIG_DIR = '..'
DEFAULT_DISTRIBUTION = 'unstable'


def check_quilt_patches_apply(local_tree):
    from lintian_brush import reset_tree  # lintian-brush < 0.16.
    assert not local_tree.has_changes()
    if local_tree.has_filename('debian/patches/series'):
        patches = QuiltPatches(local_tree, 'debian/patches')
        patches.push_all()
        patches.pop_all()
        reset_tree(local_tree)


def refresh_quilt_patches(local_tree, committer=None):
    patches = QuiltPatches(local_tree, 'debian/patches')
    patches.upgrade()
    patches.push_all(refresh=True)
    patches.pop_all()
    try:
        local_tree.commit('Refresh patches.', committer=committer)
    except PointlessCommit:
        pass


class MergeUpstreamResult(object):
    """Object representing the result of a merge_upstream operation."""

    __slots__ = ['old_upstream_version', 'new_upstream_version']

    def __init__(self, old_upstream_version, new_upstream_version):
        self.old_upstream_version = old_upstream_version
        self.new_upstream_version = new_upstream_version

    def __tuple__(self):
        # Backwards compatibility
        return (self.old_upstream_version, self.new_upstream_version)


def merge_upstream(tree, snapshot=False, location=None,
                   new_upstream_version=None, force=False,
                   distribution_name=DEFAULT_DISTRIBUTION,
                   allow_ignore_upstream_branch=True,
                   trust_package=False, committer=None):
    """Merge a new upstream version into a tree.

    Raises:
      PreviousVersionTagMissing
      MissingChangelogError
      NewUpstreamMissing
      UpstreamBranchUnavailable
      UpstreamAlreadyMerged
      UpstreamAlreadyImported
      UpstreamMergeConflicted
      QuiltError
      UpstreamVersionMissingInUpstreamBranch
      UpstreamBranchUnknown
      PackageIsNative
    Returns:
      MergeUpstreamResult object
    """
    config = debuild_config(tree)
    (changelog, top_level) = find_changelog(tree, False, max_blocks=2)
    old_upstream_version = changelog.version.upstream_version
    package = changelog.package
    contains_upstream_source = tree_contains_upstream_source(tree)
    build_type = config.build_type
    if build_type is None:
        build_type = guess_build_type(
            tree, changelog.version, contains_upstream_source)
    need_upstream_tarball = (build_type != BUILD_TYPE_MERGE)
    if build_type == BUILD_TYPE_NATIVE:
        raise PackageIsNative(changelog.package, changelog.version)

    if config.upstream_branch is not None:
        note("Using upstream branch %s (from configuration)",
             config.upstream_branch)
        upstream_branch_location = config.upstream_branch
    else:
        try:
            from lintian_brush.upstream_metadata import guess_upstream_metadata
        except ImportError:
            # Version of lintian-brush is too old..
            upstream_branch_location = None
        else:
            guessed_upstream_metadata = guess_upstream_metadata(
                tree.basedir, trust_package=trust_package)
            upstream_branch_location = guessed_upstream_metadata.get(
                'Repository')
        if upstream_branch_location:
            note("Using upstream branch %s (guessed)",
                 upstream_branch_location)

    if upstream_branch_location:
        try:
            upstream_branch = open_branch(upstream_branch_location)
        except BranchUnavailable as e:
            if not snapshot and allow_ignore_upstream_branch:
                warning('Upstream branch %s inaccessible; ignoring. %s',
                        upstream_branch_location, e)
            else:
                raise UpstreamBranchUnavailable(e)
            upstream_branch = None
    else:
        upstream_branch = None

    if upstream_branch is not None:
        upstream_branch_source = UpstreamBranchSource.from_branch(
            upstream_branch, config=config, local_dir=tree.controldir)
    else:
        upstream_branch_source = None

    if location is not None:
        try:
            primary_upstream_source = UpstreamBranchSource.from_branch(
                open_branch(location), config=config,
                local_dir=tree.controldir)
        except BranchUnavailable:
            primary_upstream_source = TarfileSource(
                location, new_upstream_version)
    else:
        if snapshot:
            if upstream_branch_source is None:
                raise UpstreamBranchUnknown()
            primary_upstream_source = upstream_branch_source
        else:
            primary_upstream_source = UScanSource(tree, top_level)

    if new_upstream_version is None and primary_upstream_source is not None:
        new_upstream_version = primary_upstream_source.get_latest_version(
            package, old_upstream_version)
    if new_upstream_version is None:
        raise NewUpstreamMissing()
    note("Using version string %s.", new_upstream_version)
    # Look up the revision id from the version string
    if upstream_branch_source is not None:
        try:
            upstream_revisions = upstream_branch_source.version_as_revisions(
                package, new_upstream_version)
        except PackageVersionNotPresent:
            if upstream_branch_source is primary_upstream_source:
                # The branch is our primary upstream source, so if it can't
                # find the version then there's nothing we can do.
                raise AssertionError(
                    "Version %s can not be found in upstream branch %r. "
                    "Specify the revision manually using --revision or adjust "
                    "'export-upstream-revision' in the configuration." %
                    (new_upstream_version, upstream_branch_source))
            elif not allow_ignore_upstream_branch:
                raise UpstreamVersionMissingInUpstreamBranch(
                    upstream_branch, new_upstream_version)
            else:
                warning(
                    'Upstream version %s is not in upstream branch %s. '
                    'Not merging from upstream branch. ',
                    new_upstream_version, upstream_branch)
                upstream_revisions = None
                upstream_branch_source = None
    else:
        upstream_revisions = None
    if need_upstream_tarball:
        with tempfile.TemporaryDirectory() as target_dir:
            try:
                locations = primary_upstream_source.fetch_tarballs(
                    package, new_upstream_version, target_dir,
                    components=[None])
            except PackageVersionNotPresent:
                if upstream_revisions is not None:
                    locations = upstream_branch_source.fetch_tarballs(
                        package, new_upstream_version, target_dir,
                        components=[None], revisions=upstream_revisions)
                else:
                    raise
            try:
                tarball_filenames = get_tarballs(
                    ORIG_DIR, tree, package, new_upstream_version,
                    upstream_branch, upstream_revisions, locations)
            except FileExists as e:
                raise AssertionError(
                    "The target file %s already exists, and is either "
                    "different to the new upstream tarball, or they "
                    "are of different formats. Either delete the target "
                    "file, or use it as the argument to import."
                    % e.path)
            try:
                conflicts = do_merge(
                    tree, tarball_filenames, package,
                    new_upstream_version, old_upstream_version,
                    upstream_branch, upstream_revisions, merge_type=None,
                    force=force, committer=committer)
            except UpstreamBranchAlreadyMerged:
                # TODO(jelmer): Perhaps reconcile these two exceptions?
                raise UpstreamAlreadyMerged(new_upstream_version)
            except UpstreamAlreadyImported:
                pristine_tar_source = PristineTarSource.from_tree(
                    tree.branch, tree)
                try:
                    conflicts = tree.merge_from_branch(
                        pristine_tar_source.branch,
                        to_revision=pristine_tar_source.version_as_revisions(
                            package, new_upstream_version)[None])
                except PointlessMerge:
                    raise UpstreamAlreadyMerged(new_upstream_version)
    if Version(old_upstream_version) >= Version(new_upstream_version):
        raise UpstreamAlreadyMerged(new_upstream_version)
    changelog_add_new_version(
        tree, new_upstream_version, distribution_name, changelog, package)
    if not need_upstream_tarball:
        note("An entry for the new upstream version has been "
             "added to the changelog.")
    else:
        if conflicts:
            raise UpstreamMergeConflicted(new_upstream_version)

    debcommit(tree, committer=committer)

    return MergeUpstreamResult(
        old_upstream_version=old_upstream_version,
        new_upstream_version=new_upstream_version)


def setup_parser(parser):
    import argparse
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
        '--builder', type=str, default=DEFAULT_BUILDER, help='Build command.')
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
    parser.add_argument(
        '--refresh-patches', action="store_true",
        help="Refresh quilt patches after upstream merge.")
    parser.add_argument(
        '--trust-package', action='store_true',
        default=False,
        help=argparse.SUPPRESS)


def main(args):
    possible_hosters = []
    ret = 0
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
        with Workspace(main_branch) as ws, ws.local_tree.lock_write():
            run_pre_check(ws.local_tree, args.pre_check)
            try:
                merge_upstream_result = merge_upstream(
                    tree=ws.local_tree, snapshot=args.snapshot,
                    trust_package=args.trust_package)
            except UpstreamAlreadyImported as e:
                show_error(
                    'Last upstream version %s already imported.', e.version)
                ret = 1
                continue
            except NewUpstreamMissing:
                show_error('Unable to find new upstream for %s.', package)
                ret = 1
                continue
            except UpstreamAlreadyMerged as e:
                show_error('Last upstream version %s already merged.',
                           e.version)
                ret = 1
                continue
            except PreviousVersionTagMissing as e:
                show_error(
                    'Unable to find tag %s for previous upstream version %s.',
                    e.tag_name, e.version)
                ret = 1
                continue
            except PristineTarError as e:
                show_error('Pristine tar error: %s', e)
                ret = 1
                continue
            except UpstreamBranchUnavailable as e:
                show_error('Upstream branch unavailable: %s. ', e)
                ret = 1
                continue
            except UpstreamBranchUnknown:
                show_error(
                    'Upstream branch location unknown. '
                    'Set \'Repository\' field in debian/upstream/metadata?')
                ret = 1
                continue
            except PackageIsNative as e:
                show_error(
                    'Package %s is native; unable to merge new upstream.',
                    e.package)
                ret = 1
                continue
            else:
                note('Merged new upstream version %s (previous: %s)',
                     merge_upstream_result.new_upstream_version,
                     merge_upstream_result.old_upstream_version)

            if args.refresh_patches and \
                    ws.local_tree.has_filename('debian/patches/series'):
                note('Refresh quilt patches.')
                try:
                    refresh_quilt_patches(ws.local_tree)
                except QuiltError as e:
                    show_error('Quilt error while refreshing patches: %s', e)
                    ret = 1
                    continue

            if args.build_verify:
                ws.build(builder=args.builder,
                         result_dir=args.build_target_dir)

            def get_proposal_description(existing_proposal):
                return ("Merge new upstream release %s" %
                        merge_upstream_result.new_upstream_version)

            if args.snapshot:
                branch_name = SNAPSHOT_BRANCH_NAME
            else:
                branch_name = RELEASE_BRANCH_NAME

            (proposal, is_new) = publish_changes(
                ws, args.mode, branch_name,
                get_proposal_description=get_proposal_description,
                get_proposal_commit_message=get_proposal_description,
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
    return ret


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(prog='propose-new-upstream')
    setup_parser(parser)
    args = parser.parse_args()
    main(args)
