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

import argparse
import sys

import breezy
from breezy.trace import note

from lintian_brush import (
    version_string as lintian_brush_version_string,
    )
from lintian_brush.debianize import (
    debianize,
    )
from lintian_brush.config import Config

import silver_platter

from . import (
    control_files_in_root,
    )
from .changer import (
    DebianChanger,
    ChangerResult,
    run_mutator,
    ChangerError,
    )


BRANCH_NAME = "debianize"


class DebianizeChanger(DebianChanger):

    name = 'debianize'

    def __init__(self, compat_release=None):
        self.compat_release = compat_release

    @classmethod
    def setup_parser(cls, parser):
        parser.add_argument(
            '--compat-release', type=str, help=argparse.SUPPRESS)

    @classmethod
    def from_args(cls, args):
        return cls(compat_release=args.compat_release)

    def suggest_branch_name(self):
        return BRANCH_NAME

    def make_changes(self, local_tree, subpath, update_changelog,
                     reporter, committer, base_proposal=None):
        base_revid = local_tree.last_revision()

        reporter.report_metadata('versions', {
            'lintian-brush': lintian_brush_version_string,
            'silver-platter': silver_platter.version_string,
            'breezy': breezy.version_string,
        })

        import distro_info
        debian_info = distro_info.DebianDistroInfo()

        compat_release = self.compat_release
        try:
            cfg = Config.from_workingtree(local_tree, subpath)
        except FileNotFoundError:
            pass
        else:
            compat_release = cfg.compat_release()
            if compat_release:
                compat_release = debian_info.codename(
                    compat_release, default=compat_release)
        if compat_release is None:
            compat_release = debian_info.stable()

        with local_tree.lock_write():
            if control_files_in_root(local_tree, subpath):
                raise ChangerError(
                    'control-files-in-root',
                    'control files live in root rather than debian/ '
                    '(LarstIQ mode)')

            debianize(
                local_tree, subpath=subpath,
                compat_release=self.compat_release)

        branches = [
            ('main', None, base_revid,
             local_tree.last_revision())]

        return ChangerResult(
            description='Debianized package.',
            mutator=None,
            branches=branches, tags=[],
            value=None,
            sufficient_for_proposal=True)

    def get_proposal_description(
            self, applied, description_format, existing_proposal):
        return "Debianize package."

    def describe(self, applied, publish_result):
        note('Created Debian package.')


if __name__ == '__main__':
    sys.exit(run_mutator(DebianizeChanger))
