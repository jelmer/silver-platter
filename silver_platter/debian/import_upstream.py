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

"""Support for importing new upstream versions."""

import argparse
from debian.changelog import Version
import ssl
import tempfile
from typing import Optional, Callable

from breezy.errors import (
    FileExists,
    NoSuchFile,
    PointlessMerge,
    InvalidHttpResponse,
    )
from breezy.plugins.debian.config import (
    UpstreamMetadataSyntaxError
    )
from breezy.plugins.debian.upstream.branch import (
    UpstreamBranchSource,
    DistCommandFailed,
    run_dist_command,
    )
from breezy.plugins.debian.errors import (
    InconsistentSourceFormatError,
    MissingUpstreamTarball,
    PackageVersionNotPresent,
    UpstreamAlreadyImported,
    UpstreamBranchAlreadyMerged,
    UnparseableChangelog,
    )
from breezy.plugins.debian.merge_upstream import (
    do_merge,
    get_tarballs,
    PreviousVersionTagMissing,
    )
from breezy.plugins.debian.merge_upstream import (
    do_import,
    )
from breezy.plugins.debian.upstream import (
    UScanSource,
    TarfileSource,
    UScanError,
    )
from breezy.plugins.debian.upstream.pristinetar import (
    PristineTarError,
    PristineTarSource,
    )
from breezy.plugins.debian.util import (
    debuild_config,
    guess_build_type,
    get_files_excluded,
    tree_contains_upstream_source,
    BUILD_TYPE_MERGE,
    BUILD_TYPE_NATIVE,
    find_changelog,
    MissingChangelogError,
)

from breezy.trace import note, warning
from breezy.tree import Tree

from lintian_brush.vcs import sanitize_url as sanitize_vcs_url
from lintian_brush.upstream_metadata import (
    guess_upstream_metadata,
    )

from ..utils import (
    open_branch,
    BranchMissing,
    BranchUnavailable,
    BranchUnsupported,
    )

from .changer import (
    run_mutator,
    ChangerError,
    DebianChanger,
    ChangerResult,
    )


SNAPSHOT_BRANCH_NAME = 'import-upstream-snapshot'
RELEASE_BRANCH_NAME = 'import-upstream-release'
ORIG_DIR = '..'
DEFAULT_DISTRIBUTION = 'unstable'


def get_upstream_branch_location(tree, subpath, config, trust_package=False):
    if config.upstream_branch is not None:
        note("Using upstream branch %s (from configuration)",
             config.upstream_branch)
        # TODO(jelmer): Make brz-debian sanitize the URL?
        upstream_branch_location = sanitize_vcs_url(config.upstream_branch)
        upstream_branch_browse = getattr(
            config, 'upstream_branch_browse', None)
    else:
        guessed_upstream_metadata = guess_upstream_metadata(
            tree.abspath(subpath), trust_package=trust_package,
            net_access=True, consult_external_directory=False)
        upstream_branch_location = guessed_upstream_metadata.get(
            'Repository')
        upstream_branch_browse = guessed_upstream_metadata.get(
            'Repository-Browse')
        if upstream_branch_location:
            note("Using upstream branch %s (guessed)",
                 upstream_branch_location)
    return (upstream_branch_location, upstream_branch_browse)


class ImportUpstreamResult(object):
    """Object representing the result of an import_upstream operation."""

    __slots__ = [
            'old_upstream_version',
            'new_upstream_version',
            'upstream_branch',
            'upstream_branch_browse',
            'upstream_revisions',
            ]

    def __init__(self, old_upstream_version, new_upstream_version,
                 upstream_branch, upstream_branch_browse,
                 upstream_revisions):
        self.old_upstream_version = old_upstream_version
        self.new_upstream_version = new_upstream_version
        self.upstream_branch = upstream_branch
        self.upstream_branch_browse = upstream_branch_browse
        self.upstream_revisions = upstream_revisions


class InvalidFormatUpstreamVersion(Exception):
    """Invalid format upstream version string."""

    def __init__(self, version, source):
        self.version = version
        self.source = source


class UpstreamBranchUnknown(Exception):
    """The location of the upstream branch is unknown."""


class NewUpstreamMissing(Exception):
    """Unable to find upstream version to merge."""


class UpstreamBranchUnavailable(Exception):
    """Snapshot merging was requested by upstream branch is unavailable."""

    def __init__(self, location, error):
        self.location = location
        self.error = error


class UpstreamVersionMissingInUpstreamBranch(Exception):
    """The upstream version is missing in the upstream branch."""

    def __init__(self, upstream_branch, upstream_version):
        self.branch = upstream_branch
        self.version = upstream_version


class PackageIsNative(Exception):
    """Unable to merge upstream version."""

    def __init__(self, package, version):
        self.package = package
        self.version = version


class UpstreamNotBundled(Exception):
    """Packaging branch does not carry upstream sources."""

    def __init__(self, package):
        self.package = package


def import_upstream(
        tree: Tree, snapshot: bool = False,
        location: Optional[str] = None,
        new_upstream_version: Optional[str] = None,
        force: bool = False, distribution_name: str = DEFAULT_DISTRIBUTION,
        allow_ignore_upstream_branch: bool = True,
        trust_package: bool = False,
        committer: Optional[str] = None,
        subpath: str = '',
        create_dist: Optional[Callable[[Tree, str, Version, str], bool]] = None
        ) -> ImportUpstreamResult:
    """Import a new upstream version into a tree.

    Raises:
      InvalidFormatUpstreamVersion
      PreviousVersionTagMissing
      DistCommandFailed
      MissingChangelogError
      MissingUpstreamTarball
      NewUpstreamMissing
      UpstreamBranchUnavailable
      UpstreamAlreadyImported
      QuiltError
      UpstreamVersionMissingInUpstreamBranch
      UpstreamBranchUnknown
      PackageIsNative
      InconsistentSourceFormatError
      UnparseableChangelog
      UScanError
      UpstreamMetadataSyntaxError
      UpstreamNotBundled
    Returns:
      ImportUpstreamResult object
    """
    if subpath is None:
        subpath = ''
    config = debuild_config(tree, subpath)
    (changelog, top_level) = find_changelog(
        tree, subpath, merge=False, max_blocks=2)
    old_upstream_version = changelog.version.upstream_version
    package = changelog.package
    contains_upstream_source = tree_contains_upstream_source(tree, subpath)
    build_type = config.build_type
    if build_type is None:
        build_type = guess_build_type(
            tree, changelog.version, subpath,
            contains_upstream_source=contains_upstream_source)
    if build_type == BUILD_TYPE_MERGE:
        raise UpstreamNotBundled(changelog.package)
    if build_type == BUILD_TYPE_NATIVE:
        raise PackageIsNative(changelog.package, changelog.version)

    upstream_branch_location, upstream_branch_browse = get_upstream_branch_location(
        tree, subpath, config, trust_package=trust_package)

    if upstream_branch_location:
        try:
            upstream_branch = open_branch(upstream_branch_location)
        except (BranchUnavailable, BranchMissing, BranchUnsupported) as e:
            if not snapshot and allow_ignore_upstream_branch:
                warning('Upstream branch %s inaccessible; ignoring. %s',
                        upstream_branch_location, e)
            else:
                raise UpstreamBranchUnavailable(upstream_branch_location, e)
            upstream_branch = None
            upstream_branch_browse = None
    else:
        upstream_branch = None

    if upstream_branch is not None:
        try:
            upstream_branch_source = UpstreamBranchSource.from_branch(
                upstream_branch, config=config, local_dir=tree.controldir,
                create_dist=create_dist)
        except InvalidHttpResponse as e:
            raise UpstreamBranchUnavailable(upstream_branch_location, str(e))
        except ssl.SSLError as e:
            raise UpstreamBranchUnavailable(upstream_branch_location, str(e))
    else:
        upstream_branch_source = None

    if location is not None:
        try:
            branch = open_branch(location)
        except (BranchUnavailable, BranchMissing, BranchUnsupported):
            primary_upstream_source = TarfileSource(
                location, new_upstream_version)
        else:
            primary_upstream_source = UpstreamBranchSource.from_branch(
                branch, config=config,
                local_dir=tree.controldir, create_dist=create_dist)
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
        try:
            Version(new_upstream_version)
        except ValueError:
            raise InvalidFormatUpstreamVersion(
                new_upstream_version, primary_upstream_source)

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
                raise UpstreamVersionMissingInUpstreamBranch(
                    upstream_branch, new_upstream_version)
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

    try:
        files_excluded = get_files_excluded(tree, subpath, top_level)
    except NoSuchFile:
        files_excluded = None
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
        do_import(
            tree, subpath, tarball_filenames, package,
            new_upstream_version, old_upstream_version,
            upstream_branch, upstream_revisions, merge_type=None,
            force=force, committer=committer,
            files_excluded=files_excluded)

    return ImportUpstreamResult(
        old_upstream_version=old_upstream_version,
        new_upstream_version=new_upstream_version,
        upstream_branch=upstream_branch,
        upstream_branch_browse=upstream_branch_browse,
        upstream_revisions=upstream_revisions)


class ImportNewUpstreamChanger(DebianChanger):

    def __init__(self, snapshot, trust_package, dist_command):
        self.snapshot = snapshot
        self.trust_package = trust_package
        self.dist_command = dist_command

    @classmethod
    def setup_parser(cls, parser):
        parser.add_argument(
            '--trust-package', action='store_true',
            default=False,
            help=argparse.SUPPRESS)
        parser.add_argument(
            '--snapshot',
            help='Merge a new upstream snapshot rather than a release',
            action='store_true')
        parser.add_argument(
            '--dist-command', type=str,
            help="Command to run to create tarball from source tree.")

    def suggest_branch_name(self):
        if self.snapshot:
            return SNAPSHOT_BRANCH_NAME
        else:
            return RELEASE_BRANCH_NAME

    @classmethod
    def from_args(cls, args):
        return cls(snapshot=args.snapshot,
                   trust_package=args.trust_package,
                   dist_command=args.dist_command)

    def make_changes(self, local_tree, subpath, update_changelog, committer,
                     base_proposal=None):

        if self.dist_command:
            def create_dist(tree, package, version, target_filename):
                run_dist_command(
                    tree, package, version, target_filename, self.dist_command)
        else:
            create_dist = None

        try:
            import_upstream_result = import_upstream(
                tree=local_tree, snapshot=self.snapshot,
                trust_package=self.trust_package,
                subpath=subpath, committer=committer,
                create_dist=create_dist)
        except UpstreamAlreadyImported as e:
            raise ChangerError(
                'upstream-already-imported',
                'Last upstream version %s already imported.' % e.version, e)
        except NewUpstreamMissing as e:
            raise ChangerError(
                'new-upstream-missing',
                'Unable to find new upstream.', e)
        except PreviousVersionTagMissing as e:
            raise ChangerError(
                'previous-upstream-missing',
                'Unable to find tag %s for previous upstream version %s.' % (
                    e.tag_name, e.version), e)
        except InvalidFormatUpstreamVersion as e:
            raise ChangerError(
                'invalid-upstream-version-format',
                '%r reported invalid format version string %s.' % (
                    e.source, e.version), e)
        except PristineTarError as e:
            raise ChangerError(
                'pristine-tar-error', 'Pristine tar error: %s' % e, e)
        except UpstreamBranchUnavailable as e:
            raise ChangerError(
                'upstream-branch-unavailable',
                'Upstream branch %s unavailable: %s. ' % (e.location, e.error),
                e)
        except UpstreamBranchUnknown as e:
            raise ChangerError(
                'upstream-branch-unknown',
                'Upstream branch location unknown. '
                'Set \'Repository\' field in debian/upstream/metadata?', e)
        except PackageIsNative as e:
            raise ChangerError(
                'native-package',
                'Package %s is native; unable to merge new upstream.' % (
                    e.package, ), e)
        except InconsistentSourceFormatError as e:
            raise ChangerError(
                'inconsistent-source-format',
                'Inconsistencies in type of package: %s' % e, e)
        except UScanError as e:
            raise ChangerError(
                'uscan-error',
                'UScan failed: %s' % e, e)
        except UpstreamMetadataSyntaxError as e:
            raise ChangerError(
                'upstream-metadata-syntax-error',
                'Unable to parse %s' % e.path, e)
        except MissingChangelogError as e:
            raise ChangerError(
                'missing-changelog', 'Missing changelog %s' % e, e)
        except DistCommandFailed as e:
            raise ChangerError(
                'dist-command-failed', 'Dist command failed: %s' % e, e)
        except MissingUpstreamTarball as e:
            raise ChangerError(
                'missing-upstream-tarball',
                'Missing upstream tarball: %s' % e, e)
        else:
            note('Imported new upstream version %s (previous: %s)',
                 import_upstream_result.new_upstream_version,
                 import_upstream_result.old_upstream_version)

        tags = set()
        tags.add('upstream/%s' % import_upstream_result.new_upstream_version)
        # TODO(jelmer): Include upstream/pristine-tar in auxiliary_branches
        proposed_commit_message = (
            "Import new upstream release %s" %
            import_upstream_result.new_upstream_version)
        return ChangerResult(
            description="Import new upstream version %s" % (
                import_upstream_result.new_upstream_version),
            mutator=import_upstream_result, tags=tags,
            sufficient_for_proposal=True,
            proposed_commit_message=proposed_commit_message)

    def get_proposal_description(
            self, import_upstream_result, description_format, unused_proposal):
        return ("Import new upstream release %s" %
                import_upstream_result.new_upstream_version)

    def describe(self, import_upstream_result, publish_result):
        if publish_result.proposal:
            if publish_result.is_new:
                note('Created new merge proposal %s.',
                     publish_result.proposal.url)
            else:
                note('Updated merge proposal %s.',
                     publish_result.proposal.url)


def main(args: argparse.Namespace):
    from .changer import run_changer
    changer = ImportNewUpstreamChanger.from_args(args)
    return run_changer(changer, args)


def setup_parser(parser: argparse.ArgumentParser):
    from .changer import setup_multi_parser
    setup_multi_parser(parser)
    ImportNewUpstreamChanger.setup_parser(parser)


if __name__ == '__main__':
    import sys
    sys.exit(run_mutator(ImportNewUpstreamChanger))
