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

from debian.deb822 import Deb822
from debian.changelog import Version
import itertools
import subprocess

from breezy import version_info as breezy_version
from breezy.errors import UnsupportedFormatError
from breezy.controldir import Prober, ControlDirFormat
from breezy.bzr import RemoteBzrProber
from breezy.git import RemoteGitProber
from breezy.plugins.debian.cmds import cmd_builddeb
from breezy.plugins.debian.directory import (
    source_package_vcs_url,
    vcs_field_to_bzr_url_converters,
    )

from breezy.urlutils import InvalidURL
from breezy.plugins.debian.changelog import (
    changelog_commit_message,
    )
try:
    from breezy.plugins.debian.builder import BuildFailedError
except ImportError:
    from breezy.plugins.debian.errors import BuildFailedError
from breezy.plugins.debian.errors import (
    MissingUpstreamTarball,
    )

from .. import proposal as _mod_proposal
from ..utils import (
    open_branch,
    )


__all__ = [
    'changelog_add_line',
    'get_source_package',
    'should_update_changelog',
    'source_package_vcs_url',
    'build',
    'BuildFailedError',
    'MissingUpstreamTarball',
    'vcs_field_to_bzr_url_converters',
    ]


DEFAULT_BUILDER = 'sbuild --no-clean-source'


class NoSuchPackage(Exception):
    """No such package."""


def build(tree, subpath='', builder=None, result_dir=None):
    """Build a debian package in a directory.

    Args:
      tree: Working tree
      subpath: Subpath to build in
      builder: Builder command (e.g. 'sbuild', 'debuild')
      result_dir: Directory to copy results to
    """
    if builder is None:
        builder = DEFAULT_BUILDER
    # TODO(jelmer): Refactor brz-debian so it's not necessary
    # to call out to cmd_builddeb, but to lower-level
    # functions instead.
    cmd_builddeb().run(
        [tree.local_abspath(subpath)], builder=builder, result_dir=result_dir)


def get_source_package(name):
    """Get source package metadata.

    Args:
      name: Name of the source package
    Returns:
      A `Deb822` object
    """
    import apt_pkg
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
            if breezy_version >= (3, 1):
                filenames = set(
                    [a.path[1] for a in delta.added] +
                    [r.path[0] for r in delta.removed] +
                    [r.path[0] for r in delta.renamed] +
                    [r.path[1] for r in delta.renamed] +
                    [m.path[0] for m in delta.modified])
            else:
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


def convert_debian_vcs_url(vcs_type, vcs_url):
    converters = dict(vcs_field_to_bzr_url_converters)
    try:
        return converters[vcs_type](vcs_url)
    except KeyError:
        raise ValueError('unknown vcs %s' % vcs_type)
    except InvalidURL as e:
        raise ValueError('invalid URL: %s' % e)


def open_packaging_branch(location, possible_transports=None, vcs_type=None):
    """Open a packaging branch from a location string.

    location can either be a package name or a full URL
    """
    if '/' not in location:
        pkg_source = get_source_package(location)
        vcs_type, location = source_package_vcs_url(pkg_source)
    probers = select_probers(vcs_type)
    return open_branch(
        location, possible_transports=possible_transports, probers=probers)


def pick_additional_colocated_branches(main_branch):
    ret = ["pristine-tar", "upstream"]
    ret.append('patch-queue/' + main_branch.name)
    if main_branch.name.startswith('debian/'):
        parts = main_branch.name.split('/')
        parts[0] = 'upstream'
        ret.append('/'.join(parts))
    return ret


class Workspace(_mod_proposal.Workspace):

    def __init__(self, main_branch, *args, **kwargs):
        if getattr(main_branch.repository, '_git', None):
            kwargs['additional_colocated_branches'] = (
                kwargs.get('additional_colocated_branches', []) +
                pick_additional_colocated_branches(main_branch))
        super(Workspace, self).__init__(main_branch, *args, **kwargs)

    def build(self, builder=None, result_dir=None, subpath=''):
        return build(tree=self.local_tree, subpath=subpath, builder=builder,
                     result_dir=result_dir)


def debcommit(tree, committer=None, paths=None):
    message = changelog_commit_message(tree, tree.basis_tree())
    tree.commit(
        committer=committer,
        message=message,
        specific_files=paths)


class UnsupportedVCSProber(Prober):

    def __init__(self, vcs_type):
        self.vcs_type = vcs_type

    def __eq__(self, other):
        return (isinstance(other, type(self)) and
                other.vcs_type == self.vcs_type)

    def __call__(self):
        # The prober expects to be registered as a class.
        return self

    def priority(self, transport):
        return 200

    def probe_transport(self, transport):
        raise UnsupportedFormatError(
            'This VCS %s is not currently supported.' %
            self.vcs_type)

    @classmethod
    def known_formats(klass):
        return []


prober_registry = {
    'bzr': RemoteBzrProber,
    'git': RemoteGitProber,
}

try:
    from breezy.plugins.fossil import RemoteFossilProber
except ImportError:
    pass
else:
    prober_registry['fossil'] = RemoteFossilProber

try:
    from breezy.plugins.svn import SvnRepositoryProber
except ImportError:
    pass
else:
    prober_registry['svn'] = SvnRepositoryProber

try:
    from breezy.plugins.hg import SmartHgProber
except ImportError:
    pass
else:
    prober_registry['hg'] = SmartHgProber

try:
    from breezy.plugins.darcs import DarcsProber
except ImportError:
    pass
else:
    prober_registry['darcs'] = DarcsProber

try:
    from breezy.plugins.cvs import CVSProber
except ImportError:
    pass
else:
    prober_registry['cvs'] = CVSProber


def select_probers(vcs_type=None):
    if vcs_type is None:
        return None
    try:
        return [prober_registry[vcs_type.lower()]]
    except KeyError:
        return [UnsupportedVCSProber(vcs_type)]


def select_preferred_probers(vcs_type=None):
    probers = list(ControlDirFormat.all_probers())
    if vcs_type:
        try:
            probers.insert(0, prober_registry[vcs_type.lower()])
        except KeyError:
            pass
    return probers


def changelog_add_line(tree, line, email):
    env = {}
    if email:
        env['DEBEMAIL'] = email
    subprocess.check_call(['dch', '--', line], cwd=tree.basedir, env=env)
