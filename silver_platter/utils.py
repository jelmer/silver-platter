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
import socket
import subprocess

from breezy import (
    config as _mod_config,
    errors,
    osutils,
    )
from breezy.branch import (
    Branch,
    BranchWriteLockResult,
    )
from breezy.controldir import ControlDir
from breezy.lock import _RelockDebugMixin, LogicalLockResult
from breezy.revision import NULL_REVISION
import breezy.transport


def create_temp_sprout(branch, additional_colocated_branches=None, dir=None,
                       path=None):
    """Create a temporary sprout of a branch.

    This attempts to fetch the least amount of history as possible.
    """
    if path is None:
        td = osutils.mkdtemp(dir=dir)
    else:
        td = path
        os.mkdir(path)

    def destroy():
        shutil.rmtree(td)
    # Only use stacking if the remote repository supports chks because of
    # https://bugs.launchpad.net/bzr/+bug/375013
    use_stacking = (
        branch._format.supports_stacking() and
        branch.repository._format.supports_chks)
    try:
        # preserve whatever source format we have.
        to_dir = branch.controldir.sprout(
            td, None, create_tree_if_local=True,
            source_branch=branch,
            stacked=use_stacking)
        # TODO(jelmer): Fetch these during the initial clone
        for branch_name in set(additional_colocated_branches or []):
            try:
                add_branch = branch.controldir.open_branch(name=branch_name)
            except (errors.NotBranchError,
                    errors.NoColocatedBranchSupport):
                pass
            else:
                local_add_branch = to_dir.create_branch(name=branch_name)
                add_branch.push(local_add_branch)
                assert (add_branch.last_revision() ==
                        local_add_branch.last_revision())
        return to_dir.open_workingtree(), destroy
    except BaseException as e:
        destroy()
        raise e


class TemporarySprout(object):
    """Create a temporary sprout of a branch.

    This attempts to fetch the least amount of history as possible.
    """

    def __init__(self, branch, additional_colocated_branches=None, dir=None):
        self.branch = branch
        self.additional_colocated_branches = additional_colocated_branches
        self.dir = dir

    def __enter__(self):
        tree, self._destroy = create_temp_sprout(
            self.branch,
            additional_colocated_branches=self.additional_colocated_branches,
            dir=self.dir)
        return tree

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._destroy()
        return False


class PreCheckFailed(Exception):
    """The post check failed."""


def run_pre_check(tree, script):
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


def run_post_check(tree, script, since_revid):
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
            env={'SINCE_REVID': since_revid})
    except subprocess.CalledProcessError:
        raise PostCheckFailed()


class BranchUnavailable(Exception):
    """Opening branch failed."""

    def __init__(self, url, description):
        self.url = url
        self.description = description

    def __str__(self):
        return self.description


class BranchMissing(Exception):
    """Branch did not exist."""

    def __init__(self, url, description):
        self.url = url
        self.description = description

    def __str__(self):
        return self.description


class BranchUnsupported(Exception):
    """The branch uses a VCS or protocol that is unsupported."""

    def __init__(self, url, description):
        self.url = url
        self.description = description

    def __str__(self):
        return self.description


def _convert_exception(url, e):
    if isinstance(e, socket.error):
        return BranchUnavailable(url, 'Socket error: %s' % e)
    if isinstance(e, errors.NotBranchError):
        return BranchMissing(url, 'Branch does not exist: %s' % e)
    if isinstance(e, errors.UnsupportedProtocol):
        return BranchUnsupported(url, str(e))
    if isinstance(e, errors.ConnectionError):
        return BranchUnavailable(url, str(e))
    if isinstance(e, errors.PermissionDenied):
        return BranchUnavailable(url, str(e))
    if isinstance(e,  errors.InvalidHttpResponse):
        return BranchUnavailable(url, str(e))
    if isinstance(e, errors.TransportError):
        return BranchUnavailable(url, str(e))
    if isinstance(e, breezy.transport.UnusableRedirect):
        return BranchUnavailable(url, str(e))
    if isinstance(e, errors.UnsupportedFormatError):
        return BranchUnsupported(url, str(e))
    if isinstance(e, errors.UnknownFormatError):
        return BranchUnsupported(url, str(e))


def open_branch(url, possible_transports=None, probers=None):
    """Open a branch by URL."""
    try:
        transport = breezy.transport.get_transport(
            url, possible_transports=possible_transports)
        dir = ControlDir.open_from_transport(transport, probers)
        return dir.open_branch()
    except Exception as e:
        converted = _convert_exception(url, e)
        if converted is not None:
            raise converted
        raise e


def open_branch_containing(url, possible_transports=None, probers=None):
    """Open a branch by URL."""
    try:
        transport = breezy.transport.get_transport(
            url, possible_transports=possible_transports)
        dir, subpath = ControlDir.open_containing_from_transport(
            transport, probers)
        return dir.open_branch(), subpath
    except Exception as e:
        converted = _convert_exception(url, e)
        if converted is not None:
            raise converted
        raise e


# TODO(jelmer): This should be in breezy
class MinimalMemoryBranch(Branch, _RelockDebugMixin):

    def __init__(self, repository, last_revision_info, tags):
        from breezy.tag import DisabledTags, MemoryTags
        self.repository = repository
        self._last_revision_info = last_revision_info
        self._revision_history_cache = None
        if tags is not None:
            self.tags = MemoryTags(tags)
        else:
            self.tags = DisabledTags(self)
        self._partial_revision_history_cache = []
        self._last_revision_info_cache = None
        self._revision_id_to_revno_cache = None
        self._partial_revision_id_to_revno_cache = {}
        self._partial_revision_history_cache = []
        self.base = 'memory://' + osutils.rand_chars(10)

    def get_config(self):
        return _mod_config.Config()

    def lock_read(self):
        self.repository.lock_read()
        return LogicalLockResult(self.unlock)

    def lock_write(self, token=None):
        self.repository.lock_write()
        return BranchWriteLockResult(self.unlock, None)

    def unlock(self):
        self.repository.unlock()

    def last_revision_info(self):
        return self._last_revision_info

    def _gen_revision_history(self):
        """Generate the revision history from last revision
        """
        last_revno, last_revision = self.last_revision_info()
        self._extend_partial_history()
        return list(reversed(self._partial_revision_history_cache))

    def get_rev_id(self, revno, history=None):
        """Find the revision id of the specified revno."""
        with self.lock_read():
            if revno == 0:
                return NULL_REVISION
            last_revno, last_revid = self.last_revision_info()
            if revno == last_revno:
                return last_revid
            if last_revno is None:
                self._extend_partial_history()
                return self._partial_revision_history_cache[
                        len(self._partial_revision_history_cache) - revno]
            else:
                if revno <= 0 or revno > last_revno:
                    raise errors.NoSuchRevision(self, revno)
                distance_from_last = last_revno - revno
                if len(self._partial_revision_history_cache) <= \
                        distance_from_last:
                    self._extend_partial_history(distance_from_last)
                return self._partial_revision_history_cache[distance_from_last]
