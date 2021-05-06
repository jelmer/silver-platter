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
import errno
import logging
import os
import sys

import breezy
from breezy.revision import NULL_REVISION
from breezy.plugins.debian.upstream.branch import (
    DistCommandFailed,
    )

from lintian_brush import (
    version_string as lintian_brush_version_string,
)
from lintian_brush.debianize import (
    debianize,
    DebianDirectoryExists,
    SourcePackageNameInvalid,
    NoBuildToolsFound,
    DistCreationFailed,
    UnidentifiedError,
    DetailedFailure,
    NoUpstreamReleases,
)
from lintian_brush.config import Config

import silver_platter

from .changer import (
    DebianChanger,
    run_mutator,
    ChangerResult,
    ChangerError,
)


BRANCH_NAME = "debianize"


class DebianizeChanger(DebianChanger):

    name = "debianize"

    def __init__(self, compat_release=None, schroot=None, diligence=0, trust_package=False, verbose=False, force_new_directory=False, upstream_version=None, upstream_version_kind=None):
        self.compat_release = compat_release
        self.schroot = schroot
        self.diligence = diligence
        self.trust = trust_package
        self.verbose = verbose
        self.force_new_directory = force_new_directory
        self.upstream_version = upstream_version
        self.upstream_version_kind = upstream_version_kind

    @classmethod
    def setup_parser(cls, parser):
        parser.add_argument("--compat-release", type=str, help=argparse.SUPPRESS)
        parser.add_argument("--schroot", type=str, help=argparse.SUPPRESS)
        parser.add_argument("--diligence", type=int, default=10, help=argparse.SUPPRESS)
        parser.add_argument(
            "--trust-package", action="store_true", help="Trust package."
        )
        parser.add_argument(
            "--verbose", action="store_true", help="Be verbose.")
        parser.add_argument(
            "--upstream-version", type=str, help="Upstream version to package.")
        parser.add_argument(
            "--upstream-version-kind", type=str, choices=['snapshot', 'release', 'auto'],
            help="Upstream version kind.")
        parser.add_argument(
            '--force-new-directory', action='store_true',
            help='Force creation of a new directory.')

    @classmethod
    def from_args(cls, args):
        if args.schroot:
            schroot = args.schroot
        else:
            schroot = os.environ.get('CHROOT')
        return cls(
            compat_release=args.compat_release, schroot=schroot,
            diligence=args.diligence,
            trust_package=args.trust_package,
            verbose=args.verbose,
            force_new_directory=args.force_new_directory,
            upstream_version=args.upstream_version,
            upstream_version_kind=args.upstream_version_kind)

    def suggest_branch_name(self):
        return BRANCH_NAME

    def make_changes(  # noqa: C901
            self,
            local_tree,
            subpath,
            update_changelog,
            reporter,
            committer,
            base_proposal=None):
        base_revid = local_tree.last_revision()
        upstream_base_revid = NULL_REVISION

        reporter.report_metadata(
            "versions",
            {
                "lintian-brush": lintian_brush_version_string,
                "silver-platter": silver_platter.version_string,
                "breezy": breezy.version_string,
            },
        )

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
                    compat_release, default=compat_release
                )
        if compat_release is None:
            compat_release = debian_info.stable()

        # For now...
        upstream_branch = local_tree.branch
        upstream_subpath = subpath

        with local_tree.lock_write():
            try:
                result = debianize(
                    local_tree, subpath=subpath,
                    upstream_branch=upstream_branch,
                    upstream_subpath=upstream_subpath,
                    compat_release=self.compat_release,
                    schroot=self.schroot,
                    diligence=self.diligence,
                    trust=self.trust,
                    verbose=self.verbose,
                    force_new_directory=self.force_new_directory,
                    create_dist=getattr(self, 'create_dist', None))
            except OSError as e:
                if e.errno == errno.ENOSPC:
                    raise ChangerError(
                        'no-space-on-device', str(e))
                else:
                    raise
            except DebianDirectoryExists:
                raise ChangerError(
                    'debian-directory-exists',
                    "A debian/ directory already exists in the upstream project.")
            except SourcePackageNameInvalid as e:
                raise ChangerError(
                    'invalid-source-package-name',
                    "Generated source package name %r is not valid." % e.source)
            except NoBuildToolsFound:
                raise ChangerError(
                    'no-build-tools',
                    "Unable to find any build systems in upstream sources.")
            except NoUpstreamReleases:
                raise ChangerError(
                    'no-upstream-releases',
                    'The upstream project does not appear to have made any releases.')
            except DistCommandFailed as e:
                raise ChangerError("dist-command-failed", str(e), e)
            except DetailedFailure as e:
                raise ChangerError('dist-%s' % e.error.kind, str(e.error))
            except UnidentifiedError as e:
                if e.secondary:
                    raise ChangerError('dist-command-failed', str(e.secondary.line))
                else:
                    raise ChangerError('dist-command-failed', e.lines[-1])
            except DistCreationFailed as e:
                if e.inner:
                    raise ChangerError('dist-%s' % e.inner.kind, e.msg)
                else:
                    raise ChangerError('dist-command-failed', e.msg)

        # TODO(jelmer): Pristine tar branch?
        branches = [
            ("main", None, base_revid, local_tree.last_revision()),
            ]
        if result.upstream_branch_name:
            branches.append((
                "upstream",
                result.upstream_branch_name,
                upstream_base_revid,
                local_tree.controldir.open_branch(result.upstream_branch_name).last_revision(),
            ))

        tags = [
            (("upstream", str(result.upstream_version), component), tag,
             local_tree.branch.tags.lookup_tag(tag))
            for (component, tag) in result.tag_names.items()
        ]

        reporter.report_metadata("wnpp_bugs", result.wnpp_bugs)

        return ChangerResult(
            description="Debianized package.",
            mutator=None,
            branches=branches,
            tags=tags,
            value=None,
            sufficient_for_proposal=True,
        )

    def get_proposal_description(self, applied, description_format, existing_proposal):
        return "Debianize package."

    def describe(self, applied, publish_result):
        logging.info("Created Debian package.")


if __name__ == "__main__":
    sys.exit(run_mutator(DebianizeChanger))
