#!/usr/bin/python
# Copyright (C) 2019 Jelmer Vernooij <jelmer@jelmer.uk>
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

__all__ = ["iter_conflicted"]

import argparse
from functools import partial
import logging
import os
import sys
from typing import Any, List, Optional, Dict, Iterable, Tuple, Type

import pkg_resources

from breezy import version_info as breezy_version_info
from breezy.branch import Branch
from breezy.propose import Hoster, MergeProposal
from breezy.transport import Transport
from breezy.workingtree import WorkingTree

from . import (
    control_files_in_root,
    open_packaging_branch,
    guess_update_changelog,
    NoSuchPackage,
    NoAptSources,
    DEFAULT_BUILDER,
)
from ..proposal import (
    HosterLoginRequired,
    UnsupportedHoster,
    NoSuchProject,
    enable_tag_pushing,
    find_existing_proposed,
    get_hoster,
    iter_conflicted,
)

from ..publish import (
    PublishResult,
    SUPPORTED_MODES,
    InsufficientChangesForNewProposal,
)
from ..utils import (
    BranchMissing,
    BranchUnavailable,
    BranchUnsupported,
    run_pre_check,
    run_post_check,
    PostCheckFailed,
    full_branch_url,
)


class ChangerReporter(object):
    def report_context(self, context):
        raise NotImplementedError(self.report_context)

    def report_metadata(self, key, value):
        raise NotImplementedError(self.report_metadata)

    def get_base_metadata(self, key, default_value=None):
        raise NotImplementedError(self.get_base_metadata)


class ChangerError(Exception):
    def __init__(
            self, category: str, summary: str, original: Optional[Exception] = None, details: Any = None
    ):
        self.category = category
        self.summary = summary
        self.original = original
        self.details = details


class ChangerResult(object):
    def __init__(
        self,
        description: Optional[str],
        mutator: Any,
        branches: Optional[List[Tuple[str, str, bytes, bytes]]] = [],
        tags: Optional[Dict[str, bytes]] = None,
        value: Optional[int] = None,
        proposed_commit_message: Optional[str] = None,
        title: Optional[str] = None,
        labels: Optional[List[str]] = None,
        sufficient_for_proposal: bool = True,
    ):
        self.description = description
        self.mutator = mutator
        self.branches = branches or []
        self.tags = tags or {}
        self.value = value
        self.proposed_commit_message = proposed_commit_message
        self.title = title
        self.labels = labels
        self.sufficient_for_proposal = sufficient_for_proposal

    def show_diff(
        self,
        repository,
        outf,
        role="main",
        old_label: str = "old/",
        new_label: str = "new/",
    ) -> None:
        from breezy.diff import show_diff_trees

        for (brole, name, base_revision, revision) in self.branches:
            if role == brole:
                break
        else:
            raise KeyError
        old_tree = repository.revision_tree(base_revision)
        new_tree = repository.revision_tree(revision)
        show_diff_trees(
            old_tree, new_tree, outf, old_label=old_label, new_label=new_label
        )


def get_package(
    package: str,
    branch_name: str,
    overwrite_unrelated: bool = False,
    refresh: bool = False,
    possible_transports: Optional[List[Transport]] = None,
    possible_hosters: Optional[List[Hoster]] = None,
    owner: Optional[str] = None,
) -> Tuple[
    str,
    Branch,
    str,
    Optional[Branch],
    Optional[Hoster],
    Optional[MergeProposal],
    Optional[bool],
]:
    main_branch, subpath = open_packaging_branch(
        package, possible_transports=possible_transports
    )

    overwrite: Optional[bool] = False

    try:
        hoster = get_hoster(main_branch, possible_hosters=possible_hosters)
    except UnsupportedHoster:
        # We can't figure out what branch to resume from when there's no
        # hoster that can tell us.
        resume_branch = None
        existing_proposal = None
        hoster = None
    else:
        (resume_branch, overwrite, existing_proposal) = find_existing_proposed(
            main_branch,
            hoster,
            branch_name,
            owner=owner,
            overwrite_unrelated=overwrite_unrelated,
        )
    if refresh:
        overwrite = True
        resume_branch = None

    return (
        package,
        main_branch,
        subpath,
        resume_branch,
        hoster,
        existing_proposal,
        overwrite,
    )


def iter_packages(
    packages: Iterable[str],
    branch_name: str,
    overwrite_unrelated: bool = False,
    refresh: bool = False,
    derived_owner: Optional[str] = None,
):
    """Iterate over relevant branches for a set of packages.

    Args:
      packages: Iterable over package names (or packaging URLs)
      branch_name: Branch name to look for
      overwrite_unrelated: Allow overwriting unrelated changes
      refresh: Whether to refresh existing merge proposals
    Returns:
      iterator over
        (package name, main branch object, subpath, branch to resume (if any),
         hoster (None if the hoster is not supported),
         existing_proposal, whether to overwrite the branch)
    """
    possible_transports: List[Transport] = []
    possible_hosters: List[Hoster] = []

    for pkg in packages:
        logging.info("Processing: %s", pkg)

        (
            pkg,
            main_branch,
            subpath,
            resume_branch,
            hoster,
            existing_proposal,
            overwrite,
        ) = get_package(
            pkg,
            branch_name,
            overwrite_unrelated=overwrite_unrelated,
            refresh=refresh,
            possible_transports=possible_transports,
            possible_hosters=possible_hosters,
            owner=derived_owner,
        )

        yield (
            pkg,
            main_branch,
            subpath,
            resume_branch,
            hoster,
            existing_proposal,
            overwrite,
        )


def setup_parser_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--dry-run",
        help="Create branches but don't push or propose anything.",
        action="store_true",
        default=False,
    )
    parser.add_argument(
        "--build-verify",
        help="Build package to verify it.",
        dest="build_verify",
        action="store_true",
    )
    parser.add_argument(
        "--pre-check",
        help="Command to run to check whether to process package.",
        type=str,
    )
    parser.add_argument(
        "--post-check", help="Command to run to check package before pushing.", type=str
    )
    parser.add_argument(
        "--builder",
        default=DEFAULT_BUILDER,
        type=str,
        help="Build command to use when verifying build.",
    )
    parser.add_argument(
        "--refresh",
        help="Discard old branch and apply fixers from scratch.",
        action="store_true",
    )
    parser.add_argument("--committer", help="Committer identity", type=str)
    parser.add_argument(
        "--mode",
        help="Mode for pushing",
        choices=SUPPORTED_MODES,
        default="propose",
        type=str,
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
        "--diff", action="store_true", help="Output diff of created merge proposal."
    )
    parser.add_argument(
        "--build-target-dir",
        type=str,
        help=(
            "Store built Debian files in specified directory " "(with --build-verify)"
        ),
    )
    parser.add_argument(
        "--install", "-i",
        action="store_true",
        help="Install built package (implies --build-verify)")
    parser.add_argument(
        "--overwrite", action="store_true", help="Overwrite existing branches."
    )
    parser.add_argument("--name", type=str, help="Proposed branch name", default=None)
    parser.add_argument(
        "--derived-owner", type=str, default=None, help="Owner for derived branches."
    )
    parser.add_argument(
        "--label", type=str, help="Label to attach", action="append", default=[]
    )
    parser.add_argument(
        "--preserve-repositories", action="store_true",
        help="Preserve temporary repositories.")


class DebianChanger(object):
    """A class which can make and explain changes to a Debian package in VCS."""

    name: str

    @classmethod
    def setup_parser(cls, parser: argparse.ArgumentParser) -> None:
        raise NotImplementedError(cls.setup_parser)

    @classmethod
    def from_args(cls, args: List[str]) -> "DebianChanger":
        raise NotImplementedError(cls.from_args)

    def suggest_branch_name(self) -> str:
        raise NotImplementedError(self.suggest_branch_name)

    def make_changes(
        self,
        local_tree: WorkingTree,
        subpath: str,
        update_changelog: bool,
        reporter: ChangerReporter,
        committer: Optional[str],
        base_proposal: Optional[MergeProposal] = None,
    ) -> ChangerResult:
        raise NotImplementedError(self.make_changes)

    def get_proposal_description(
        self, applied: Any, description_format: str, existing_proposal: MergeProposal
    ) -> str:
        raise NotImplementedError(self.get_proposal_description)

    def describe(self, applied: Any, publish_result: PublishResult) -> None:
        raise NotImplementedError(self.describe)

    @classmethod
    def describe_command(cls, command):
        return cls.name


class DummyChangerReporter(ChangerReporter):
    def report_context(self, context):
        pass

    def report_metadata(self, key, value):
        pass

    def get_base_metadata(self, key, default_value=None):
        return None


def _run_single_changer(  # noqa: C901
    changer: DebianChanger,
    pkg: str,
    main_branch: Branch,
    subpath: str,
    resume_branch: Optional[Branch],
    hoster: Optional[Hoster],
    existing_proposal: Optional[MergeProposal],
    overwrite: Optional[bool],
    mode: str,
    branch_name: str,
    diff: bool = False,
    committer: Optional[str] = None,
    build_verify: bool = False,
    preserve_repositories: bool = False,
    install: bool = False,
    pre_check: Optional[str] = None,
    post_check: Optional[str] = None,
    builder: str = DEFAULT_BUILDER,
    dry_run: bool = False,
    update_changelog: Optional[bool] = None,
    label: Optional[List[str]] = None,
    derived_owner: Optional[str] = None,
    build_target_dir: Optional[str] = None,
) -> Optional[bool]:
    from breezy import errors
    from . import (
        BuildFailedError,
        MissingUpstreamTarball,
        Workspace,
    )

    if hoster is None and mode == "attempt-push":
        logging.warn(
            "Unsupported hoster; will attempt to push to %s",
            full_branch_url(main_branch),
        )
        mode = "push"
    with Workspace(
        main_branch, resume_branch=resume_branch
    ) as ws, ws.local_tree.lock_write():
        if ws.refreshed:
            overwrite = True
        run_pre_check(ws.local_tree, pre_check)
        if control_files_in_root(ws.local_tree, subpath):
            debian_path = subpath
        else:
            debian_path = os.path.join(subpath, "debian")
        if update_changelog is None:
            dch_guess = guess_update_changelog(ws.local_tree, debian_path)
            if dch_guess:
                logging.info('%s', dch_guess[1])
                update_changelog = dch_guess[0]
            else:
                # Assume yes.
                update_changelog = True
        try:
            changer_result = changer.make_changes(
                ws.local_tree,
                subpath=subpath,
                update_changelog=update_changelog,
                committer=committer,
                reporter=DummyChangerReporter(),
            )
        except ChangerError as e:
            logging.error('%s: %s', e.category, e.summary)
            return False

        if not ws.changes_since_main():
            if existing_proposal:
                logging.info("%s: nothing left to do. Closing proposal.", pkg)
                existing_proposal.close()
            else:
                logging.info("%s: nothing to do", pkg)
            return None

        try:
            run_post_check(ws.local_tree, post_check, ws.orig_revid)
        except PostCheckFailed as e:
            logging.info("%s: %s", pkg, e)
            return False
        if build_verify or install:
            try:
                ws.build(builder=builder, result_dir=build_target_dir)
            except BuildFailedError:
                logging.info("%s: build failed", pkg)
                return False
            except MissingUpstreamTarball:
                logging.info("%s: unable to find upstream source", pkg)
                return False

        if install:
            from .apply import install_built_package
            install_built_package(ws.local_tree, ws.subpath, build_target_dir)

        enable_tag_pushing(ws.local_tree.branch)

        kwargs: Dict[str, Any] = {}
        if breezy_version_info >= (3, 1):
            kwargs["tags"] = changer_result.tags

        try:
            publish_result = ws.publish_changes(
                mode,
                branch_name,
                get_proposal_description=partial(
                    changer.get_proposal_description, changer_result.mutator
                ),
                get_proposal_commit_message=(
                    lambda oldmp: changer_result.proposed_commit_message
                ),
                dry_run=dry_run,
                hoster=hoster,
                allow_create_proposal=changer_result.sufficient_for_proposal,
                overwrite_existing=overwrite,
                existing_proposal=existing_proposal,
                derived_owner=derived_owner,
                labels=label,
                **kwargs
            )
        except UnsupportedHoster as e:
            logging.error(
                "%s: No known supported hoster for %s. Run 'svp login'?",
                pkg,
                full_branch_url(e.branch),
            )
            return False
        except NoSuchProject as e:
            logging.info("%s: project %s was not found", pkg, e.project)
            return False
        except errors.PermissionDenied as e:
            logging.info("%s: %s", pkg, e)
            return False
        except errors.DivergedBranches:
            logging.info("%s: a branch exists. Use --overwrite to discard it.", pkg)
            return False
        except InsufficientChangesForNewProposal:
            logging.info('%s: insufficient changes for a new merge proposal',
                         pkg)
            return False
        except HosterLoginRequired as e:
            logging.error(
                "Credentials for hosting site at %r missing. " "Run 'svp login'?",
                e.hoster.base_url,
            )
            return False

        if publish_result.proposal:
            changer.describe(changer_result.mutator, publish_result)
        if diff:
            for branch_entry in changer_result.branches:
                role = branch_entry[0]
                if len(changer_result.branches) > 1:
                    sys.stdout.write("%s\n" % role)
                    sys.stdout.write(("-" * len(role)) + "\n")
                sys.stdout.flush()
                changer_result.show_diff(
                    ws.local_tree.branch.repository, sys.stdout.buffer, role=role
                )
                if len(changer_result.branches) > 1:
                    sys.stdout.write("\n")
        if preserve_repositories:
            ws.defer_destroy()
            logging.info('Workspace preserved in %s', ws.local_tree.abspath(ws.subpath))

        return True


def run_single_changer(changer: DebianChanger, args: argparse.Namespace) -> int:
    import silver_platter  # noqa: F401

    if args.name:
        branch_name = args.name
    else:
        branch_name = changer.suggest_branch_name()

    try:
        (
            pkg,
            main_branch,
            subpath,
            resume_branch,
            hoster,
            existing_proposal,
            overwrite,
        ) = get_package(
            args.package,
            branch_name,
            overwrite_unrelated=args.overwrite,
            refresh=args.refresh,
            owner=args.derived_owner,
        )
    except NoSuchPackage:
        logging.info("%s: no such package", args.package)
        return 1
    except NoSuchProject as e:
        logging.info("%s: unable to find project: %s", args.package, e.project)
        return 1
    except (BranchMissing, BranchUnavailable, BranchUnsupported) as e:
        logging.info("%s: ignoring: %s", args.package, e)
        return 1
    except NoAptSources:
        logging.info(
            "%s: no apt sources configured, unable to get package metadata.",
            args.package,
        )
        return 1

    if (
        _run_single_changer(
            changer,
            pkg,
            main_branch,
            subpath,
            resume_branch,
            hoster,
            existing_proposal,
            overwrite,
            args.mode,
            branch_name,
            diff=args.diff,
            committer=args.committer,
            build_verify=args.build_verify,
            preserve_repositories=args.preserve_repositories,
            install=args.install,
            pre_check=args.pre_check,
            builder=args.builder,
            post_check=args.post_check,
            dry_run=args.dry_run,
            update_changelog=args.update_changelog,
            label=args.label,
            derived_owner=args.derived_owner,
            build_target_dir=args.build_target_dir,
        )
        is False
    ):
        return 1
    else:
        return 0


BUILTIN_ENTRYPOINTS = [
    pkg_resources.EntryPoint(
        "new-upstream", "silver_platter.debian.upstream", attrs=("NewUpstreamChanger",)
    ),
    pkg_resources.EntryPoint(
        "orphan", "silver_platter.debian.orphan", attrs=("OrphanChanger",)
    ),
    pkg_resources.EntryPoint(
        "debianize", "silver_platter.debian.debianize", attrs=("DebianizeChanger",)
    ),
]


def changer_subcommands() -> List[str]:
    endpoints = pkg_resources.iter_entry_points(__name__)
    ret = []
    for ep in BUILTIN_ENTRYPOINTS + list(endpoints):
        ret.append(ep.name)
    return ret


def changer_subcommand(name: str) -> Type[DebianChanger]:
    for ep in BUILTIN_ENTRYPOINTS:
        if ep.name == name:
            return ep.resolve()
    endpoints = pkg_resources.iter_entry_points(__name__, name)
    for ep in endpoints:
        return ep.load()
    raise KeyError(name)


def run_mutator(changer_cls, argv=None):
    import json
    import argparse
    import os
    from breezy.workingtree import WorkingTree

    parser = argparse.ArgumentParser()
    changer_cls.setup_parser(parser)
    args = parser.parse_args(argv)
    wt, subpath = WorkingTree.open_containing(".")
    changer = changer_cls.from_args(args)
    try:
        update_changelog_str = (os.environ["UPDATE_CHANGELOG"],)
    except KeyError:
        update_changelog = None
    else:
        if update_changelog_str == "leave_changelog":
            update_changelog = False
        elif update_changelog_str == "update_changelog":
            update_changelog = True
        else:
            # TODO(jelmer): Warn
            update_changelog = None

    try:
        base_metadata_path = os.environ["BASE_METADATA"]
    except KeyError:
        existing_proposal = None
    else:
        with open(base_metadata_path, "r") as f:
            base_metadata = json.load(f)

        class PreviousProposal(MergeProposal):
            def __init__(self, metadata):
                self.metadata = metadata

            def get_description(self):
                return self.metadata.get("description")

            def get_commit_message(self):
                return self.metadata.get("commit-message")

        existing_proposal = PreviousProposal(base_metadata["merge-proposal"])

    mutator_metadata = {}
    try:
        result = changer.make_changes(
            wt,
            subpath,
            update_changelog=update_changelog,
            committer=os.environ.get("COMMITTER"),
            base_proposal=existing_proposal,
            reporter=DummyChangerReporter(),
        )
    except ChangerError as e:
        result_json = {
            "result-code": e.category,
            "description": e.summary,
            "details": e.details,
        }
    else:
        result_json = {
            "result-code": None,
            "description": result.description,
            "suggested-branch-name": changer.suggest_branch_name(),
            "tags": result.tags,
            "branches": result.branches,
            "value": result.value,
            "mutator": mutator_metadata,
            "merge-proposal": {
                "sufficient": changer.sufficient_for_proposal,
                "commit-message": result.proposed_commit_message,
                "title": result.title,
                "labels": result.labels,
                "description-plain": changer.get_proposal_description(
                    result.mutator, "plain", existing_proposal
                ),
                "description-markdown": changer.get_proposal_description(
                    result.mutator, "markdown", existing_proposal
                ),
            },
        }
    json.dump(result_json, sys.stdout, indent=4)
    return 0
