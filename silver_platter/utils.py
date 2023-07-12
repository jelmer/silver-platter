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

import os
import shutil
import subprocess
import tempfile
from typing import Callable, Dict, Optional, Tuple

from breezy import errors
from breezy.branch import Branch
from breezy.controldir import NoColocatedBranchSupport
from breezy.revision import RevisionID
from breezy.workingtree import WorkingTree
from . import _svp_rs


def create_temp_sprout(
    branch: Branch,
    additional_colocated_branches: Optional[Dict[str, str]] = None,
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
        branch.repository._format.supports_chks  # type: ignore
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
            except (errors.NotBranchError, NoColocatedBranchSupport):
                pass
            else:
                if isinstance(additional_colocated_branches, dict):
                    to_branch_name = additional_colocated_branches[
                        from_branch_name]
                else:
                    to_branch_name = from_branch_name
                local_add_branch = to_dir.create_branch(name=to_branch_name)
                add_branch.push(local_add_branch)
                assert add_branch.last_revision() \
                    == local_add_branch.last_revision()
        return to_dir.open_workingtree(), destroy
    except BaseException as e:
        destroy()
        raise e


class TemporarySprout:
    """Create a temporary sprout of a branch.

    This attempts to fetch the least amount of history as possible.
    """

    def __init__(
        self,
        branch: Branch,
        additional_colocated_branches: Optional[Dict[str, str]] = None,
        dir: Optional[str] = None,
    ) -> None:
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
    tree: WorkingTree, script: Optional[str], since_revid: RevisionID
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
            script, shell=True, cwd=tree.basedir,
            env={"SINCE_REVID": since_revid}
        )
    except subprocess.CalledProcessError:
        raise PostCheckFailed()


BranchTemporarilyUnavailable = _svp_rs.BranchTemporarilyUnavailable
BranchRateLimited = _svp_rs.BranchRateLimited
BranchUnavailable = _svp_rs.BranchUnavailable
BranchMissing = _svp_rs.BranchMissing
BranchUnsupported = _svp_rs.BranchUnsupported
open_branch = _svp_rs.open_branch
open_branch_containing = _svp_rs.open_branch_containing
full_branch_url = _svp_rs.full_branch_url


def get_branch_vcs_type(branch):
    vcs = getattr(branch.repository, "vcs", None)
    if vcs:
        return vcs.abbreviation
    else:
        return "bzr"
