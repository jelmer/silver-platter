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

import logging
import os
import shutil
import socket
import subprocess
import tempfile
from typing import Callable, Tuple, Optional, List, Union, Dict

from breezy import (
    errors,
    urlutils,
)

from breezy.bzr import LineEndingError

from breezy.branch import (
    Branch,
)
from breezy.controldir import ControlDir, Prober
from breezy.git.remote import RemoteGitError
from breezy.transport import Transport, get_transport
from breezy.workingtree import WorkingTree

from breezy.transport import UnusableRedirect


def create_temp_sprout(
    branch: Branch,
    additional_colocated_branches: Optional[Union[List[str], Dict[str, str]]] = None,
    dir: Optional[str] = None,
    path: Optional[str] = None,
) -> Tuple[WorkingTree, Callable[[], None]]:
    """Create a temporary sprout of a branch.

    This attempts to fetch the least amount of history as possible.
    """
    if path is None:
        td = tempfile.mkdtemp(dir=dir)
    else:
        td = path
        os.mkdir(path)

    def destroy() -> None:
        shutil.rmtree(td)

    # Only use stacking if the remote repository supports chks because of
    # https://bugs.launchpad.net/bzr/+bug/375013
    use_stacking = (
        branch._format.supports_stacking() and  # type: ignore
        branch.repository._format.supports_chks
    )
    try:
        # preserve whatever source format we have.
        to_dir = branch.controldir.sprout(  # type: ignore
            td,
            None,
            create_tree_if_local=True,
            source_branch=branch,
            stacked=use_stacking,
        )
        # TODO(jelmer): Fetch these during the initial clone
        for from_branch_name in set(additional_colocated_branches or []):
            try:
                add_branch = branch.controldir.open_branch(  # type: ignore
                    name=from_branch_name)
            except (errors.NotBranchError, errors.NoColocatedBranchSupport):
                pass
            else:
                if isinstance(additional_colocated_branches, dict):
                    to_branch_name = additional_colocated_branches[from_branch_name]
                else:
                    to_branch_name = from_branch_name
                local_add_branch = to_dir.create_branch(name=to_branch_name)
                add_branch.push(local_add_branch)
                assert add_branch.last_revision() == local_add_branch.last_revision()
        return to_dir.open_workingtree(), destroy
    except BaseException as e:
        destroy()
        raise e


class TemporarySprout(object):
    """Create a temporary sprout of a branch.

    This attempts to fetch the least amount of history as possible.
    """

    def __init__(
        self,
        branch: Branch,
        additional_colocated_branches: Optional[List[str]] = None,
        dir: Optional[str] = None,
    ):
        self.branch = branch
        self.additional_colocated_branches = additional_colocated_branches
        self.dir = dir

    def __enter__(self) -> WorkingTree:
        tree, self._destroy = create_temp_sprout(
            self.branch,
            additional_colocated_branches=self.additional_colocated_branches,
            dir=self.dir,
        )
        return tree

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._destroy()
        return False


class PreCheckFailed(Exception):
    """The post check failed."""


def run_pre_check(tree: WorkingTree, script: Optional[str]) -> None:
    """Run a script ahead of making any changes to a tree.

    Args:
      tree: The working tree to operate in
      script: Command to run
    Raises:
      PreCheckFailed: If the pre-check failed
    """
    if not script:
        return
    try:
        subprocess.check_call(script, shell=True, cwd=tree.basedir)
    except subprocess.CalledProcessError:
        raise PreCheckFailed()


class PostCheckFailed(Exception):
    """The post check failed."""


def run_post_check(
    tree: WorkingTree, script: Optional[str], since_revid: bytes
) -> None:
    """Run a script after making any changes to a tree.

    Args:
      tree: The working tree to operate in
      script: Command to run
      since_revid: Revision id since which changes were made
    Raises:
      PostCheckFailed: If the pre-check failed
    """
    if not script:
        return
    try:
        subprocess.check_call(
            script, shell=True, cwd=tree.basedir, env={"SINCE_REVID": since_revid}
        )
    except subprocess.CalledProcessError:
        raise PostCheckFailed()


class BranchUnavailable(Exception):
    """Opening branch failed."""

    def __init__(self, url: str, description: str):
        self.url = url
        self.description = description

    def __str__(self) -> str:
        return self.description


class BranchRateLimited(Exception):
    """Opening branch was rate-limited."""

    def __init__(self, url: str, description: str, retry_after: Optional[int] = None):
        self.url = url
        self.description = description
        self.retry_after = retry_after

    def __str__(self) -> str:
        if self.retry_after is not None:
            return "%s (retry after %s)" % (self.description, self.retry_after)
        else:
            return self.description


class BranchMissing(Exception):
    """Branch did not exist."""

    def __init__(self, url: str, description: str):
        self.url = url
        self.description = description

    def __str__(self) -> str:
        return self.description


class BranchUnsupported(Exception):
    """The branch uses a VCS or protocol that is unsupported."""

    def __init__(self, url: str, description: str):
        self.url = url
        self.description = description

    def __str__(self) -> str:
        return self.description


def _convert_exception(url: str, e: Exception) -> Optional[Exception]:
    if isinstance(e, socket.error):
        return BranchUnavailable(url, "Socket error: %s" % e)
    if isinstance(e, errors.NotBranchError):
        return BranchMissing(url, "Branch does not exist: %s" % e)
    if isinstance(e, errors.UnsupportedProtocol):
        return BranchUnsupported(url, str(e))
    if isinstance(e, errors.ConnectionError):
        return BranchUnavailable(url, str(e))
    if isinstance(e, errors.PermissionDenied):
        return BranchUnavailable(url, str(e))
    if isinstance(e, errors.InvalidHttpResponse):
        if "Unexpected HTTP status 429" in str(e):
            if hasattr(e, 'headers'):
                try:
                    retry_after = int(e.headers['Retry-After'])  # type: ignore
                except TypeError:
                    logging.warning(
                        'Unable to parse retry-after header: %s',
                        e.headers['Retry-After'])  # type: ignore
                    retry_after = None
                else:
                    retry_after = None
            else:
                # Breezy < 3.2.1
                retry_after = None
            raise BranchRateLimited(url, str(e), retry_after=retry_after)
        return BranchUnavailable(url, str(e))
    if isinstance(e, errors.TransportError):
        return BranchUnavailable(url, str(e))
    if UnusableRedirect is not None and isinstance(e, UnusableRedirect):
        return BranchUnavailable(url, str(e))
    if isinstance(e, errors.UnsupportedFormatError):
        return BranchUnsupported(url, str(e))
    if isinstance(e, errors.UnknownFormatError):
        return BranchUnsupported(url, str(e))
    if isinstance(e, RemoteGitError):
        return BranchUnavailable(url, str(e))
    if isinstance(e, LineEndingError):
        return BranchUnavailable(url, str(e))
    return None


def open_branch(
    url: str,
    possible_transports: Optional[List[Transport]] = None,
    probers: Optional[List[Prober]] = None,
    name: str = None,
) -> Branch:
    """Open a branch by URL."""
    url, params = urlutils.split_segment_parameters(url)
    if name is None:
        try:
            name = urlutils.unquote(params["branch"])
        except KeyError:
            name = None
    try:
        transport = get_transport(url, possible_transports=possible_transports)
        dir = ControlDir.open_from_transport(transport, probers)
        return dir.open_branch(name=name)
    except Exception as e:
        converted = _convert_exception(url, e)
        if converted is not None:
            raise converted
        raise e


def open_branch_containing(
    url: str,
    possible_transports: Optional[List[Transport]] = None,
    probers: Optional[List[Prober]] = None,
) -> Tuple[Branch, str]:
    """Open a branch by URL."""
    try:
        transport = get_transport(url, possible_transports=possible_transports)
        dir, subpath = ControlDir.open_containing_from_transport(transport, probers)  # type: ignore
        return dir.open_branch(), subpath
    except Exception as e:
        converted = _convert_exception(url, e)
        if converted is not None:
            raise converted
        raise e


def full_branch_url(branch):
    """Get the full URL for a branch.

    Ideally this should just return Branch.user_url,
    but that currently exclude the branch name
    in some situations.
    """
    if branch.name is None:
        return branch.user_url
    url, params = urlutils.split_segment_parameters(branch.user_url)
    if branch.name != "":
        params["branch"] = urlutils.quote(branch.name, "")
    return urlutils.join_segment_parameters(url, params)
