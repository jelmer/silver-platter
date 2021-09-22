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

from contextlib import ExitStack
from functools import partial
import logging
import os
from typing import Any, List, Optional, Tuple, Dict
from urllib.parse import urlparse

from breezy import errors, osutils
from breezy import version_info as breezy_version_info
from breezy.branch import Branch
from breezy.propose import Hoster, MergeProposal, UnsupportedHoster, get_hoster, HosterLoginRequired
from breezy.transport import Transport

from debmutate.control import ControlEditor
from debmutate.deb822 import ChangeConflict
from debmutate.reformatting import GeneratedFile, FormattingUnpreservable


from . import (
    control_files_in_root,
    guess_update_changelog,
    pick_additional_colocated_branches,
    open_packaging_branch,
    connect_udd_mirror,
    add_changelog_entry,
    NoVcsInformation,
    NoAptSources,
    NoSuchPackage,
    DEFAULT_BUILDER,
    BuildFailedError,
    MissingUpstreamTarball,
    Workspace,
)
from .changer import (
    ChangerError,
    ChangerResult,
)
from ..proposal import (
    push_changes,
    find_existing_proposed,
    NoSuchProject,
    enable_tag_pushing,
    )
from ..publish import (
    InsufficientChangesForNewProposal,
    SUPPORTED_MODES,
)
from ..utils import (
    BranchMissing,
    BranchUnavailable,
    BranchUnsupported,
    full_branch_url,
    run_pre_check,
    run_post_check,
    PostCheckFailed,
    )


BRANCH_NAME = "orphan"
QA_MAINTAINER = "Debian QA Group <packages@qa.debian.org>"


def push_to_salsa(local_tree, orig_branch, user, name, dry_run=False):
    from breezy import urlutils
    from breezy.branch import Branch
    from breezy.errors import PermissionDenied, AlreadyControlDirError
    from breezy.plugins.gitlab.hoster import GitLab

    if dry_run:
        logging.info("Creating and pushing to salsa project %s/%s", user, name)
        return

    try:
        salsa = GitLab.probe_from_url("https://salsa.debian.org/")
    except HosterLoginRequired:
        logging.warning("No login for salsa known, not pushing branch.")
        return

    try:
        orig_hoster = get_hoster(orig_branch)
    except UnsupportedHoster:
        logging.debug("Original branch %r not hosted on salsa.")
        from_project = None
    else:
        if orig_hoster == salsa:
            from_project = urlutils.URL.from_string(orig_branch.controldir.user_url).path
        else:
            from_project = None

    if from_project is not None:
        salsa.fork_project(from_project, owner=user)
    else:
        to_project = "%s/%s" % (user, name)
        try:
            salsa.create_project(to_project)
        except PermissionDenied as e:
            logging.info('No permission to create new project %s under %s: %s',
                         name, user, e)
            return
        except AlreadyControlDirError:
            logging.info('Project %s already exists, using..', to_project)
    target_branch = Branch.open(
        "git+ssh://git@salsa.debian.org/%s/%s.git" % (user, name)
    )
    additional_colocated_branches = pick_additional_colocated_branches(
        local_tree.branch
    )
    return push_changes(
        local_tree.branch,
        target_branch,
        hoster=salsa,
        additional_colocated_branches=additional_colocated_branches,
        dry_run=dry_run,
    )


class OrphanResult(object):
    def __init__(
        self,
        package=None,
        old_vcs_url=None,
        new_vcs_url=None,
        salsa_user=None,
        wnpp_bug=None,
    ):
        self.package = package
        self.old_vcs_url = old_vcs_url
        self.new_vcs_url = new_vcs_url
        self.pushed = False
        self.salsa_user = salsa_user
        self.wnpp_bug = wnpp_bug

    def json(self):
        return {
            "package": self.package,
            "old_vcs_url": self.old_vcs_url,
            "new_vcs_url": self.new_vcs_url,
            "pushed": self.pushed,
            "salsa_user": self.salsa_user,
            "wnpp_bug": self.wnpp_bug,
        }


def find_wnpp_bug(source):
    conn = connect_udd_mirror()
    cursor = conn.cursor()
    cursor.execute("select id from wnpp where type = 'O' and source = %s", (source,))
    entry = cursor.fetchone()
    if entry is None:
        raise KeyError
    return entry[0]


def set_vcs_fields_to_salsa_user(control, salsa_user):
    old_vcs_url = control.source.get("Vcs-Git")
    control.source["Vcs-Git"] = "https://salsa.debian.org/%s/%s.git" % (
        salsa_user,
        control.source['Source']
    )
    new_vcs_url = control.source["Vcs-Git"]
    control.source["Vcs-Browser"] = "https://salsa.debian.org/%s/%s" % (
        salsa_user,
        control.source['Source']
    )
    return (old_vcs_url, new_vcs_url)


def set_maintainer_to_qa_team(control):
    if (QA_MAINTAINER == control.source.get('Maintainer') and
            'Uploaders' not in control.source):
        return False
    control.source["Maintainer"] = QA_MAINTAINER
    try:
        del control.source["Uploaders"]
    except KeyError:
        pass
    return True


def setup_parser(parser):
    parser.add_argument(
        "--no-update-vcs",
        action="store_true",
        help="Do not move the VCS repository to the Debian team on Salsa.",
    )
    parser.add_argument(
        "--salsa-user",
        type=str,
        default="debian",
        help="Salsa user to push repository to.",
    )
    parser.add_argument(
        "--just-update-headers",
        action="store_true",
        help="Update the VCS-* headers, but don't actually "
        "clone the repository.",
    )
    parser.add_argument(
        "--no-check-wnpp", action="store_true", help="Do not check for WNPP bug."
    )


class OrphanChanger:

    def __init__(
        self,
        update_vcs=True,
        salsa_push=True,
        salsa_user="debian",
        dry_run=False,
        check_wnpp=True,
    ):
        self.update_vcs = update_vcs
        self.salsa_push = salsa_push
        self.salsa_user = salsa_user
        self.dry_run = dry_run
        self.check_wnpp = check_wnpp

    @classmethod
    def from_args(cls, args):
        return cls(
            update_vcs=not args.no_update_vcs,
            dry_run=args.dry_run,
            salsa_user=args.salsa_user,
            salsa_push=not args.just_update_headers,
            check_wnpp=not args.no_check_wnpp,
        )

    def make_changes(  # noqa: C901
        self,
        local_tree,
        subpath,
        update_changelog,
        reporter,
        committer,
        base_proposal=None,
    ):
        try:
            result = orphan(
                local_tree,
                subpath,
                update_changelog,
                committer,
                update_vcs=self.update_vcs,
                salsa_push=self.salsa_push,
                salsa_user=self.salsa_user,
                dry_run=self.dry_run,
                check_wnpp=self.check_wnpp
                )
        except AlreadyOrphaned:
            raise ChangerError("nothing-to-do", "Already orphaned")
        except NoWnppBug as e:
            raise ChangerError(
                "nothing-to-do",
                "Package %s is purported to be orphaned, "
                "but no open wnpp bug exists." % e.package,
            )
        except FormattingUnpreservable as e:
            raise ChangerError(
                "formatting-unpreservable",
                "unable to preserve formatting while editing %s" % e.path,
            )
        except (ChangeConflict, GeneratedFile) as e:
            raise ChangerError(
                "generated-file", "unable to edit generated file: %r" % e
            )

        reporter.report_metadata("old_vcs_url", result.old_vcs_url)
        reporter.report_metadata("new_vcs_url", result.new_vcs_url)
        reporter.report_metadata("pushed", result.pushed)
        reporter.report_metadata("wnpp_bug", result.wnpp_bug)

        tags = []

        return ChangerResult(
            description="Move package to QA team.",
            mutator=result.json(),
            tags=tags,
            sufficient_for_proposal=True,
            proposed_commit_message=("Set the package maintainer to the QA team."),
        )


class NoWnppBug(Exception):
    """No wnpp bug exists."""

    def __init__(self, package):
        self.package = package


class AlreadyOrphaned(Exception):
    """Package is already orphaned."""


def orphan(
        local_tree, subpath, update_changelog, committer, update_vcs=True,
        salsa_push=True, salsa_user="debian", dry_run=False, check_wnpp=True) -> OrphanResult:
    control_path = local_tree.abspath(osutils.pathjoin(subpath, "debian/control"))
    changelog_entries = []
    with ExitStack() as es:
        control = es.enter_context(ControlEditor(path=control_path))
        if check_wnpp:
            try:
                wnpp_bug = find_wnpp_bug(control.source["Source"])
            except KeyError:
                raise NoWnppBug(control.source['Source'])
        else:
            wnpp_bug = None
        if set_maintainer_to_qa_team(control):
            if wnpp_bug is not None:
                changelog_entries.append("Orphan package - see bug %d." % wnpp_bug)
            else:
                changelog_entries.append("Orphan package.")
        result = OrphanResult(wnpp_bug=wnpp_bug, package=control.source["Source"])

        if update_vcs:
            (result.old_vcs_url, result.new_vcs_url) = set_vcs_fields_to_salsa_user(
                control, salsa_user)
            result.salsa_user = salsa_user
            if result.old_vcs_url == result.new_vcs_url:
                result.old_vcs_url = result.new_vcs_url = None
            else:
                changelog_entries.append(
                    "Update VCS URLs to point to Debian group."
                )
    if not changelog_entries:
        raise AlreadyOrphaned()
    if update_changelog in (True, None):
        add_changelog_entry(
            local_tree,
            osutils.pathjoin(subpath, "debian/changelog"),
            ["QA Upload."] + changelog_entries,
        )
    local_tree.commit(
        "Move package to QA team.", committer=committer, allow_pointless=False
    )

    result.pushed = False
    if update_vcs and salsa_push and result.new_vcs_url:
        parent_branch_url = local_tree.branch.get_parent()
        if parent_branch_url is not None:
            parent_branch = Branch.open(parent_branch_url)
        else:
            parent_branch = local_tree.branch
        push_result = push_to_salsa(
            local_tree,
            parent_branch,
            salsa_user,
            result.package,
            dry_run=dry_run,
        )
        if push_result:
            result.pushed = True
    return result


def move_instructions(package_name, salsa_user, old_vcs_url, new_vcs_url):
    yield "Please move the repository from %s to %s." % (old_vcs_url, new_vcs_url)
    if urlparse(old_vcs_url).hostname == "salsa.debian.org":
        path = urlparse(old_vcs_url).path
        if path.endswith(".git"):
            path = path[:-4]
        yield "If you have the salsa(1) tool installed, run: "
        yield ""
        yield "    salsa fork --group=%s %s" % (salsa_user, path)
    else:
        yield "If you have the salsa(1) tool installed, run: "
        yield ""
        yield "    git clone %s %s" % (old_vcs_url, package_name)
        yield "    salsa --group=%s push_repo %s" % (salsa_user, package_name)


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


def main(argv):
    import argparse
    import silver_platter  # noqa: F401

    parser = argparse.ArgumentParser(prog="debian-svp orphan URL|package")
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

    parser.add_argument("package", type=str, nargs="?")
    setup_parser(parser)
    args = parser.parse_args(argv)
    if args.package is None:
        parser.print_usage()
        return 1

    if args.name:
        branch_name = args.name
    else:
        branch_name = 'orphan'

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
    except NoVcsInformation as e:
        logging.fatal(
            'Package %s does not have any Vcs-* headers. '
            'Specify Git URL manually?', e.args[0])
        return 1
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

    if hoster is None and args.mode == "attempt-push":
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
        run_pre_check(ws.local_tree, args.pre_check)
        if control_files_in_root(ws.local_tree, subpath):
            debian_path = subpath
        else:
            debian_path = os.path.join(subpath, "debian")
        if args.update_changelog is None:
            dch_guess = guess_update_changelog(ws.local_tree, debian_path)
            if dch_guess:
                logging.info('%s', dch_guess[1])
                args.update_changelog = dch_guess[0]
            else:
                # Assume yes.
                args.update_changelog = True
        try:
            result = orphan(
                ws.local_tree,
                subpath=subpath,
                update_changelog=args.update_changelog,
                committer=args.committer,
                update_vcs=not args.no_update_vcs,
                dry_run=args.dry_run,
                salsa_user=args.salsa_user,
                salsa_push=not args.just_update_headers,
                check_wnpp=not args.no_check_wnpp,
            )
        except AlreadyOrphaned:
            logging.info('Package is already orphaned.')
            return 0
        except NoWnppBug as e:
            logging.fatal(
                "Package %s is purported to be orphaned, "
                "but no open wnpp bug exists." % e.package,
            )
            return 1
        except FormattingUnpreservable as e:
            logging.fatal(
                "unable to preserve formatting while editing %s" % e.path,
            )
            return 1
        except (ChangeConflict, GeneratedFile) as e:
            logging.fatal(
                "unable to edit generated file: %r" % e)
            return 1

        if not ws.changes_since_main():
            if existing_proposal:
                logging.info("%s: nothing left to do. Closing proposal.", pkg)
                existing_proposal.close()
            else:
                logging.info("%s: nothing to do", pkg)
            return None

        try:
            run_post_check(ws.local_tree, args.post_check, ws.base_revid)
        except PostCheckFailed as e:
            logging.info("%s: %s", pkg, e)
            return False
        if args.build_verify or args.install:
            try:
                ws.build(builder=args.builder, result_dir=args.build_target_dir)
            except BuildFailedError:
                logging.info("%s: build failed", pkg)
                return False
            except MissingUpstreamTarball:
                logging.info("%s: unable to find upstream source", pkg)
                return False

        if args.install:
            from .apply import install_built_package
            install_built_package(ws.local_tree, ws.subpath, args.build_target_dir)

        enable_tag_pushing(ws.local_tree.branch)

        try:
            publish_result = ws.publish_changes(
                mode,
                branch_name,
                get_proposal_description=lambda df, mp: "Set the package maintainer to the QA team.",
                get_proposal_commit_message=(
                    lambda oldmp: "Set the package maintainer to the QA team."
                ),
                dry_run=args.dry_run,
                hoster=hoster,
                allow_create_proposal=True,
                overwrite_existing=overwrite,
                existing_proposal=existing_proposal,
                derived_owner=args.derived_owner,
                labels=args.label,
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
            if publish_result.is_new:
                logging.info(
                    "Proposed change of maintainer to QA team: %s",
                    publish_result.proposal.url,
                )
            else:
                logging.info("No changes for orphaned package %s", result.package)
            if result.pushed:
                logging.info("Pushed new package to %s.", result.new_vcs_url)
            elif result.new_vcs_url:
                for line in move_instructions(
                    result.package,
                    result.salsa_user,
                    result.old_vcs_url,
                    result.new_vcs_url,
                ):
                    logging.info("%s", line)

        if args.diff:
            ws.show_diff(sys.stdout.buffer)
        if args.preserve_repositories:
            ws.defer_destroy()
            logging.info('Workspace preserved in %s', ws.local_tree.abspath(ws.subpath))

        return 0


if __name__ == '__main__':
    import sys
    sys.exit(main(sys.argv))
