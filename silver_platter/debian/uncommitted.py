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

from . import NoAptSources
from .changer import (
    run_mutator,
    DebianChanger,
    ChangerResult,
    ChangerError,
    )
from breezy.trace import note
from breezy.plugins.debian.upstream import PackageVersionNotPresent


BRANCH_NAME = 'missing-commits'


class AptSourceError(Exception):
    """An error occured while running 'apt source'."""

    def __init__(self, reason):
        self.reason = reason


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


class NoMissingVersions(Exception):

    def __init__(self, vcs_version, archive_version):
        self.vcs_version = vcs_version
        self.archive_version = archive_version
        super(NoMissingVersions, self).__init__(
            'No missing versions after all. Archive has %s, VCS has %s' % (
                archive_version, vcs_version))


class TreeVersionNotInArchiveChangelog(Exception):

    def __init__(self, tree_version):
        self.tree_version = tree_version
        super(TreeVersionNotInArchiveChangelog, self).__init__(
            'tree version %s does not appear in archive changelog' %
            tree_version)


class TreeUpstreamVersionMissing(Exception):

    def __init__(self, upstream_version):
        self.upstream_version = upstream_version
        super(TreeUpstreamVersionMissing, self).__init__(
            'unable to find upstream version %r' % upstream_version)


def retrieve_source(package_name, target):
    try:
        subprocess.run(
            ['apt', 'source', package_name], cwd=target,
            check=True,
            stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.splitlines()
        if stderr[-1] == (
                b'E: You must put some \'source\' URIs in your '
                b'sources.list'):
            raise NoAptSources()
        CS = b"\x1b[1;31mE: \x1b[0m"
        CE = b"\x1b[0m"
        if stderr[-1] == (
                CS +
                b"You must put some 'deb-src' URIs in your sources.list" +
                CE):
            raise NoAptSources()
        if stderr[-1].startswith(b'E: '):
            raise AptSourceError(stderr[-1][3:].decode())
        if stderr[-1].startswith(CS):
            raise AptSourceError(stderr[-1][len(CS):-len(CE)])
        raise AptSourceError(
            [line.decode('utf-8', 'surrogateescape') for line in stderr])


def import_uncommitted(tree, subpath):
    from breezy.plugins.debian.import_dsc import (
        DistributionBranch,
        DistributionBranchSet,
        )
    cl_path = os.path.join(subpath, 'debian/changelog')
    with tree.get_file(cl_path) as f:
        tree_cl = Changelog(f)
        package_name = tree_cl.package
    with contextlib.ExitStack() as es:
        archive_source = es.enter_context(tempfile.TemporaryDirectory())
        try:
            retrieve_source(package_name, archive_source)
        except AptSourceError as e:
            if isinstance(e.reason, list):
                reason = e.reason[-1]
            else:
                reason = e.reason
            raise ChangerError('apt-source-error', reason)
        except NoAptSources:
            raise ChangerError(
                'no-apt-sources',
                'No sources configured in /etc/apt/sources.list')
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
            raise TreeVersionNotInArchiveChangelog(tree_cl.version)
        if len(missing_versions) == 0:
            raise NoMissingVersions(tree_cl.version, archive_cl.version)
        note('Missing versions: %s', ', '.join(map(str, missing_versions)))
        ret = []
        dbs = DistributionBranchSet()
        db = DistributionBranch(tree.branch, tree.branch, tree=tree)
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
                raise TreeUpstreamVersionMissing(
                    tree_cl.version.upstream_version)
            db.extract_upstream_tree(upstream_tips, upstream_dir)
        no_preparation = not tree.has_filename('.pc/applied-patches')
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
            revision = db.version_as_revisions(version)
            ret.append((tag_name, version, revision))
    return ret


class UncommittedChanger(DebianChanger):

    name = 'import-upload'

    @classmethod
    def setup_parser(cls, parser):
        pass

    @classmethod
    def from_args(cls, args):
        return cls()

    def suggest_branch_name(self):
        return BRANCH_NAME

    def make_changes(self, local_tree, subpath, update_changelog, reporter,
                     committer, base_proposal=None):
        base_revid = local_tree.last_revision()
        try:
            ret = import_uncommitted(local_tree, subpath)
        except TreeUpstreamVersionMissing as e:
            raise ChangerError('tree-upstream-version-missing', str(e))
        except TreeVersionNotInArchiveChangelog as e:
            raise ChangerError(
                'tree-version-not-in-archive-changelog', str(e))
        except NoMissingVersions as e:
            raise ChangerError('nothing-to-do', str(e))
        tags = [(None, tag_name, revid) for (tag_name, version, revid) in ret]
        # TODO(jelmer): Include upstream tags
        proposed_commit_message = "Import missing uploads: %s." % (
            ', '.join([str(v) for t, v in ret]))
        reporter.report_metadata('tags', [
            (tag_name, str(version)) for (tag_name, version, revid) in ret])

        branches = [
            ('main', None, base_revid,
             local_tree.last_revision())]

        # TODO(jelmer): Include branches for upstream/pristine-tar

        return ChangerResult(
            description='Import archive changes missing from the VCS.',
            branches=branches, mutator=ret, tags=tags,
            sufficient_for_proposal=True,
            proposed_commit_message=proposed_commit_message)

    def get_proposal_description(
            self, applied, description_format, existing_proposal):
        return "Import missing uploads: %s." % (
            ', '.join([str(v) for t, v in applied]))

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

    @classmethod
    def describe_command(cls, command):
        return "Import archive changes missing from VCS"


if __name__ == '__main__':
    import sys
    sys.exit(run_mutator(UncommittedChanger))
