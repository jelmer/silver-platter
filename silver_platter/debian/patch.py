#!/usr/bin/python
# Copyright (C) 2021 Jelmer Vernooij <jelmer@jelmer.uk>
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

from io import BytesIO
import logging
import os
import posixpath

from .changer import (
    run_mutator,
    DebianChanger,
    ChangerResult,
)

from breezy.patch import patch_tree
from breezy.plugins.debian.changelog import changelog_commit_message


BRANCH_NAME = "patch"


class PatchResult(object):
    def __init__(self, patchname, message):
        self.patchname = patchname
        self.message = message


class PatchChanger(DebianChanger):

    name = "patch"

    def __init__(self, patchname, strip=1, quiet=False):
        self.patchname = patchname
        self.strip = strip
        self.quiet = quiet

    @classmethod
    def setup_parser(cls, parser):
        parser.add_argument('patchname', type=str, help='Path to patch.')
        parser.add_argument('--strip', '-p', type=int, default=1, help="Number of path elements to strip")
        parser.add_argument('--quiet', action='store_true', help='Be quiet')

    @classmethod
    def from_args(cls, args):
        return cls(patchname=args.patchname, strip=args.strip, quiet=args.quiet)

    def suggest_branch_name(self):
        return BRANCH_NAME

    def make_changes(
        self,
        local_tree,
        subpath,
        update_changelog,
        reporter,
        committer,
        base_proposal=None,
    ):
        base_revid = local_tree.last_revision()
        with open(self.patchname, 'rb') as f:
            patches = [f.read()]
        outf = BytesIO()
        patch_tree(
            local_tree, patches, strip=self.strip, quiet=self.quiet,
            out=outf)
        message = changelog_commit_message(
            local_tree, local_tree.basis_tree(),
            path=posixpath.join(subpath, "debian/changelog")
        )
        if not message:
            message = "Apply patch %s." % os.path.basename(self.patchname)
        revid = local_tree.commit(message)
        branches = [("main", None, base_revid, revid)]
        tags = []
        return ChangerResult(
            description=None,
            mutator=PatchResult(message=message, patchname=os.path.basename(self.patchname)),
            branches=branches,
            tags=tags,
            proposed_commit_message=message,
            sufficient_for_proposal=True,
        )

    def get_proposal_description(self, applied, description_format, existing_proposal):
        return applied.message

    def describe(self, result, publish_result):
        if publish_result.is_new:
            logging.info(
                "Proposed change from applying %s: %s", os.path.basename(self.patchname),
                publish_result.proposal.url
            )
        else:
            logging.info("No changes for package %s", result.package_name)

    @classmethod
    def describe_command(cls, command):
        return "Apply patch"


if __name__ == "__main__":
    import sys

    sys.exit(run_mutator(PatchChanger))
