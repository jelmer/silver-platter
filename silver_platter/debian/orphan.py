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

import logging
from urllib.parse import urlparse

from breezy import osutils
from breezy.branch import Branch

from debmutate.control import ControlEditor
from debmutate.deb822 import ChangeConflict
from debmutate.reformatting import GeneratedFile, FormattingUnpreservable


from . import (
    pick_additional_colocated_branches,
    connect_udd_mirror,
    add_changelog_entry,
)
from .changer import (
    run_mutator,
    DebianChanger,
    ChangerError,
    ChangerResult,
)
from ..proposal import push_changes


BRANCH_NAME = "orphan"


def push_to_salsa(local_tree, orig_branch, user, name, dry_run=False):
    from breezy import urlutils
    from breezy.branch import Branch
    from breezy.errors import PermissionDenied
    from breezy.propose import UnsupportedHoster, get_hoster, HosterLoginRequired
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
            from_project = urlutils.from_string(orig_branch.controldir.user_url).path
        else:
            from_project = None

    if from_project is not None:
        salsa.fork_project(from_project, owner=user)
    else:
        try:
            salsa.create_project("%s/%s" % (user, name))
        except PermissionDenied as e:
            logging.info('No permission to create new project under %s: %s',
                         user, e)
            return
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


def find_wnpp_bug(source):
    conn = connect_udd_mirror()
    cursor = conn.cursor()
    cursor.execute("select id from wnpp where type = 'O' and source = %s", (source,))
    entry = cursor.fetchone()
    if entry is None:
        raise KeyError
    return entry[0]


class OrphanChanger(DebianChanger):

    name = "orphan"

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
    def setup_parser(cls, parser):
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

    @classmethod
    def from_args(cls, args):
        return cls(
            update_vcs=not args.no_update_vcs,
            dry_run=args.dry_run,
            salsa_user=args.salsa_user,
            salsa_push=not args.just_update_headers,
            check_wnpp=not args.no_check_wnpp,
        )

    def suggest_branch_name(self):
        return BRANCH_NAME

    def make_changes(  # noqa: C901
        self,
        local_tree,
        subpath,
        update_changelog,
        reporter,
        committer,
        base_proposal=None,
    ):
        base_revid = local_tree.last_revision()
        control_path = local_tree.abspath(osutils.pathjoin(subpath, "debian/control"))
        changelog_entries = []
        try:
            with ControlEditor(path=control_path) as editor:
                if self.check_wnpp:
                    try:
                        wnpp_bug = find_wnpp_bug(editor.source["Source"])
                    except KeyError:
                        raise ChangerError(
                            "nothing-to-do",
                            "Package is purported to be orphaned, "
                            "but no open wnpp bug exists.",
                        )
                else:
                    wnpp_bug = None
                editor.source["Maintainer"] = "Debian QA Group <packages@qa.debian.org>"
                try:
                    del editor.source["Uploaders"]
                except KeyError:
                    pass
            if editor.changed:
                if wnpp_bug is not None:
                    changelog_entries.append("Orphan package - see bug %d." % wnpp_bug)
                else:
                    changelog_entries.append("Orphan package.")
            result = OrphanResult(wnpp_bug=wnpp_bug)

            if self.update_vcs:
                with ControlEditor(path=control_path) as editor:
                    result.package_name = editor.source["Source"]
                    result.old_vcs_url = editor.source.get("Vcs-Git")
                    editor.source["Vcs-Git"] = "https://salsa.debian.org/%s/%s.git" % (
                        self.salsa_user,
                        result.package_name,
                    )
                    result.new_vcs_url = editor.source["Vcs-Git"]
                    editor.source["Vcs-Browser"] = "https://salsa.debian.org/%s/%s" % (
                        self.salsa_user,
                        result.package_name,
                    )
                    result.salsa_user = self.salsa_user
                if result.old_vcs_url == result.new_vcs_url:
                    result.old_vcs_url = result.new_vcs_url = None
                if editor.changed:
                    changelog_entries.append(
                        "Update VCS URLs to point to Debian group."
                    )
            if not changelog_entries:
                raise ChangerError("nothing-to-do", "Already orphaned")
            if update_changelog in (True, None):
                add_changelog_entry(
                    local_tree,
                    osutils.pathjoin(subpath, "debian/changelog"),
                    ["QA Upload."] + changelog_entries,
                )
            local_tree.commit(
                "Move package to QA team.", committer=committer, allow_pointless=False
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

        result.pushed = False
        if self.update_vcs and self.salsa_push and result.new_vcs_url:
            parent_branch_url = local_tree.branch.get_parent()
            if parent_branch_url is not None:
                parent_branch = Branch.open(parent_branch_url)
            else:
                parent_branch = local_tree.branch
            push_result = push_to_salsa(
                local_tree,
                parent_branch,
                self.salsa_user,
                result.package_name,
                dry_run=self.dry_run,
            )
            if push_result:
                result.pushed = True
        reporter.report_metadata("old_vcs_url", result.old_vcs_url)
        reporter.report_metadata("new_vcs_url", result.new_vcs_url)
        reporter.report_metadata("pushed", result.pushed)
        reporter.report_metadata("wnpp_bug", result.wnpp_bug)

        branches = [("main", None, base_revid, local_tree.last_revision())]

        tags = []

        return ChangerResult(
            description="Move package to QA team.",
            mutator=result,
            branches=branches,
            tags=tags,
            sufficient_for_proposal=True,
            proposed_commit_message=("Set the package maintainer to the QA team."),
        )

    def get_proposal_description(self, applied, description_format, existing_proposal):
        return "Set the package maintainer to the QA team."

    def describe(self, result, publish_result):
        if publish_result.is_new:
            logging.info(
                "Proposed change of maintainer to QA team: %s",
                publish_result.proposal.url,
            )
        else:
            logging.info("No changes for orphaned package %s", result.package_name)
        if result.pushed:
            logging.info("Pushed new package to %s.", result.new_vcs_url)
        elif result.new_vcs_url:
            for line in move_instructions(
                result.package_name,
                result.salsa_user,
                result.old_vcs_url,
                result.new_vcs_url,
            ):
                logging.info("%s", line)

    @classmethod
    def describe_command(cls, command):
        return "Mark as orphaned"


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


if __name__ == "__main__":
    import sys

    sys.exit(run_mutator(OrphanChanger))
