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

__all__ = [
    'get_source_package',
    'propose_or_push',
    'should_update_changelog',
    'source_package_vcs_url',
    'build',
    'BuildFailedError',
    'MissingUpstreamTarball',
    ]

import apt_pkg
from debian.deb822 import Deb822
from debian.changelog import Version
import itertools

from breezy.branch import Branch
from breezy.plugins.debian.cmds import cmd_builddeb
from breezy.plugins.debian.directory import (
    source_package_vcs_url,
    vcs_field_to_bzr_url_converters,
    )
from breezy.urlutils import InvalidURL
from breezy.plugins.debian.errors import (
    BuildFailedError,
    MissingUpstreamTarball,
    )

from .. import proposal as _mod_proposal


class NoSuchPackage(Exception):
    """No such package."""


def build(directory, builder='sbuild'):
    """Build a debian package in a directory.

    Args:
      directory: Directory to build in
      builder: Builder command (e.g. 'sbuild', 'debuild')
    """
    # TODO(jelmer): Refactor brz-debian so it's not necessary
    # to call out to cmd_builddeb, but to lower-level
    # functions instead.
    cmd_builddeb().run([directory], builder=builder)


def get_source_package(name):
    """Get source package metadata.

    Args:
      name: Name of the source package
    Returns:
      A `Deb822` object
    """
    apt_pkg.init()

    sources = apt_pkg.SourceRecords()

    by_version = {}
    while sources.lookup(name):
        by_version[sources.version] = sources.record

    if len(by_version) == 0:
        raise NoSuchPackage(name)

    # Try the latest version
    version = sorted(by_version, key=Version)[-1]

    return Deb822(by_version[version])


def _changelog_stats(branch, history):
    mixed = 0
    changelog_only = 0
    other_only = 0
    dch_references = 0
    with branch.lock_read():
        graph = branch.repository.get_graph()
        revids = list(itertools.islice(
            graph.iter_lefthand_ancestry(branch.last_revision()), history))
        revs = []
        for revid, rev in branch.repository.iter_revisions(revids):
            if rev is None:
                # Ghost
                continue
            if 'Git-Dch: ' in rev.message:
                dch_references += 1
            revs.append(rev)
        for delta in branch.repository.get_deltas_for_revisions(revs):
            filenames = set([a[0] for a in delta.added] +
                            [r[0] for r in delta.removed] +
                            [r[1] for r in delta.renamed] +
                            [m[0] for m in delta.modified])
            if not set([f for f in filenames if f.startswith('debian/')]):
                continue
            if 'debian/changelog' in filenames:
                if len(filenames) > 1:
                    mixed += 1
                else:
                    changelog_only += 1
            else:
                other_only += 1
    return (changelog_only, other_only, mixed, dch_references)


def should_update_changelog(branch, history=200):
    """Guess whether the changelog should be updated manually.

    Args:
      branch: A branch object
      history: Number of revisions back to analyze
    Returns:
      boolean indicating whether changelog should be updated
    """
    # Two indications this branch may be doing changelog entries at
    # release time:
    # - "Git-Dch: " is used in the commit messages
    # - The vast majority of lines in changelog get added in
    #   commits that only touch the changelog
    (changelog_only, other_only, mixed, dch_references) = _changelog_stats(
            branch, history)
    if dch_references:
        return False
    if changelog_only > mixed:
        # Is this a reasonable threshold?
        return False
    # Assume yes
    return True


def propose_or_push(main_branch, *args, **kwargs):
    """Wrapper for propose_or_push that includes debian-specific branches.
    """
    if getattr(main_branch.repository, '_git', None):
        kwargs['additional_branches'] = (
            kwargs.get('additional_branches', []) +
            ["pristine-tar", "upstream"])
    return _mod_proposal.propose_or_push(main_branch, *args, **kwargs)


def convert_debian_vcs_url(vcs_type, vcs_url):
    converters = dict(vcs_field_to_bzr_url_converters)
    try:
        return converters[vcs_type](vcs_url)
    except KeyError:
        raise ValueError('unknown vcs %s' % vcs_type)
    except InvalidURL as e:
        raise ValueError('invalid URL: %s' % e)


def open_packaging_branch(location, possible_transports=None):
    """Open a packaging branch from a location string.

    location can either be a package name or a full URL
    """
    if '/' not in location:
        pkg_source = get_source_package(location)
        vcs_type, location = source_package_vcs_url(pkg_source)
    return Branch.open(location, possible_transports=possible_transports)
