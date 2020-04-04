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

import contextlib
import json
import os
import subprocess
import tempfile

from urllib.request import urlopen

from debian.changelog import Changelog

from .changer import (
    run_changer,
    DebianChanger,
    setup_multi_parser as setup_changer_parser,
    )
from breezy.trace import note
from breezy.plugins.debian.errors import PackageVersionNotPresent


BRANCH_NAME = 'missing-commits'


def select_vcswatch_packages():
    import psycopg2
    conn = psycopg2.connect(
        database="udd",
        user="udd-mirror",
        password="udd-mirror",
        host="udd-mirror.debian.net")
    cursor = conn.cursor()
    args = []
    query = """\
    SELECT sources.source, vcswatch.url
    FROM vcswatch JOIN sources ON sources.source = vcswatch.source
    WHERE
     vcswatch.status IN ('OLD', 'UNREL') AND
     sources.release = 'sid'
"""
    cursor.execute(query, tuple(args))
    packages = []
    for package, vcs_url in cursor.fetchall():
        packages.append(package)
    return packages


def download_snapshot(package, version, output_dir, no_preparation=False):
    note('Downloading %s %s', package, version)
    srcfiles_url = ('https://snapshot.debian.org/mr/package/%s/%s/'
                    'srcfiles?fileinfo=1' % (package, version))
    files = {}
    for hsh, entries in json.load(urlopen(srcfiles_url))['fileinfo'].items():
        for entry in entries:
            files[entry['name']] = hsh
    for filename, hsh in files.items():
        local_path = os.path.join(output_dir, os.path.basename(filename))
        with open(local_path, 'wb') as f:
            url = 'https://snapshot.debian.org/file/%s' % hsh
            with urlopen(url) as g:
                f.write(g.read())
    args = []
    if no_preparation:
        args.append('--no-preparation')
    subprocess.check_call(
        ['dpkg-source'] + args + ['-x', '%s_%s.dsc' % (package, version)],
        cwd=output_dir)


class UncommittedChanger(DebianChanger):

    @classmethod
    def setup_parser(cls, parser):
        pass

    @classmethod
    def from_args(cls, args):
        return cls()

    def suggest_branch_name(self):
        return BRANCH_NAME

    def make_changes(self, local_tree, subpath, update_changelog, committer):
        from breezy.plugins.debian.import_dsc import (
            DistributionBranch,
            DistributionBranchSet,
            )
        cl_path = os.path.join(subpath, 'debian/changelog')
        with local_tree.get_file(cl_path) as f:
            tree_cl = Changelog(f)
            package_name = tree_cl.package
        with contextlib.ExitStack() as es:
            archive_source = es.enter_context(tempfile.TemporaryDirectory())
            subprocess.check_call(
                ['apt-get', 'source', package_name], cwd=archive_source)
            [subdir] = [
                e.path for e in os.scandir(archive_source) if e.is_dir()]
            with open(os.path.join(subdir, 'debian', 'changelog'), 'r') as f:
                archive_cl = Changelog(f)
            missing_versions = []
            for block in archive_cl:
                if block.version == tree_cl.version:
                    break
                missing_versions.append(block.version)
            else:
                raise Exception(
                    'tree version %s does not appear in archive changelog' %
                    tree_cl.version)
            if len(missing_versions) == 0:
                raise Exception('no missing versions after all')
            note('Missing versions: %s', ', '.join(map(str, missing_versions)))
            ret = []
            dbs = DistributionBranchSet()
            db = DistributionBranch(
                local_tree.branch, local_tree.branch, tree=local_tree)
            dbs.add_branch(db)
            if tree_cl.version.debian_revision:
                note('Extracting upstream version %s.',
                     tree_cl.version.upstream_version)
                upstream_dir = es.enter_context(tempfile.TemporaryDirectory())
                try:
                    upstream_tips = db.pristine_upstream_source\
                        .version_as_revisions(
                                tree_cl.package,
                                tree_cl.version.upstream_version)
                except PackageVersionNotPresent:
                    raise Exception(
                        'unable to find upstream version %r' %
                        tree_cl.version.upstream_version)
                db.extract_upstream_tree(upstream_tips, upstream_dir)
            no_preparation = not local_tree.has_filename('.pc/applied-patches')
            version_path = {}
            for version in missing_versions:
                output_dir = es.enter_context(tempfile.TemporaryDirectory())
                download_snapshot(
                    package_name, version, output_dir,
                    no_preparation=no_preparation)
                version_path[version] = output_dir
            for version in reversed(missing_versions):
                note('Importing %s', version)
                dsc_path = os.path.join(
                    version_path[version],
                    '%s_%s.dsc' % (package_name, version))
                tag_name = db.import_package(dsc_path)
                ret.append((tag_name, version))
        return ret

    def get_proposal_description(
            self, applied, description_format, existing_proposal):
        return "Import missing uploads: %s." % (
            ', '.join([str(v) for t, v in applied]))

    def get_commit_message(self, applied, existing_proposal):
        return "Import missing uploads: %s." % (
            ', '.join([str(v) for t, v in applied]))

    def allow_create_proposal(self, applied):
        return True

    def describe(self, applied, publish_result):
        if publish_result.is_new:
            note('Proposed import of versions %s: %s',
                 ', '.join([str(v) for t, v in applied]),
                 publish_result.proposal.url)
        elif applied:
            note('Updated proposal %s with versions %s.',
                 publish_result.proposal.url,
                 ', '.join([str(v) for t, v in applied]))
        else:
            note('No new versions imported for proposal %s',
                 publish_result.proposal.url)

    def tags(self, applied):
        # TODO(jelmer): Include tags for upstream parts
        return [t for t, v in applied]


def main(args):
    if not args.packages:
        args.packages = select_vcswatch_packages()
    changer = UncommittedChanger.from_args(args)
    return run_changer(changer, args)


def setup_parser(parser):
    setup_changer_parser(parser)
    UncommittedChanger.setup_parser(parser)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(prog='import-upload')
    setup_parser(parser)
    args = parser.parse_args()
    main(args)
