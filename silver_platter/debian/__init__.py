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

from typing import Dict, Optional

import breezy.plugins.debian  # For apt: URL support  # noqa: F401
from breezy import urlutils
from breezy.branch import Branch
from breezy.git.repository import GitRepository
from breezy.plugins.debian.builder import BuildFailedError
from breezy.plugins.debian.changelog import debcommit
from breezy.plugins.debian.directory import (
    source_package_vcs,
    vcs_field_to_bzr_url_converters,
)
from breezy.plugins.debian.upstream import MissingUpstreamTarball
from breezy.urlutils import InvalidURL
from debmutate.vcs import split_vcs_url

from debian.changelog import Version
from debian.deb822 import Deb822

from .. import workspace as _mod_workspace
from .._svp_rs import (
    MissingChangelog,
    build,
    control_files_in_root,
    guess_update_changelog,
    is_debcargo_package,
    pick_additional_colocated_branches,
)
from .._svp_rs import (
    get_maintainer_from_env as _get_maintainer_from_env,
)
from ..probers import select_probers
from ..utils import open_branch

__all__ = [
    "MissingChangelog",
    "is_debcargo_package",
    "control_files_in_root",
    "_get_maintainer_from_env",
    "source_package_vcs",
    "build",
    "BuildFailedError",
    "MissingUpstreamTarball",
    "vcs_field_to_bzr_url_converters",
    "guess_update_changelog",
]


DEFAULT_URGENCY = "medium"
DEFAULT_BUILDER = "sbuild --no-clean-source"


class NoSuchPackage(Exception):
    """No such package."""


class NoVcsInformation(Exception):
    """Package does not have any Vcs headers."""


def apt_get_source_package(apt_repo, name: str) -> Deb822:
    """Get source package metadata.

    Args:
      name: Name of the source package
    Returns:
      A `Deb822` object
    """
    by_version: Dict[Version, Deb822] = {}

    for source in apt_repo.iter_source_by_name(name):
        by_version[source["Version"]] = source

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
    location, possible_transports=None, vcs_type=None, apt_repo=None
):
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
        url,
        possible_transports=possible_transports,
        probers=probers,
        name=branch_name,
    )
    return branch, subpath or ""


class Workspace(_mod_workspace.Workspace):
    def __init__(self, main_branch: Branch, *args, **kwargs) -> None:
        if isinstance(main_branch.repository, GitRepository):
            if "additional_colocated_branches" not in kwargs:
                kwargs["additional_colocated_branches"] = {}
            kwargs["additional_colocated_branches"].update(
                pick_additional_colocated_branches(main_branch)
            )
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

    def commit(
        self,
        message=None,
        subpath="",
        paths=None,
        committer=None,
        reporter=None,
    ):
        return debcommit(
            self.local_tree,
            committer=committer,
            subpath=subpath,
            paths=paths,
            reporter=reporter,
            message=message,
        )
