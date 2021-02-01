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

import argparse
import errno
from debian.changelog import Version, ChangelogParseError
import os
import re
import ssl
import tempfile
import traceback
from typing import List, Optional, Callable, Union

from ..utils import (
    full_branch_url,
    open_branch,
    BranchMissing,
    BranchUnavailable,
    BranchUnsupported,
    )

from . import (
    changelog_add_line,
    debcommit,
    control_files_in_root,
    )
from .changer import (
    run_mutator,
    ChangerError,
    DebianChanger,
    ChangerResult,
    )
from breezy.commit import (
    PointlessCommit,
    )
from breezy.errors import (
    FileExists,
    InvalidNormalization,
    NoSuchFile,
    NotBranchError,
    PointlessMerge,
    InvalidHttpResponse,
    NoRoundtrippingSupport,
    )
from breezy.revision import NULL_REVISION
from breezy.plugins.debian.config import (
    UpstreamMetadataSyntaxError
    )
from breezy.plugins.debian.util import (
    InconsistentSourceFormatError,
    )
from breezy.plugins.debian.import_dsc import (
    UpstreamAlreadyImported,
    UpstreamBranchAlreadyMerged,
    )

from breezy.trace import note, warning
from breezy.transform import MalformedTransform

from breezy.plugins.debian.merge_upstream import (
    changelog_add_new_version,
    do_import,
    do_merge,
    get_tarballs,
    PreviousVersionTagMissing,
    )
from breezy.plugins.debian.repack_tarball import (
    UnsupportedRepackFormat,
    )

from breezy.plugins.debian.upstream.pristinetar import (
    PristineTarError,
    get_pristine_tar_source,
    )
from breezy.plugins.quilt.quilt import (
    QuiltError,
    QuiltPatches,
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

from breezy.plugins.debian.upstream import (
    TarfileSource,
    MissingUpstreamTarball,
    PackageVersionNotPresent,
    )
from breezy.plugins.debian.upstream.uscan import (
    UScanSource,
    UScanError,
    NoWatchFile,
    WatchLineWithoutMatches,
    WatchLineWithoutMatchingHrefs,
    )
from breezy.plugins.debian.upstream.branch import (
    UpstreamBranchSource,
    DistCommandFailed,
    run_dist_command,
    )
from breezy.tree import Tree

from lintian_brush.vcs import sanitize_url as sanitize_vcs_url
from lintian_brush.upstream_metadata import (
    guess_upstream_metadata,
    )


__all__ = [
    'PreviousVersionTagMissing',
    'merge_upstream',
    'InvalidFormatUpstreamVersion',
    'DistCommandFailed',
    'MissingChangelogError',
    'MissingUpstreamTarball',
    'NewUpstreamMissing',
    'UpstreamBranchUnavailable',
    'UnsupportedRepackFormat',
    'UpstreamAlreadyMerged',
    'UpstreamAlreadyImported',
    'UpstreamMergeConflicted',
    'NewUpstreamTarballMissing',
    'QuiltError',
    'NoUpstreamLocationsKnown',
    'UpstreamVersionMissingInUpstreamBranch',
    'UpstreamBranchUnknown',
    'PackageIsNative',
    'ChangelogParseError',
    'UScanError',
    'UpstreamMetadataSyntaxError',
    'QuiltPatchPushFailure',
    'WatchLineWithoutMatches',
]


class UpstreamMergeConflicted(Exception):
    """The upstream merge resulted in conflicts."""

    def __init__(self, upstream_version, conflicts):
        self.version = upstream_version
        self.conflicts = conflicts


class UpstreamAlreadyMerged(Exception):
    """Upstream release (or later version) has already been merged."""

    def __init__(self, upstream_version):
        self.version = upstream_version


class QuiltPatchPushFailure(Exception):

    def __init__(self, patch_name, actual_error):
        self.patch_name = patch_name
        self.actual_error = actual_error


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


class NewUpstreamTarballMissing(Exception):

    def __init__(self, package, version, upstream):
        self.package = package
        self.version = version
        self.upstream = upstream


class NoUpstreamLocationsKnown(Exception):
    """No upstream locations (uscan/repository) for the package are known."""

    def __init__(self, package):
        self.package = package


class NewerUpstreamAlreadyImported(Exception):
    """A newer upstream version has already been imported."""

    def __init__(self, old_upstream_version, new_upstream_version):
        self.old_upstream_version = old_upstream_version
        self.new_upstream_version = new_upstream_version


RELEASE_BRANCH_NAME = "new-upstream-release"
SNAPSHOT_BRANCH_NAME = "new-upstream-snapshot"
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
    if upstream_branch_browse is None and upstream_branch_location is not None:
        try:
            from lintian_brush.vcs import determine_browser_url
        except ImportError:
            pass
        else:
            upstream_branch_browse = determine_browser_url(
                None, upstream_branch_location)
    return (upstream_branch_location, upstream_branch_browse)


def refresh_quilt_patches(
        local_tree: Tree, old_version: Version, new_version: Version,
        committer: Optional[str] = None, subpath: str = '') -> None:
    # TODO(jelmer):
    # Find patch base branch.
    #   If it exists, rebase it onto the new upstream.
    #   And then run 'gbp pqm export' or similar
    # If not:
    #   Refresh patches against the new upstream revision
    patches = QuiltPatches(local_tree, os.path.join(subpath, 'debian/patches'))
    patches.upgrade()
    for name in patches.unapplied():
        try:
            patches.push(name, refresh=True)
        except QuiltError as e:
            lines = e.stdout.splitlines()
            m = re.match(
                'Patch debian/patches/(.*) can be reverse-applied', lines[-1])
            if m and getattr(patches, 'delete', None):
                assert m.group(1) == name
                patches.delete(name, remove=True)
                changelog_add_line(
                    local_tree, subpath,
                    'Drop patch %s, present upstream.' % name,
                    email=committer)
                debcommit(
                    local_tree, committer=committer,
                    subpath=subpath,
                    paths=[
                     'debian/patches/series', 'debian/patches/' + name,
                     'debian/changelog'])
            else:
                raise QuiltPatchPushFailure(name, e)
    patches.pop_all()
    try:
        local_tree.commit(
            'Refresh patches.', committer=committer, allow_pointless=False)
    except PointlessCommit:
        pass


class ImportUpstreamResult(object):
    """Object representing the result of an import_upstream operation."""

    __slots__ = [
            'old_upstream_version',
            'new_upstream_version',
            'upstream_branch',
            'upstream_branch_browse',
            'upstream_revisions',
            'imported_revisions',
            'include_upstream_history',
            ]

    def __init__(self, include_upstream_history,
                 old_upstream_version, new_upstream_version,
                 upstream_branch, upstream_branch_browse,
                 upstream_revisions, imported_revisions):
        self.old_upstream_version = old_upstream_version
        self.new_upstream_version = new_upstream_version
        self.upstream_branch = upstream_branch
        self.upstream_branch_browse = upstream_branch_browse
        self.upstream_revisions = upstream_revisions
        self.imported_revisions = imported_revisions
        self.include_upstream_history = include_upstream_history


def detect_include_upstream_history(
        tree, upstream_branch_source, package, old_upstream_version):
    # Simple heuristic: Find the old upstream version and see if it's present
    # in the history of the packaging branch
    try:
        revision = upstream_branch_source.version_as_revision(
            package, old_upstream_version)
    except PackageVersionNotPresent:
        warning(
            'Old upstream version %r is not present in upstream '
            'branch %r. Unable to determine whether upstream history '
            'is normally included. Assuming no.', old_upstream_version,
            upstream_branch_source)
        return False

    graph = tree.branch.repository.get_graph()
    ret = graph.is_ancestor(revision, tree.last_revision())
    if ret:
        note('Including upstream history, since previous upstream version '
             '(%s) is present in packaging branch history.',
             old_upstream_version)
    else:
        note('Not including upstream history, since previous upstream version '
             '(%s) is not present in packaging branch history.',
             old_upstream_version)

    return ret


def matches_release(upstream_version: str, release_version: str):
    release_version = release_version.lower()
    upstream_version = upstream_version.lower()
    m = re.match('(.*)([~+-])(dfsg|git|bzr|svn|hg).*', upstream_version)
    if m and m.group(1) == release_version:
        return True
    m = re.match('(.*)([~+-]).*', upstream_version)
    if m and m.group(1) == release_version:
        return True
    return False


def find_new_upstream(
        tree, subpath, config, package, location=None,
        old_upstream_version=None, new_upstream_version=None,
        trust_package=False, snapshot=False,
        allow_ignore_upstream_branch=False, top_level=False,
        create_dist=None, include_upstream_history: Optional[bool] = None):

    # TODO(jelmer): Find the lastest upstream present in the upstream branch
    # rather than what's in the changelog.

    upstream_branch_location, upstream_branch_browse = (
        get_upstream_branch_location(
            tree, subpath, config, trust_package=trust_package))

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
                create_dist=create_dist, snapshot=snapshot)
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
                local_dir=tree.controldir, create_dist=create_dist,
                snapshot=snapshot)
    else:
        if snapshot:
            if upstream_branch_source is None:
                raise UpstreamBranchUnknown()
            primary_upstream_source = upstream_branch_source
        else:
            try:
                primary_upstream_source = UScanSource.from_tree(
                    tree, subpath, top_level)
            except NoWatchFile:
                # TODO(jelmer): Call out to lintian_brush.watch to generate a
                # watch file.
                if upstream_branch_source is None:
                    raise NoUpstreamLocationsKnown(package)
                primary_upstream_source = upstream_branch_source

    if new_upstream_version is None and primary_upstream_source is not None:
        new_upstream_version = primary_upstream_source.get_latest_version(
            package, old_upstream_version)

    if new_upstream_version is None:
        raise NewUpstreamMissing()

    try:
        new_upstream_version = Version(new_upstream_version)
    except ValueError:
        raise InvalidFormatUpstreamVersion(
            new_upstream_version, primary_upstream_source)

    if old_upstream_version:
        if old_upstream_version == new_upstream_version:
            raise UpstreamAlreadyImported(new_upstream_version)
        if old_upstream_version > new_upstream_version:
            if not snapshot and matches_release(
                    str(old_upstream_version), str(new_upstream_version)):
                raise UpstreamAlreadyImported(new_upstream_version)
            raise NewerUpstreamAlreadyImported(
                old_upstream_version, new_upstream_version)

    # TODO(jelmer): Check if new_upstream_version is already imported

    note("Using version string %s.", new_upstream_version)

    if include_upstream_history is None and upstream_branch_source is not None:
        include_upstream_history = detect_include_upstream_history(
            tree, upstream_branch_source, package, old_upstream_version)

    if include_upstream_history is False:
        upstream_branch_source = None

    # Look up the revision id from the version string
    if upstream_branch_source is not None:
        try:
            upstream_revisions = upstream_branch_source.version_as_revisions(
                package, str(new_upstream_version))
        except PackageVersionNotPresent:
            if upstream_branch_source is primary_upstream_source:
                # The branch is our primary upstream source, so if it can't
                # find the version then there's nothing we can do.
                raise UpstreamVersionMissingInUpstreamBranch(
                    upstream_branch_source.upstream_branch,
                    new_upstream_version)
            elif not allow_ignore_upstream_branch:
                raise UpstreamVersionMissingInUpstreamBranch(
                    upstream_branch_source.upstream_branch,
                    new_upstream_version)
            else:
                warning(
                    'Upstream version %s is not in upstream branch %s. '
                    'Not merging from upstream branch. ',
                    new_upstream_version,
                    upstream_branch_source.upstream_branch)
                upstream_revisions = None
                upstream_branch_source = None
    else:
        upstream_revisions = None

    try:
        files_excluded = get_files_excluded(tree, subpath, top_level)
    except NoSuchFile:
        files_excluded = None

    return (
        primary_upstream_source,
        new_upstream_version,
        upstream_revisions,
        upstream_branch_source,
        upstream_branch,
        upstream_branch_browse,
        files_excluded,
        include_upstream_history)


def import_upstream(
        tree: Tree, snapshot: bool = False,
        location: Optional[str] = None,
        new_upstream_version: Optional[Union[Version, str]] = None,
        force: bool = False, distribution_name: str = DEFAULT_DISTRIBUTION,
        allow_ignore_upstream_branch: bool = True,
        trust_package: bool = False,
        committer: Optional[str] = None,
        subpath: str = '',
        include_upstream_history: Optional[bool] = None,
        create_dist: Optional[
            Callable[[Tree, str, Version, str], Optional[str]]] = None
        ) -> ImportUpstreamResult:
    """Import a new upstream version into a tree.

    Raises:
      InvalidFormatUpstreamVersion
      PreviousVersionTagMissing
      UnsupportedRepackFormat
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
      ChangelogParseError
      UScanError
      UpstreamMetadataSyntaxError
      UpstreamNotBundled
      NewUpstreamTarballMissing
      NoUpstreamLocationsKnown
      NewerUpstreamAlreadyImported
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

    (primary_upstream_source,
     new_upstream_version,
     upstream_revisions,
     upstream_branch_source,
     upstream_branch,
     upstream_branch_browse,
     files_excluded, include_upstream_history) = find_new_upstream(
        tree, subpath, config, package, location=location,
        old_upstream_version=old_upstream_version,
        new_upstream_version=new_upstream_version, trust_package=trust_package,
        snapshot=snapshot,
        allow_ignore_upstream_branch=allow_ignore_upstream_branch,
        top_level=top_level, include_upstream_history=include_upstream_history,
        create_dist=create_dist)

    with tempfile.TemporaryDirectory() as target_dir:
        initial_path = os.path.join(target_dir, 'initial')
        os.mkdir(initial_path)
        try:
            locations = primary_upstream_source.fetch_tarballs(
                package, str(new_upstream_version), target_dir,
                components=[None])
        except (PackageVersionNotPresent, WatchLineWithoutMatchingHrefs):
            if upstream_revisions is not None:
                locations = upstream_branch_source.fetch_tarballs(
                    package, str(new_upstream_version), initial_path,
                    components=[None], revisions=upstream_revisions)
            else:
                raise
        orig_path = os.path.join(target_dir, 'orig')
        try:
            tarball_filenames = get_tarballs(
                orig_path, tree, package, new_upstream_version,
                locations)
        except FileExists as e:
            raise AssertionError(
                "The target file %s already exists, and is either "
                "different to the new upstream tarball, or they "
                "are of different formats. Either delete the target "
                "file, or use it as the argument to import."
                % e.path)
        imported_revisions = do_import(
            tree, subpath, tarball_filenames, package,
            str(new_upstream_version), str(old_upstream_version),
            upstream_branch_source.upstream_branch
            if upstream_branch_source else None,
            upstream_revisions, merge_type=None,
            force=force, committer=committer,
            files_excluded=files_excluded)

    return ImportUpstreamResult(
        include_upstream_history=include_upstream_history,
        old_upstream_version=old_upstream_version,
        new_upstream_version=new_upstream_version,
        upstream_branch=upstream_branch,
        upstream_branch_browse=upstream_branch_browse,
        upstream_revisions=upstream_revisions,
        imported_revisions=imported_revisions)


class MergeUpstreamResult(object):
    """Object representing the result of a merge_upstream operation."""

    __slots__ = [
            'old_upstream_version',
            'new_upstream_version',
            'upstream_branch',
            'upstream_branch_browse',
            'upstream_revisions',
            'old_revision',
            'new_revision',
            'imported_revisions',
            'include_upstream_history',
            ]

    def __init__(self, include_upstream_history, old_upstream_version,
                 new_upstream_version,
                 upstream_branch, upstream_branch_browse,
                 upstream_revisions, old_revision,
                 new_revision, imported_revisions):
        self.include_upstream_history = include_upstream_history
        self.old_upstream_version = old_upstream_version
        self.new_upstream_version = new_upstream_version
        self.upstream_branch = upstream_branch
        self.upstream_branch_browse = upstream_branch_browse
        self.upstream_revisions = upstream_revisions
        self.old_revision = old_revision
        self.new_revision = new_revision
        self.imported_revisions = imported_revisions

    def __tuple__(self):
        # Backwards compatibility
        return (self.old_upstream_version, self.new_upstream_version)


def merge_upstream(tree: Tree, snapshot: bool = False,
                   location: Optional[str] = None,
                   new_upstream_version: Optional[str] = None,
                   force: bool = False,
                   distribution_name: str = DEFAULT_DISTRIBUTION,
                   allow_ignore_upstream_branch: bool = True,
                   trust_package: bool = False,
                   committer: Optional[str] = None,
                   update_changelog: bool = True,
                   subpath: str = '',
                   include_upstream_history: Optional[bool] = None,
                   create_dist: Optional[Callable[
                       [Tree, str, Version, str],
                       Optional[str]]] = None) -> MergeUpstreamResult:
    """Merge a new upstream version into a tree.

    Raises:
      InvalidFormatUpstreamVersion
      PreviousVersionTagMissing
      UnsupportedRepackFormat
      DistCommandFailed
      MissingChangelogError
      MissingUpstreamTarball
      NewUpstreamMissing
      UpstreamBranchUnavailable
      UpstreamAlreadyMerged
      UpstreamAlreadyImported
      UpstreamMergeConflicted
      QuiltError
      UpstreamVersionMissingInUpstreamBranch
      UpstreamBranchUnknown
      PackageIsNative
      InconsistentSourceFormatError
      ChangelogParseError
      UScanError
      NoUpstreamLocationsKnown
      UpstreamMetadataSyntaxError
      NewerUpstreamAlreadyImported
    Returns:
      MergeUpstreamResult object
    """
    if subpath is None:
        subpath = ''
    config = debuild_config(tree, subpath)
    (changelog, top_level) = find_changelog(
        tree, subpath, merge=False, max_blocks=2)
    old_upstream_version = changelog.version.upstream_version
    old_revision = tree.last_revision()
    package = changelog.package
    contains_upstream_source = tree_contains_upstream_source(tree, subpath)
    build_type = config.build_type
    if build_type is None:
        build_type = guess_build_type(
            tree, changelog.version, subpath,
            contains_upstream_source=contains_upstream_source)
    need_upstream_tarball = (build_type != BUILD_TYPE_MERGE)
    if build_type == BUILD_TYPE_NATIVE:
        raise PackageIsNative(changelog.package, changelog.version)

    (primary_upstream_source,
     new_upstream_version,
     upstream_revisions,
     upstream_branch_source,
     upstream_branch,
     upstream_branch_browse,
     files_excluded, include_upstream_history) = find_new_upstream(
        tree, subpath, config, package, location=location,
        old_upstream_version=old_upstream_version,
        new_upstream_version=new_upstream_version, trust_package=trust_package,
        snapshot=snapshot,
        allow_ignore_upstream_branch=allow_ignore_upstream_branch,
        top_level=top_level, include_upstream_history=include_upstream_history,
        create_dist=create_dist)

    if need_upstream_tarball:
        with tempfile.TemporaryDirectory() as target_dir:
            initial_path = os.path.join(target_dir, 'initial')
            os.mkdir(initial_path)

            try:
                locations = primary_upstream_source.fetch_tarballs(
                    package, str(new_upstream_version), initial_path,
                    components=[None])
            except PackageVersionNotPresent as e:
                if upstream_revisions is not None:
                    locations = upstream_branch_source.fetch_tarballs(
                        package, str(new_upstream_version), initial_path,
                        components=[None], revisions=upstream_revisions)
                else:
                    raise NewUpstreamTarballMissing(
                        e.package, e.version, e.upstream)

            orig_path = os.path.join(target_dir, 'orig')
            os.mkdir(orig_path)
            try:
                tarball_filenames = get_tarballs(
                    orig_path, tree, package, new_upstream_version,
                    locations)
            except FileExists as e:
                raise AssertionError(
                    "The target file %s already exists, and is either "
                    "different to the new upstream tarball, or they "
                    "are of different formats. Either delete the target "
                    "file, or use it as the argument to import."
                    % e.path)
            try:
                conflicts, imported_revids = do_merge(
                    tree, subpath, tarball_filenames, package,
                    str(new_upstream_version), str(old_upstream_version),
                    upstream_branch_source.upstream_branch
                    if upstream_branch_source else None,
                    upstream_revisions, merge_type=None,
                    force=force, committer=committer,
                    files_excluded=files_excluded)
            except UpstreamBranchAlreadyMerged:
                # TODO(jelmer): Perhaps reconcile these two exceptions?
                raise UpstreamAlreadyMerged(new_upstream_version)
            except UpstreamAlreadyImported:
                pristine_tar_source = get_pristine_tar_source(
                    tree, tree.branch)
                upstream_revid = None
                imported_revids = []
                for component, revid in (
                        pristine_tar_source.version_as_revisions(
                            package, new_upstream_version).items()):
                    if component is None:
                        upstream_revid = revid
                    upstream_tag = pristine_tar_source.tag_name(
                        new_upstream_version, component)
                    imported_revids.append(
                        (component, upstream_tag, revid, None))
                try:
                    conflicts = tree.merge_from_branch(
                        pristine_tar_source.branch,
                        to_revision=upstream_revid)
                except PointlessMerge:
                    raise UpstreamAlreadyMerged(new_upstream_version)
    else:
        conflicts = 0
        imported_revids = []

    # Re-read changelog, since it may have been changed by the merge
    # from upstream.
    (changelog, top_level) = find_changelog(tree, subpath, False, max_blocks=2)
    package = changelog.package

    if Version(old_upstream_version) >= Version(new_upstream_version):
        if conflicts:
            raise UpstreamMergeConflicted(old_upstream_version, conflicts)
        raise UpstreamAlreadyMerged(new_upstream_version)
    if update_changelog:
        changelog_add_new_version(
            tree, subpath, new_upstream_version, distribution_name, changelog,
            package)
    if not need_upstream_tarball:
        note("An entry for the new upstream version has been "
             "added to the changelog.")
    else:
        if conflicts:
            raise UpstreamMergeConflicted(new_upstream_version, conflicts)

    if update_changelog:
        debcommit(tree, subpath=subpath, committer=committer)
    else:
        tree.commit(
            committer=committer,
            message='Merge new upstream release %s.' % new_upstream_version,
            specific_files=(
                [subpath] if len(tree.get_parent_ids()) <= 1 else None))

    return MergeUpstreamResult(
        include_upstream_history=include_upstream_history,
        old_upstream_version=old_upstream_version,
        new_upstream_version=new_upstream_version,
        old_revision=old_revision,
        new_revision=tree.last_revision(),
        upstream_branch=upstream_branch,
        upstream_branch_browse=upstream_branch_browse,
        upstream_revisions=upstream_revisions,
        imported_revisions=imported_revids)


def override_dh_autoreconf_add_arguments(basedir: str, args):
    from lintian_brush.rules import update_rules

    # TODO(jelmer): Make sure dh-autoreconf is installed,
    # or debhelper version is >= 10

    def update_makefile(mf):
        for rule in mf.iter_rules(b'override_dh_autoreconf'):
            command = rule.commands()[0].split(b' ')
            if command[0] != b'dh_autoreconf':
                return
            rule.lines = [rule.lines[0]]
            command += args
            break
        else:
            rule = mf.add_rule(b'override_dh_autoreconf')
            command = [b'dh_autoreconf'] + args
        rule.append_command(b' '.join(command))

    return update_rules(
        makefile_cb=update_makefile,
        path=os.path.join(basedir, 'debian', 'rules'))


def update_packaging(
        tree: Tree, old_tree: Tree, subpath: str = '',
        committer: Optional[str] = None) -> List[str]:
    """Update packaging to take in changes between upstream trees.

    Args:
      tree: Current tree
      old_tree: Old tree
      committer: Optional committer to use for changes
    """
    notes = []
    tree_delta = tree.changes_from(old_tree, specific_files=[subpath])
    for delta in tree_delta.added:
        path = delta.path[1]
        if path is None:
            continue
        if not path.startswith(subpath):
            continue
        path = path[len(subpath):]
        if path == 'autogen.sh':
            if override_dh_autoreconf_add_arguments(
                    tree.basedir, [b'./autogen.sh']):
                note('Modifying debian/rules: '
                     'Invoke autogen.sh from dh_autoreconf.')
                changelog_add_line(
                    tree, subpath, 'Invoke autogen.sh from dh_autoreconf.',
                    email=committer)
                debcommit(
                    tree, committer=committer,
                    subpath=subpath,
                    paths=['debian/changelog', 'debian/rules'])
        elif path.startswith('LICENSE') or path.startswith('COPYING'):
            notes.append(
                'License file %s has changed.' % os.path.join(subpath, path))
    return notes


class NewUpstreamChanger(DebianChanger):

    name = 'new-upstream'

    def __init__(self, snapshot, trust_package, refresh_patches,
                 update_packaging, dist_command, import_only=False,
                 include_upstream_history=None):
        self.snapshot = snapshot
        self.trust_package = trust_package
        self.refresh_patches = refresh_patches
        self.update_packaging = update_packaging
        self.dist_command = dist_command
        self.import_only = import_only
        self.include_upstream_history = include_upstream_history

    @classmethod
    def setup_parser(cls, parser):
        parser.add_argument(
            '--chroot', type=str, help="Name of chroot",
            default=os.environ.get('CHROOT'))
        parser.add_argument(
            '--trust-package', action='store_true',
            default=False,
            help=argparse.SUPPRESS)
        parser.add_argument(
            '--import-only', action='store_true',
            help='Only import a new version, do not merge.')
        parser.add_argument(
            '--update-packaging', action='store_true',
            default=False,
            help='Attempt to update packaging to upstream changes.')
        parser.add_argument(
            '--snapshot',
            help='Merge a new upstream snapshot rather than a release',
            action='store_true')
        parser.add_argument(
            '--refresh-patches', action="store_true",
            help="Refresh quilt patches after upstream merge.")
        parser.add_argument(
            '--dist-command', type=str,
            help="Command to run to create tarball from source tree.")
        parser.add_argument(
            '--no-include-upstream-history', action="store_false",
            default=None, dest="include_upstream_history",
            help="do not include upstream branch history")
        parser.add_argument(
            '--include-upstream-history', action="store_true",
            dest="include_upstream_history",
            help="force inclusion of upstream history", default=None)

    def suggest_branch_name(self):
        if self.snapshot:
            return SNAPSHOT_BRANCH_NAME
        else:
            return RELEASE_BRANCH_NAME

    @classmethod
    def from_args(cls, args):
        return cls(snapshot=args.snapshot,
                   trust_package=args.trust_package,
                   refresh_patches=args.refresh_patches,
                   update_packaging=args.update_packaging,
                   dist_command=args.dist_command,
                   import_only=args.import_only,
                   include_upstream_history=args.include_upstream_history)

    def create_dist_from_command(self, tree, package, version, target_dir):
        return run_dist_command(
            tree, package, version, target_dir, self.dist_command)

    def make_changes(self, local_tree, subpath, update_changelog, reporter,
                     committer, base_proposal=None):

        base_revids = {
            b.name: b.last_revision()
            for b in local_tree.controldir.list_branches()}

        if control_files_in_root(local_tree, subpath):
            raise ChangerError(
                'control-files-in-root',
                'control files live in root rather than debian/ '
                '(LarstIQ mode)')

        if self.dist_command:
            create_dist = self.create_dist_from_command
        else:
            create_dist = None

        try:
            if not self.import_only:
                try:
                    result = merge_upstream(
                        tree=local_tree, snapshot=self.snapshot,
                        trust_package=self.trust_package,
                        update_changelog=update_changelog,
                        subpath=subpath, committer=committer,
                        include_upstream_history=self.include_upstream_history,
                        create_dist=create_dist)
                except MalformedTransform:
                    traceback.print_exc()
                    error_description = (
                        'Malformed tree transform during new upstream merge')
                    error_code = 'malformed-transform'
                    raise ChangerError(error_code, error_description)
            else:
                result = import_upstream(
                    tree=local_tree, snapshot=self.snapshot,
                    trust_package=self.trust_package,
                    subpath=subpath, committer=committer,
                    include_upstream_history=self.include_upstream_history,
                    create_dist=create_dist)
        except UpstreamAlreadyImported as e:
            reporter.report_context(str(e.version))
            reporter.report_metadata('upstream_version', str(e.version))
            raise ChangerError(
                'nothing-to-do',
                'Last upstream version %s already imported.' % e.version, e)
        except UnsupportedRepackFormat as e:
            error_description = (
                'Unable to repack file %s to supported tarball format.' % (
                    os.path.basename(e.location)))
            raise ChangerError(
                'unsupported-repack-format', error_description)
        except NewUpstreamMissing as e:
            raise ChangerError(
                'new-upstream-missing',
                'Unable to find new upstream source.', e)
        except UpstreamAlreadyMerged as e:
            reporter.report_context(str(e.version))
            reporter.report_metadata('upstream_version', str(e.version))
            raise ChangerError(
                'nothing-to-do',
                'Last upstream version %s already merged.' % e.version, e)
        except PreviousVersionTagMissing as e:
            raise ChangerError(
                'previous-upstream-missing',
                'Previous upstream version %s missing (tag: %s).' % (
                    e.version, e.tag_name), e)
        except InvalidFormatUpstreamVersion as e:
            raise ChangerError(
                'invalid-upstream-version-format',
                '%r reported invalid format version string %s.' % (
                    e.source, e.version), e)
        except PristineTarError as e:
            raise ChangerError(
                'pristine-tar-error', 'Pristine tar error: %s' % e, e)
        except UpstreamBranchUnavailable as e:
            error_description = (
                "The upstream branch at %s was unavailable: %s" % (
                    e.location, e.error))
            error_code = 'upstream-branch-unavailable'
            if 'Fossil branches are not yet supported' in str(e.error):
                error_code = 'upstream-unsupported-vcs-fossil'
            if 'Mercurial branches are not yet supported.' in str(e.error):
                error_code = 'upstream-unsupported-vcs-hg'
            if 'Subversion branches are not yet supported.' in str(
                    e.error):
                error_code = 'upstream-unsupported-vcs-svn'
            if 'Darcs branches are not yet supported' in str(e.error):
                error_code = 'upstream-unsupported-vcs-darcs'
            if 'Unsupported protocol for url' in str(e.error):
                if 'svn://' in str(e.error):
                    error_code = 'upstream-unsupported-vcs-svn'
                elif 'cvs+pserver://' in str(e.error):
                    error_code = 'upstream-unsupported-vcs-cvs'
                else:
                    error_code = 'upstream-unsupported-vcs'
            raise ChangerError(error_code, error_description, e)
        except UpstreamBranchUnknown as e:
            raise ChangerError(
                'upstream-branch-unknown',
                'Upstream branch location unknown. '
                'Set \'Repository\' field in debian/upstream/metadata?', e)
        except UpstreamMergeConflicted as e:
            reporter.report_context(str(e.version))
            reporter.report_metadata('upstream_version', str(e.version))
            reporter.report_metadata('conflicts', e.conflicts)
            raise ChangerError(
                'upstream-merged-conflicts',
                'Merging upstream version %s resulted in conflicts.'
                % e.version, e)
        except PackageIsNative as e:
            raise ChangerError(
                'native-package',
                'Package %s is native; unable to merge new upstream.' % (
                    e.package, ), e)
        except ChangelogParseError as e:
            error_description = str(e)
            error_code = 'unparseable-changelog'
            raise ChangerError(error_code, error_description, e)
        except UpstreamVersionMissingInUpstreamBranch as e:
            error_description = (
                'Upstream version %s not in upstream branch %r' % (
                    e.version, e.branch))
            error_code = 'upstream-version-missing-in-upstream-branch'
            raise ChangerError(error_code, error_description, e)
        except InconsistentSourceFormatError as e:
            raise ChangerError(
                'inconsistent-source-format',
                'Inconsistencies in type of package: %s' % e, e)
        except WatchLineWithoutMatches as e:
            raise ChangerError(
                'uscan-watch-line-without-matches',
                'UScan did not find matches for line: %s' % e.line.strip())
        except NoRoundtrippingSupport:
            error_description = (
                'Unable to import upstream repository into '
                'packaging repository.')
            error_code = 'roundtripping-error'
            raise ChangerError(error_code, error_description)
        except UScanError as e:
            error_description = str(e)
            if e.errors == 'OpenPGP signature did not verify.':
                error_code = 'upstream-pgp-signature-verification-failed'
            else:
                error_code = 'uscan-error'
            raise ChangerError(error_code, error_description, e)
        except UpstreamMetadataSyntaxError as e:
            raise ChangerError(
                'upstream-metadata-syntax-error',
                'Unable to parse %s: %s' % (e.path, e.error), e)
        except InvalidNormalization as e:
            error_description = str(e)
            error_code = 'invalid-path-normalization'
            raise ChangerError(error_code, error_description)
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
        except NewUpstreamTarballMissing as e:
            reporter.report_context(str(e.version))
            reporter.report_metadata('upstream_version', str(e.version))
            raise ChangerError(
                'new-upstream-tarball-missing',
                'New upstream version (%s/%s) found, but was missing '
                'when retrieved as tarball from %r.' % (
                    e.package, e.version, e.upstream))
        except NoUpstreamLocationsKnown as e:
            raise ChangerError(
                'no-upstream-locations-known',
                'No debian/watch file or Repository in '
                'debian/upstream/metadata to retrieve new upstream version'
                'from.', e)
        except NewerUpstreamAlreadyImported as e:
            reporter.report_context(str(e.new_upstream_version))
            raise ChangerError(
                'newer-upstream-version-already-imported',
                'A newer upstream release (%s) has already been imported. '
                'Found: %s' % (e.old_upstream_version, e.new_upstream_version))
        except OSError as e:
            if e.errno == errno.ENOSPC:
                raise ChangerError('no-space-on-device', str(e))
            raise

        reporter.report_metadata(
            'old_upstream_version',
            str(result.old_upstream_version)
            if result.old_upstream_version else None)
        reporter.report_metadata(
            'upstream_version', str(result.new_upstream_version))
        if result.upstream_branch:
            reporter.report_metadata(
                'upstream_branch_url',
                full_branch_url(result.upstream_branch))
            reporter.report_metadata(
                'upstream_branch_browse',
                result.upstream_branch_browse)

        reporter.report_metadata(
            'include_upstream_history', result.include_upstream_history)

        reporter.report_context(str(result.new_upstream_version))

        tags = [
            (('upstream', str(result.new_upstream_version), component),
             tag, revid)
            for (component, tag, revid,
                 pristine_tar_imported) in result.imported_revisions]

        branches = []

        try:
            pristine_tar_branch = local_tree.controldir.open_branch(
                'pristine-tar')
        except NotBranchError:
            pass
        else:
            base_revision = base_revids.get('pristine-tar', NULL_REVISION)
            new_revision = pristine_tar_branch.last_revision()
            if base_revision != new_revision:
                branches.append((
                    'pristine-tar', 'pristine-tar',
                    base_revision, new_revision))

        # TODO(jelmer): ideally, the branch name would be provided by do_merge
        # / do_import
        try:
            upstream_branch = local_tree.controldir.open_branch('upstream')
        except NotBranchError:
            pass
        else:
            base_revision = base_revids.get('upstream', NULL_REVISION)
            new_revision = upstream_branch.last_revision()
            if base_revision != new_revision:
                branches.append(
                    ('upstream', 'upstream', base_revision, new_revision))

        if self.import_only:
            note('Imported new upstream version %s (previous: %s)',
                 result.new_upstream_version,
                 result.old_upstream_version)

            return ChangerResult(
                description="Imported new upstream version %s" % (
                    result.new_upstream_version),
                mutator=result, tags=tags, branches=branches,
                sufficient_for_proposal=True)
        else:
            note('Merged new upstream version %s (previous: %s)',
                 result.new_upstream_version, result.old_upstream_version)

            if self.update_packaging:
                old_tree = local_tree.branch.repository.revision_tree(
                    result.old_revision)
                notes = update_packaging(local_tree, old_tree)
                reporter.report_metadata('notes', notes)
                for n in notes:
                    note('%s', n)

            patch_series_path = os.path.join(
                subpath, 'debian/patches/series')
            if self.refresh_patches and \
                    local_tree.has_filename(patch_series_path):
                note('Refresh quilt patches.')
                try:
                    refresh_quilt_patches(
                        local_tree,
                        old_version=result.old_upstream_version,
                        new_version=result.new_upstream_version,
                        committer=committer,
                        subpath=subpath)
                except QuiltError as e:
                    error_description = (
                        "An error (%d) occurred refreshing quilt patches: "
                        "%s%s" % (e.retcode, e.stderr, e.extra))
                    error_code = 'quilt-refresh-error'
                    raise ChangerError(error_code, error_description, e)
                except QuiltPatchPushFailure as e:
                    error_description = (
                        "An error occurred refreshing quilt patch %s: %s"
                        % (e.patch_name, e.actual_error.extra))
                    error_code = 'quilt-refresh-error'
                    raise ChangerError(error_code, error_description, e)

            branches.append(
                ('main', None,
                 base_revids[local_tree.branch.name],
                 local_tree.last_revision()))

            proposed_commit_message = (
                "Merge new upstream release %s" % result.new_upstream_version)
            return ChangerResult(
                description="Merged new upstream version %s" % (
                    result.new_upstream_version),
                mutator=result, tags=tags,
                branches=branches,
                sufficient_for_proposal=True,
                proposed_commit_message=proposed_commit_message)

    def get_proposal_description(
            self, merge_upstream_result, description_format, unused_proposal):
        return ("Merge new upstream release %s" %
                merge_upstream_result.new_upstream_version)

    def describe(self, merge_upstream_result, publish_result):
        if publish_result.proposal:
            if publish_result.is_new:
                note('Created new merge proposal %s.',
                     publish_result.proposal.url)
            else:
                note('Updated merge proposal %s.',
                     publish_result.proposal.url)


if __name__ == '__main__':
    import sys
    sys.exit(run_mutator(NewUpstreamChanger))
