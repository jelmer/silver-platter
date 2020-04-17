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
from debian.changelog import Version
import os
import re
import ssl
import tempfile

from ..utils import (
    open_branch,
    BranchMissing,
    BranchUnavailable,
    BranchUnsupported,
    )

from . import (
    changelog_add_line,
    debcommit,
    )
from .changer import ChangerError, DebianChanger
from breezy.commit import (
    PointlessCommit,
    )
from breezy.errors import (
    FileExists,
    NoSuchFile,
    PointlessMerge,
    InvalidHttpResponse,
    )
from breezy.plugins.debian.config import (
    UpstreamMetadataSyntaxError
    )
from breezy.plugins.debian.errors import (
    InconsistentSourceFormatError,
    MissingUpstreamTarball,
    PackageVersionNotPresent,
    UpstreamAlreadyImported,
    UpstreamBranchAlreadyMerged,
    UnparseableChangelog,
    )

from breezy.trace import note, warning

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
try:
    from breezy.plugins.quilt.quilt import (
        QuiltError,
        QuiltPatches,
        )
except ImportError:
    from breezy.plugins.debian.quilt.quilt import (
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
    UScanSource,
    TarfileSource,
    UScanError,
    )
from breezy.plugins.debian.upstream.branch import (
    UpstreamBranchSource,
    DistCommandFailed,
    )

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
    'UpstreamAlreadyMerged',
    'UpstreamAlreadyImported',
    'UpstreamMergeConflicted',
    'QuiltError',
    'UpstreamVersionMissingInUpstreamBranch',
    'UpstreamBranchUnknown',
    'PackageIsNative',
    'UnparseableChangelog',
    'UScanError',
    'UpstreamMetadataSyntaxError',
    'QuiltPatchPushFailure',
]


class NewUpstreamMissing(Exception):
    """Unable to find upstream version to merge."""


class UpstreamBranchUnavailable(Exception):
    """Snapshot merging was requested by upstream branch is unavailable."""

    def __init__(self, location, error):
        self.location = location
        self.error = error


class UpstreamMergeConflicted(Exception):
    """The upstream merge resulted in conflicts."""

    def __init__(self, upstream_version, conflicts):
        self.version = upstream_version
        self.conflicts = conflicts


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


class InvalidFormatUpstreamVersion(Exception):
    """Invalid format upstream version string."""

    def __init__(self, version, source):
        self.version = version
        self.source = source


class QuiltPatchPushFailure(Exception):

    def __init__(self, patch_name, actual_error):
        self.patch_name = patch_name
        self.actual_error = actual_error


RELEASE_BRANCH_NAME = "new-upstream-release"
SNAPSHOT_BRANCH_NAME = "new-upstream-snapshot"
ORIG_DIR = '..'
DEFAULT_DISTRIBUTION = 'unstable'


def refresh_quilt_patches(local_tree, old_version, new_version,
                          committer=None, subpath=''):
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
                    local_tree, 'Drop patch %s, present upstream.' % name,
                    email=committer)
                debcommit(local_tree, committer=committer, paths=[
                    os.path.join(subpath, p) for p in [
                     'debian/patches/series', 'debian/patches/' + name,
                     'debian/changelog']])
            else:
                raise QuiltPatchPushFailure(name, e)
    patches.pop_all()
    try:
        local_tree.commit(
            'Refresh patches.', committer=committer, allow_pointless=False)
    except PointlessCommit:
        pass


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
            ]

    def __init__(self, old_upstream_version, new_upstream_version,
                 upstream_branch, upstream_branch_browse,
                 upstream_revisions, old_revision,
                 new_revision):
        self.old_upstream_version = old_upstream_version
        self.new_upstream_version = new_upstream_version
        self.upstream_branch = upstream_branch
        self.upstream_branch_browse = upstream_branch_browse
        self.upstream_revisions = upstream_revisions
        self.old_revision = old_revision
        self.new_revision = new_revision

    def __tuple__(self):
        # Backwards compatibility
        return (self.old_upstream_version, self.new_upstream_version)


def merge_upstream(tree, snapshot=False, location=None,
                   new_upstream_version=None, force=False,
                   distribution_name=DEFAULT_DISTRIBUTION,
                   allow_ignore_upstream_branch=True,
                   trust_package=False, committer=None,
                   update_changelog=True, subpath='', dist_command=None):
    """Merge a new upstream version into a tree.

    Args:
      dist_command: Command to run to create upstream tarball from source tree
    Raises:
      InvalidFormatUpstreamVersion
      PreviousVersionTagMissing
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
      UnparseableChangelog
      UScanError
      UpstreamMetadataSyntaxError
    Returns:
      MergeUpstreamResult object
    """
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
    need_upstream_tarball = (build_type != BUILD_TYPE_MERGE)
    if build_type == BUILD_TYPE_NATIVE:
        raise PackageIsNative(changelog.package, changelog.version)

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
                dist_command=dist_command)
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
                local_dir=tree.controldir, dist_command=dist_command)
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

    old_revision = tree.last_revision()

    if need_upstream_tarball:
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
            try:
                conflicts = do_merge(
                    tree, subpath, tarball_filenames, package,
                    new_upstream_version, old_upstream_version,
                    upstream_branch, upstream_revisions, merge_type=None,
                    force=force, committer=committer,
                    files_excluded=files_excluded)
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
    else:
        conflicts = 0

    # Re-read changelog, since it may have been changed by the merge
    # from upstream.
    (changelog, top_level) = find_changelog(tree, subpath, False, max_blocks=2)
    old_upstream_version = changelog.version.upstream_version
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
        debcommit(tree, committer=committer)
    else:
        tree.commit(
            committer=committer,
            message='Merge new upstream release %s.' % new_upstream_version)

    return MergeUpstreamResult(
        old_upstream_version=old_upstream_version,
        new_upstream_version=new_upstream_version,
        old_revision=old_revision,
        new_revision=tree.last_revision(),
        upstream_branch=upstream_branch,
        upstream_branch_browse=upstream_branch_browse,
        upstream_revisions=upstream_revisions)


def override_dh_autoreconf_add_arguments(basedir, args):
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


def update_packaging(tree, old_tree, committer=None):
    """Update packaging to take in changes between upstream trees.

    Args:
      tree: Current tree
      old_tree: Old tree
      committer: Optional committer to use for changes
    """
    notes = []
    tree_delta = tree.changes_from(old_tree)
    for delta in tree_delta.added:
        if getattr(delta, 'path', None):
            path = delta.path[1]
        else:  # Breezy < 3.1
            path = delta[0]
        if path is None:
            continue
        if path == 'autogen.sh':
            if override_dh_autoreconf_add_arguments(
                    tree.basedir, [b'./autogen.sh']):
                note('Modifying debian/rules: '
                     'Invoke autogen.sh from dh_autoreconf.')
                changelog_add_line(
                    tree, 'Invoke autogen.sh from dh_autoreconf.',
                    email=committer)
                debcommit(
                    tree, committer=committer,
                    paths=['debian/changelog', 'debian/rules'])
        elif path.startswith('LICENSE') or path.startswith('COPYING'):
            notes.append('License file %s has changed.' % path)
        return notes


class NewUpstreamChanger(DebianChanger):

    def __init__(self, snapshot, trust_package, refresh_patches,
                 update_packaging, dist_command):
        self.snapshot = snapshot
        self.trust_package = trust_package
        self.refresh_patches = refresh_patches
        self.update_packaging = update_packaging
        self.dist_command = dist_command

    @classmethod
    def setup_parser(cls, parser):
        parser.add_argument(
            '--trust-package', action='store_true',
            default=False,
            help=argparse.SUPPRESS)
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
                   dist_command=args.dist_command)

    def make_changes(self, local_tree, subpath, update_changelog, committer):
        try:
            merge_upstream_result = merge_upstream(
                tree=local_tree, snapshot=self.snapshot,
                trust_package=self.trust_package,
                update_changelog=update_changelog,
                subpath=subpath, committer=committer,
                dist_command=self.dist_command)
        except UpstreamAlreadyImported as e:
            raise ChangerError(
                'Last upstream version %s already imported.' % e.version, e)
        except NewUpstreamMissing as e:
            raise ChangerError('Unable to find new upstream.', e)
        except UpstreamAlreadyMerged as e:
            raise ChangerError(
                'Last upstream version %s already merged.' % e.version, e)
        except PreviousVersionTagMissing as e:
            raise ChangerError(
                'Unable to find tag %s for previous upstream version %s.' % (
                    e.tag_name, e.version), e)
        except InvalidFormatUpstreamVersion as e:
            raise ChangerError(
                '%r reported invalid format version string %s.' % (
                    e.source, e.version), e)
        except PristineTarError as e:
            raise ChangerError('Pristine tar error: %s' % e, e)
        except UpstreamBranchUnavailable as e:
            raise ChangerError(
                'Upstream branch %s unavailable: %s. ' % (e.location, e.error),
                e)
        except UpstreamBranchUnknown as e:
            raise ChangerError(
                'Upstream branch location unknown. '
                'Set \'Repository\' field in debian/upstream/metadata?', e)
        except UpstreamMergeConflicted as e:
            raise ChangerError('Merging upstream resulted in conflicts.', e)
        except PackageIsNative as e:
            raise ChangerError(
                'Package %s is native; unable to merge new upstream.' % (
                    e.package, ), e)
        except InconsistentSourceFormatError as e:
            raise ChangerError('Inconsistencies in type of package: %s' % e,
                               e)
        except UScanError as e:
            raise ChangerError('UScan failed: %s' % e, e)
        except UpstreamMetadataSyntaxError as e:
            raise ChangerError('Unable to parse %s' % e.path, e)
        except MissingChangelogError as e:
            raise ChangerError('Missing changelog %s' % e, e)
        except DistCommandFailed as e:
            raise ChangerError('Dist command failed: %s' % e, e)
        except MissingUpstreamTarball as e:
            raise ChangerError('Missing upstream tarball: %s' % e, e)
        else:
            note('Merged new upstream version %s (previous: %s)',
                 merge_upstream_result.new_upstream_version,
                 merge_upstream_result.old_upstream_version)

        if self.update_packaging:
            old_tree = local_tree.branch.repository.revision_tree(
                merge_upstream_result.old_revision)
            notes = update_packaging(local_tree, old_tree)
            for n in notes:
                note('%s', n)

        if self.refresh_patches and \
                local_tree.has_filename('debian/patches/series'):
            note('Refresh quilt patches.')
            try:
                refresh_quilt_patches(
                    local_tree,
                    old_version=merge_upstream_result.old_upstream_version,
                    new_version=merge_upstream_result.new_upstream_version)
            except QuiltError as e:
                raise ChangerError(
                    'Quilt error while refreshing patches: %s', e)

        return merge_upstream_result

    def get_proposal_description(
            self, merge_upstream_result, description_format, unused_proposal):
        return ("Merge new upstream release %s" %
                merge_upstream_result.new_upstream_version)

    def get_commit_message(self, merge_upstream_result, unused_proposal):
        return ("Merge new upstream release %s" %
                merge_upstream_result.new_upstream_version)

    def allow_create_proposal(self, merge_upstream_result):
        return True

    def describe(self, merge_upstream_result, publish_result):
        if publish_result.proposal:
            if publish_result.is_new:
                note('Created new merge proposal %s.',
                     publish_result.proposal.url)
            else:
                note('Updated merge proposal %s.',
                     publish_result.proposal.url)

    def tags(self, applied):
        return ['upstream/%s' % applied.new_upstream_version]


def main(args):
    from .changer import run_changer
    changer = NewUpstreamChanger.from_args(args)
    return run_changer(changer, args)


def setup_parser(parser):
    from .changer import setup_multi_parser
    setup_multi_parser(parser)
    NewUpstreamChanger.setup_parser(parser)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(prog='propose-new-upstream')
    setup_parser(parser)
    args = parser.parse_args()
    main(args)
