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

import logging
import os
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import breezy.plugins.debian  # For apt: URL support  # noqa: F401
from breezy import urlutils
from breezy.branch import Branch
from breezy.git.repository import GitRepository
from breezy.mutabletree import MutableTree
from breezy.plugins.debian.builder import BuildFailedError
from breezy.plugins.debian.changelog import debcommit
from breezy.plugins.debian.cmds import cmd_builddeb
from breezy.plugins.debian.directory import (
    source_package_vcs,
    vcs_field_to_bzr_url_converters,
)
from breezy.plugins.debian.upstream import MissingUpstreamTarball
from breezy.tree import Tree
from breezy.urlutils import InvalidURL
from breezy.workingtree import WorkingTree
from debian.changelog import Version, get_maintainer
from debian.deb822 import Deb822
from debmutate.changelog import Changelog
from debmutate.changelog import changelog_add_entry as _changelog_add_entry
from debmutate.vcs import split_vcs_url

from .. import workspace as _mod_workspace
from ..probers import select_probers
from ..utils import open_branch

__all__ = [
    "add_changelog_entry",
    "guess_update_changelog",
    "source_package_vcs",
    "build",
    "BuildFailedError",
    "MissingUpstreamTarball",
    "vcs_field_to_bzr_url_converters",
]


DEFAULT_URGENCY = "medium"
DEFAULT_BUILDER = "sbuild --no-clean-source"


class NoSuchPackage(Exception):
    """No such package."""


class NoVcsInformation(Exception):
    """Package does not have any Vcs headers."""


try:
    from lintian_brush.detect_gbp_dch import guess_update_changelog
except ModuleNotFoundError:

    class ChangelogBehaviour:

        def __init__(self, update_changelog, explanation) -> None:
            self.update_changelog = update_changelog
            self.explanation = explanation

        def __str__(self) -> str:
            return self.explanation

        def __repr__(self) -> str:
            return "{}(update_changelog={!r}, explanation={!r})".format(
                type(self).__name__, self.update_changelog,
                self.explanation)

    def guess_update_changelog(
        tree: WorkingTree, debian_path: str, cl: Optional[Changelog] = None
    ) -> Optional[ChangelogBehaviour]:
        logging.warning(
            'Install lintian-brush to detect automatically whether '
            'the changelog should be updated.')
        return ChangelogBehaviour(
            'update_changelog',
            'defaulting to updating changelog since '
            'lintian-brush is not installed')


def add_changelog_entry(
    tree: MutableTree,
    path: str,
    summary: List[str],
    maintainer: Optional[Tuple[str, str]] = None,
    timestamp: Optional[datetime] = None,
    urgency: str = DEFAULT_URGENCY,
) -> None:
    """Add a changelog entry.

    Args:
      tree: Tree to edit
      path: Path to the changelog file
      summary: Entry to add
      maintainer: Maintainer details; tuple of fullname and email
      suppress_warnings: Whether to suppress any warnings from 'dch'
    """
    # TODO(jelmer): This logic should ideally be in python-debian.
    with tree.get_file(path) as f:
        cl = Changelog()
        cl.parse_changelog(
            f, max_blocks=None, allow_empty_author=True, strict=False)
        _changelog_add_entry(
            cl,
            summary=summary,
            maintainer=maintainer,
            timestamp=timestamp,
            urgency=urgency,
        )
    # Workaround until
    # https://salsa.debian.org/python-debian-team/python-debian/-/merge_requests/22
    # lands.
    pieces = []
    for line in cl.initial_blank_lines:
        pieces.append(line.encode(cl._encoding) + b"\n")
    for block in cl._blocks:
        try:
            serialized = block._format(allow_missing_author=True).encode(
                block._encoding
            )
        except TypeError:  # older python-debian
            serialized = bytes(block)
        pieces.append(serialized)
    tree.put_file_bytes_non_atomic(path, b"".join(pieces))


def build(
    tree: WorkingTree,
    subpath: str = "",
    builder: Optional[str] = None,
    result_dir: Optional[str] = None,
) -> None:
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


def apt_get_source_package(apt_repo, name: str) -> Deb822:
    """Get source package metadata.

    Args:
      name: Name of the source package
    Returns:
      A `Deb822` object
    """
    by_version: Dict[Version, Deb822] = {}

    for source in apt_repo.iter_source_by_name(name):
        by_version[source['Version']] = source

    if len(by_version) == 0:
        raise NoSuchPackage(name)

    # Try the latest version
    version = sorted(by_version, key=Version)[-1]

    return by_version[version]


def convert_debian_vcs_url(vcs_type: str, vcs_url: str) -> str:
    converters = dict(vcs_field_to_bzr_url_converters)
    try:
        return converters[vcs_type](vcs_url)
    except KeyError:
        raise ValueError("unknown vcs %s" % vcs_type)
    except InvalidURL as e:
        raise ValueError("invalid URL: %s" % e)


def open_packaging_branch(
        location, possible_transports=None, vcs_type=None, apt_repo=None):
    """Open a packaging branch from a location string.

    location can either be a package name or a full URL
    """
    if apt_repo is None:
        from breezy.plugins.debian.apt_repo import LocalApt
        apt_repo = LocalApt()
    if "/" not in location and ":" not in location:
        with apt_repo:
            pkg_source = apt_get_source_package(apt_repo, location)
        try:
            (vcs_type, vcs_url) = source_package_vcs(pkg_source)
        except KeyError:
            raise NoVcsInformation(location)
        (url, branch_name, subpath) = split_vcs_url(vcs_url)
    else:
        url, params = urlutils.split_segment_parameters(location)
        try:
            branch_name = urlutils.unquote(params["branch"])
        except KeyError:
            branch_name = None
        subpath = ""
    probers = select_probers(vcs_type)
    branch = open_branch(
        url, possible_transports=possible_transports, probers=probers,
        name=branch_name
    )
    return branch, subpath or ""


def pick_additional_colocated_branches(
        main_branch: Branch) -> Dict[str, str]:
    ret = {
        "pristine-tar": "pristine-tar",
        "pristine-lfs": "pristine-lfs",
        "upstream": "upstream",
        "patch-queue/" + main_branch.name: "patch-queue",  # type: ignore
    }
    if main_branch.name.startswith("debian/"):  # type: ignore
        parts = main_branch.name.split("/")  # type: ignore
        parts[0] = "upstream"
        ret["/".join(parts)] = "upstream"
    existing_branch_names = main_branch.controldir.branch_names()
    return {k: v for (k, v) in ret.items() if k in existing_branch_names}


class Workspace(_mod_workspace.Workspace):
    def __init__(self, main_branch: Branch, *args, **kwargs) -> None:
        if isinstance(main_branch.repository, GitRepository):
            if "additional_colocated_branches" not in kwargs:
                kwargs["additional_colocated_branches"] = {}
            kwargs["additional_colocated_branches"].update(
                pick_additional_colocated_branches(main_branch))
        super().__init__(main_branch, *args, **kwargs)

    @classmethod
    def from_apt_package(cls, package, dir=None):
        main_branch = open_packaging_branch(package)
        return cls(main_branch=main_branch, dir=dir)

    def build(
        self,
        builder: Optional[str] = None,
        result_dir: Optional[str] = None,
        subpath: str = "",
    ) -> None:
        return build(
            tree=self.local_tree,
            subpath=subpath,
            builder=builder,
            result_dir=result_dir,
        )

    def commit(self, message=None, subpath="", paths=None, committer=None,
               reporter=None):
        return debcommit(
            self.local_tree, committer=committer, subpath=subpath,
            paths=paths, reporter=reporter, message=message)


def is_debcargo_package(tree: Tree, subpath: str) -> bool:
    control_path = os.path.join(subpath, "debian", "debcargo.toml")
    return tree.has_filename(control_path)


def control_files_in_root(tree: Tree, subpath: str) -> bool:
    debian_path = os.path.join(subpath, "debian")
    if tree.has_filename(debian_path):
        return False
    control_path = os.path.join(subpath, "control")
    if tree.has_filename(control_path):
        return True
    return tree.has_filename(control_path + ".in")


def _get_maintainer_from_env(env):
    old_env = dict(os.environ.items())
    try:
        os.environ.update(env)
        return get_maintainer()
    finally:
        os.environ.clear()
        os.environ.update(old_env)
