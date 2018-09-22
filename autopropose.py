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

"""Autopropose implementation."""

from __future__ import absolute_import

import argparse
import os
import subprocess
import shutil
import sys
import tempfile

if os.name == "posix":
    import locale
    locale.setlocale(locale.LC_ALL, '')
    # Use better default than ascii with posix filesystems that deal in bytes
    # natively even when the C locale or no locale at all is given. Note that
    # we need an immortal string for the hack, hence the lack of a hyphen.
    sys._brz_default_fs_enc = "utf8"

import breezy
breezy.initialize()
import breezy.bzr
import breezy.git
import breezy.plugins.launchpad
import breezy.plugins.debian
from breezy import osutils
from breezy import (
    branch as _mod_branch,
    errors,
    )
from breezy.i18n import gettext
from breezy.commit import PointlessCommit
from breezy.trace import note
from breezy.transport import get_transport
from breezy.plugins.propose import (
    propose as _mod_propose,
    )


def script_runner(branch, script):
    local_tree = branch.controldir.create_workingtree()
    p = subprocess.Popen(script, cwd=local_tree.basedir, stdout=subprocess.PIPE)
    (description, err) = p.communicate("")
    if p.returncode != 0:
        raise errors.BzrCommandError(
            gettext("Script %s failed with error code %d") % (
                script, p.returncode))
    try:
        local_tree.commit(description, allow_pointless=False)
    except PointlessCommit:
        raise errors.BzrCommandError(gettext(
            "Script didn't make any changes"))
    return description


def autopropose(main_branch, callback, name, overwrite=False, labels=None):
    hoster = _mod_propose.get_hoster(main_branch)
    try:
        existing_branch = hoster.get_derived_branch(main_branch, name=name)
    except errors.NotBranchError:
        pass
    else:
        raise errors.AlreadyBranchError(name)
    td = tempfile.mkdtemp()
    try:
        # preserve whatever source format we have.
        to_dir = main_branch.controldir.sprout(
                get_transport(td).base, None, create_tree_if_local=False,
                source_branch=main_branch)
        local_branch = to_dir.open_branch()
        orig_revid = local_branch.last_revision()
        description = callback(local_branch)
        if local_branch.last_revision() == orig_revid:
            raise PointlessCommit()
        remote_branch, public_branch_url = hoster.publish_derived(
            local_branch, main_branch, name=name, overwrite=overwrite)
    finally:
        shutil.rmtree(td)
    proposal_builder = hoster.get_proposer(remote_branch, main_branch)
    return proposal_builder.create_proposal(description=description, labels=labels)


parser = argparse.ArgumentParser()
parser.add_argument('url', help='URL of branch to work on.', type=str)
parser.add_argument('script', help='Path to script to run.', type=str)
parser.add_argument('--overwrite', action="store_true", help='Overwrite changes when publishing')
parser.add_argument('--label', type=str, help='Label to attach', action="append", default=[])
parser.add_argument('--name', type=str, help='Proposed branch name', default=None)
args = parser.parse_args()

main_branch = _mod_branch.Branch.open(args.url)
if args.name is None:
    name = os.path.splitext(osutils.basename(args.script.split(' ')[0]))[0]
else:
    name = args.name
proposal = autopropose(
        main_branch, lambda branch: script_runner(branch, args.script),
        name=name, overwrite=args.overwrite, labels=args.label)
note(gettext('Merge proposal created: %s') % proposal.url)
