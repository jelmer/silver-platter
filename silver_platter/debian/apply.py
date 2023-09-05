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

import json
import logging
import os
import subprocess
import sys
import tempfile
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, BinaryIO, Dict, List, Optional, Tuple, Union

from breezy.commit import PointlessCommit
from breezy.revision import RevisionID
from breezy.workingtree import WorkingTree
from breezy.workspace import check_clean_tree, reset_tree
from debian.changelog import Changelog
from debian.deb822 import Deb822

from ..apply import (
    ResultFileFormatError,
    ScriptFailed,
    ScriptMadeNoChanges,
    ScriptNotFound,
)
from . import (
    DEFAULT_BUILDER,
    BuildFailedError,
    MissingUpstreamTarball,
    _get_maintainer_from_env,
    add_changelog_entry,
    build,
    control_files_in_root,
    guess_update_changelog,
)


class MissingChangelog(Exception):
    """No changelog file is present."""


class DetailedFailure(Exception):
    """Detailed failure."""

    def __init__(self, source_name, result_code, description, stage=None,
                 details=None) -> None:
        self.source = source_name
        self.result_code = result_code
        self.description = description
        self.details = details
        self.stage = stage

    @classmethod
    def from_json(cls, source_name, json):
        return cls(
            source_name,
            result_code=json.get('result_code'),
            description=json.get('description'),
            stage=tuple(json['stage']) if json.get('stage') else None,
            details=json.get('details'))


@dataclass
class CommandResult:

    source: Optional[str]
    description: Optional[str] = None
    value: Optional[int] = None
    serialized_context: Optional[str] = None
    context: Dict[str, str] = field(default_factory=dict)
    tags: List[Tuple[str, RevisionID]] = field(default_factory=list)
    old_revision: Optional[RevisionID] = None
    new_revision: Optional[RevisionID] = None
    target_branch_url: Optional[str] = None

    @classmethod
    def from_json(cls, source, data):
        if 'tags' in data:
            tags = []
            for name, revid in data['tags']:
                tags.append((name, revid.encode('utf-8')))
        else:
            tags = None
        return cls(
            source=source,
            value=data.get('value', None),
            context=data.get('context', {}),
            serialized_context=data.get('serialized_context', None),
            description=data.get('description'),
            target_branch_url=data.get('target-branch-url', None),
            tags=tags)


def install_built_package(local_tree, subpath, build_target_dir):
    import re
    import subprocess
    abspath = local_tree.abspath(os.path.join(subpath, 'debian/changelog'))
    with open(abspath) as f:
        cl = Changelog(f)
    non_epoch_version = cl[0].version.upstream_version
    if cl[0].version.debian_version is not None:
        non_epoch_version += "-%s" % cl[0].version.debian_version
    c = re.compile(
        '{}_{}_(.*).changes'.format(
            re.escape(cl[0].package),
            re.escape(non_epoch_version)))  # type: ignore
    for entry in os.scandir(build_target_dir):
        if not c.match(entry.name):
            continue
        with open(entry.path, 'rb') as g:
            changes = Deb822(g)
            if changes.get('Binary'):
                subprocess.check_call(['debi', entry.path])


def script_runner(   # noqa: C901
    local_tree: WorkingTree, script: Union[str, List[str]],
    commit_pending: Optional[bool] = None,
    resume_metadata: Optional[Any] = None,
    subpath: str = '', update_changelog: Optional[bool] = None,
    extra_env: Optional[Dict[str, str]] = None,
    committer: Optional[str] = None,
    stderr: Optional[BinaryIO] = None
) -> CommandResult:  # noqa: C901
    """Run a script in a tree and commit the result.

    This ignores newly added files.

    Args:
      local_tree: Local tree to run script in
      script: Script to run
      commit_pending: Whether to commit pending changes
        (True, False or None: only commit if there were no commits by the
         script)
    """
    if control_files_in_root(local_tree, subpath):
        debian_path = subpath
    else:
        debian_path = os.path.join(subpath, "debian")
    if update_changelog is None:
        dch_guess = guess_update_changelog(local_tree, debian_path)
        if dch_guess:
            if isinstance(dch_guess, tuple):  # lintian-brush < 1.22
                update_changelog, explanation = dch_guess
            else:
                update_changelog = dch_guess.update_changelog
                explanation = dch_guess.explanation
            logging.info('%s', explanation)
        else:
            # Assume yes.
            update_changelog = True

    cl_path = os.path.join(debian_path, 'changelog')
    try:
        with open(local_tree.abspath(cl_path)) as f:
            cl = Changelog(f)
            source_name = cl[0].package
    except FileNotFoundError:
        source_name = None

    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)

    env['SVP_API'] = '1'
    if source_name:
        env['DEB_SOURCE'] = source_name

    if update_changelog:
        env['DEB_UPDATE_CHANGELOG'] = 'update'
    else:
        env['DEB_UPDATE_CHANGELOG'] = 'leave'

    last_revision = local_tree.last_revision()
    orig_tags = local_tree.branch.tags.get_tag_dict()
    with tempfile.TemporaryDirectory() as td:
        env['SVP_RESULT'] = os.path.join(td, 'result.json')
        if resume_metadata:
            env['SVP_RESUME'] = os.path.join(td, 'resume-metadata.json')
            with open(env['SVP_RESUME'], 'w') as f:
                json.dump(resume_metadata, f)
        try:
            p = subprocess.Popen(
                script, cwd=local_tree.abspath(subpath),
                stdout=subprocess.PIPE,
                shell=isinstance(script, str), env=env,
                stderr=stderr)
        except FileNotFoundError as e:
            raise ScriptNotFound(script) from e
        (description_encoded, err) = p.communicate(b"")
        try:
            with open(env['SVP_RESULT']) as f:
                try:
                    result_json = json.load(f)
                except json.decoder.JSONDecodeError as e:
                    raise ResultFileFormatError(e)
        except FileNotFoundError:
            result_json = None
        if p.returncode != 0:
            if result_json is not None:
                raise DetailedFailure.from_json(source_name, result_json)
            raise ScriptFailed(script, p.returncode)
        # If the changelog didn't exist earlier, then hopefully it was created
        # now.
        if source_name is None:
            try:
                with open(local_tree.abspath(cl_path)) as f:
                    cl = Changelog(f)
                    source_name = cl[0].package
            except FileNotFoundError:
                raise MissingChangelog(cl_path)
        if result_json is not None:
            result = CommandResult.from_json(source_name, result_json)
        else:
            result = CommandResult(source=source_name)
    if not result.description:
        result.description = description_encoded.decode().replace("\r", "")
    new_revision = local_tree.last_revision()
    if result.tags is None:
        result.tags = []
        for name, revid in local_tree.branch.tags.get_tag_dict().items():
            if orig_tags.get(name) != revid:
                result.tags.append((name, revid))
    if last_revision == new_revision and commit_pending is None:
        # Automatically commit pending changes if the script did not
        # touch the branch.
        commit_pending = True
    if commit_pending:
        if (update_changelog
                and result.description
                and local_tree.has_changes()):
            add_changelog_entry(
                local_tree,
                os.path.join(debian_path, 'changelog'),
                [result.description],
                maintainer=_get_maintainer_from_env(extra_env))
        local_tree.smart_add([local_tree.abspath(subpath)])
        with suppress(PointlessCommit):
            new_revision = local_tree.commit(
                result.description, allow_pointless=False,
                committer=committer)
    if new_revision == last_revision:
        raise ScriptMadeNoChanges()
    result.old_revision = last_revision
    result.new_revision = new_revision
    return result


def main(argv: List[str]) -> Optional[int]:  # noqa: C901
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--command", help="Path to script to run.", type=str)
    parser.add_argument(
        "--diff", action="store_true", help="Show diff of generated changes."
    )
    parser.add_argument(
        "--no-update-changelog",
        action="store_false",
        default=None,
        dest="update_changelog",
        help="do not update the changelog",
    )
    parser.add_argument(
        "--update-changelog",
        action="store_true",
        dest="update_changelog",
        help="force updating of the changelog",
        default=None,
    )

    parser.add_argument(
        "--commit-pending",
        help="Commit pending changes after script.",
        choices=["yes", "no", "auto"],
        default=None,
        type=str,
    )
    parser.add_argument(
        "--build-verify",
        help="Build package to verify it.",
        dest="build_verify",
        action="store_true",
    )
    parser.add_argument(
        "--builder",
        default=DEFAULT_BUILDER,
        type=str,
        help="Build command to use when verifying build.",
    )
    parser.add_argument(
        "--build-target-dir",
        type=str,
        help=(
            "Store built Debian files in specified directory "
            "(with --build-verify)"
        ),
    )
    parser.add_argument(
        "--install", "-i",
        action="store_true",
        help="Install built package (implies --build-verify)")
    parser.add_argument(
        "--dump-context", action="store_true",
        help="Report context on success")

    parser.add_argument(
        "--recipe", type=str, help="Recipe to use.")
    args = parser.parse_args(argv)

    if args.recipe:
        from ..recipe import Recipe
        recipe = Recipe.from_path(args.recipe)
    else:
        recipe = None

    if args.commit_pending:
        commit_pending = {
            "auto": None, "yes": True, "no": False}[args.commit_pending]
    elif recipe:
        commit_pending = recipe.commit_pending
    else:
        commit_pending = None

    if args.command:
        command = args.command
    elif recipe and recipe.command:
        command = recipe.command
    else:
        parser.error('No command or recipe specified.')

    local_tree, subpath = WorkingTree.open_containing('.')

    check_clean_tree(local_tree)

    try:
        try:
            result = script_runner(
                local_tree, script=command, commit_pending=commit_pending,
                subpath=subpath, update_changelog=args.update_changelog)
        except MissingChangelog as e:
            logging.error('No debian changelog file (%s) present', e.args[0])
            return False
        except ScriptMadeNoChanges:
            logging.info('Script made no changes')
            return False

        if result.description:
            logging.info('Succeeded: %s', result.description)

        if args.build_verify or args.install:
            try:
                build(local_tree, subpath, builder=args.builder,
                      result_dir=args.build_target_dir)
            except BuildFailedError:
                logging.error("%s: build failed", result.source)
                return False
            except MissingUpstreamTarball:
                logging.error(
                    "%s: unable to find upstream source", result.source)
                return False
    except Exception:
        reset_tree(local_tree, subpath=subpath)
        raise

    if args.install:
        install_built_package(local_tree, subpath, args.build_target_dir)

    if args.diff:
        from breezy.diff import show_diff_trees
        old_tree = local_tree.revision_tree(result.old_revision)
        new_tree = local_tree.revision_tree(result.new_revision)
        show_diff_trees(
            old_tree,
            new_tree,
            sys.stdout.buffer,
            old_label='old/',
            new_label='new/')

    if args.dump_context:
        json.dump(result.context, sys.stdout, indent=5)
    return 0
