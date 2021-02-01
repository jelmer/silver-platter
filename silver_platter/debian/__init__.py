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
import os
import subprocess
from typing import Optional, Dict, List

from debmutate.vcs import split_vcs_url

from breezy import urlutils
from breezy.branch import Branch
from breezy.errors import UnsupportedFormatError
from breezy.controldir import Prober, ControlDirFormat
from breezy.bzr import RemoteBzrProber
from breezy.git import RemoteGitProber
from breezy.git.repository import GitRepository
from breezy.plugins.debian.cmds import cmd_builddeb
from breezy.plugins.debian.directory import (
    source_package_vcs,
    vcs_field_to_bzr_url_converters,
    )

from breezy.tree import Tree
from breezy.urlutils import InvalidURL
from breezy.workingtree import WorkingTree

from breezy.plugins.debian.changelog import (
    changelog_commit_message,
    )
from breezy.plugins.debian.builder import BuildFailedError
from breezy.plugins.debian.upstream import (
    MissingUpstreamTarball,
    )

from lintian_brush.detect_gbp_dch import guess_update_changelog
from lintian_brush.changelog import add_changelog_entry

from .. import proposal as _mod_proposal
from ..utils import (
    open_branch,
    )


__all__ = [
    'add_changelog_entry',
    'changelog_add_line',
    'apt_get_source_package',
    'guess_update_changelog',
    'source_package_vcs',
    'build',
    'BuildFailedError',
    'MissingUpstreamTarball',
    'vcs_field_to_bzr_url_converters',
    ]


DEFAULT_BUILDER = 'sbuild --no-clean-source'


class NoSuchPackage(Exception):
    """No such package."""


def build(tree: Tree,
          subpath: str = '',
          builder: Optional[str] = None,
          result_dir: Optional[str] = None) -> None:
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
        [tree.abspath(subpath)], builder=builder, result_dir=result_dir)


class NoAptSources(Exception):
    """No apt sources were configured."""


def apt_get_source_package(name: str) -> Deb822:
    """Get source package metadata.

    Args:
      name: Name of the source package
    Returns:
      A `Deb822` object
    """
    import apt_pkg
    apt_pkg.init()

    try:
        sources = apt_pkg.SourceRecords()
    except apt_pkg.Error as e:
        if e.args[0] == (
                'E:You must put some \'deb-src\' URIs in your sources.list'):
            raise NoAptSources()
        raise

    by_version: Dict[str, Deb822] = {}
    while sources.lookup(name):
        by_version[sources.version] = sources.record  # type: ignore

    if len(by_version) == 0:
        raise NoSuchPackage(name)

    # Try the latest version
    version = sorted(by_version, key=Version)[-1]

    return Deb822(by_version[version])


def convert_debian_vcs_url(vcs_type: str, vcs_url: str) -> str:
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
    if '/' not in location and ':' not in location:
        pkg_source = apt_get_source_package(location)
        try:
            (vcs_type, vcs_url) = source_package_vcs(pkg_source)
        except KeyError:
            raise Exception(
                'Package %s does not have any VCS information' % location)
        (url, branch_name, subpath) = split_vcs_url(vcs_url)
    else:
        url, params = urlutils.split_segment_parameters(location)
        branch_name = params.get('branch')
        subpath = ''
    probers = select_probers(vcs_type)
    branch = open_branch(
        url, possible_transports=possible_transports, probers=probers,
        name=branch_name)
    return branch, subpath or ''


def pick_additional_colocated_branches(main_branch):
    ret = ["pristine-tar", "pristine-lfs", "upstream"]
    ret.append('patch-queue/' + main_branch.name)
    if main_branch.name.startswith('debian/'):
        parts = main_branch.name.split('/')
        parts[0] = 'upstream'
        ret.append('/'.join(parts))
    return ret


class Workspace(_mod_proposal.Workspace):

    def __init__(self, main_branch: Branch, *args, **kwargs) -> None:
        if isinstance(main_branch.repository, GitRepository):
            kwargs['additional_colocated_branches'] = (
                kwargs.get('additional_colocated_branches', []) +
                pick_additional_colocated_branches(main_branch))
        super(Workspace, self).__init__(main_branch, *args, **kwargs)

    def build(self, builder: Optional[str] = None,
              result_dir: Optional[str] = None, subpath: str = '') -> None:
        return build(tree=self.local_tree, subpath=subpath, builder=builder,
                     result_dir=result_dir)


def debcommit(tree, committer=None, subpath='', paths=None):
    message = changelog_commit_message(
        tree, tree.basis_tree(),
        path=os.path.join(subpath, 'debian/changelog'))
    if paths:
        specific_files = [os.path.join(subpath, p) for p in paths]
    elif subpath:
        specific_files = [subpath]
    else:
        specific_files = None
    tree.commit(
        committer=committer,
        message=message,
        specific_files=specific_files)


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


def select_preferred_probers(vcs_type: Optional[str] = None) -> List[Prober]:
    probers = list(ControlDirFormat.all_probers())
    if vcs_type:
        try:
            probers.insert(0, prober_registry[vcs_type.lower()])
        except KeyError:
            pass
    return probers


def changelog_add_line(
        tree: WorkingTree,
        subpath: str,
        line: str,
        email: Optional[str] = None) -> None:
    env = {}
    if email:
        env['DEBEMAIL'] = email
    subprocess.check_call(
        ['dch', '--', line], cwd=tree.abspath(subpath), env=env)


def is_debcargo_package(tree: Tree, subpath: str) -> bool:
    debian_path = os.path.join(subpath, 'debian')
    if tree.has_filename(debian_path):
        return False
    control_path = os.path.join(subpath, 'debcargo.toml')
    if tree.has_filename(control_path):
        return True
    return False


def control_files_in_root(tree: Tree, subpath: str) -> bool:
    debian_path = os.path.join(subpath, 'debian')
    if tree.has_filename(debian_path):
        return False
    control_path = os.path.join(subpath, 'control')
    if tree.has_filename(control_path):
        return True
    if tree.has_filename(control_path + '.in'):
        return True
    return False


def control_file_present(tree: Tree, subpath: str) -> bool:
    """Check whether there are any control files present in a tree.

    Args:
      tree: Tree to check
      subpath: subpath to check
    Returns:
      whether control file is present
    """
    for name in ['debian/control', 'debian/control.in', 'control',
                 'control.in']:
        name = os.path.join(subpath, name)
        if tree.has_filename(name):
            return True
    return False
