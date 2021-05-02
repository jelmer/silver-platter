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

from typing import Optional, List

from breezy import osutils

from debmutate.control import ControlEditor, delete_from_list
from debmutate.deb822 import ChangeConflict
from debmutate.reformatting import GeneratedFile, FormattingUnpreservable


from . import (
    add_changelog_entry,
)
from .changer import (
    run_mutator,
    DebianChanger,
    ChangerError,
    ChangerResult,
)


BRANCH_NAME = "mia"
MIA_EMAIL = "mia@qa.debian.org"
MIA_TEAMMAINT_USERTAG = "mia-teammaint"


class MIAResult(object):
    def __init__(
        self, source=None, removed_uploaders=None, bugs=None
    ):
        self.source = source
        self.removed_uploaders = removed_uploaders
        self.bugs = bugs


def all_mia_teammaint_bugs():
    import debianbts

    return set(
        debianbts.get_usertag(MIA_EMAIL, [MIA_TEAMMAINT_USERTAG])[MIA_TEAMMAINT_USERTAG]
    )


def get_package_bugs(source):
    import debianbts

    return set(debianbts.get_bugs(src=source, status="open"))


def get_mia_maintainers(bug) -> Optional[List[str]]:
    import debianbts

    log = debianbts.get_bug_log(bug)
    return log[0]["message"].get_all("X-Debbugs-CC")


def drop_uploaders(editor, mia_people):
    removed_mia = []
    try:
        uploaders = editor.source["Uploaders"].split(",")
    except KeyError:
        raise ChangerError("nothing-to-do", "No uploaders field")

    for person in mia_people:
        if person in [uploader.strip() for uploader in uploaders]:
            editor.source["Uploaders"] = delete_from_list(
                editor.source["Uploaders"], person
            )
            removed_mia.append(person)

    if not editor.source['Uploaders']:
        del editor.source['Uploaders']
    return removed_mia


def tree_drop_mia_uploaders(local_tree, subpath, update_changelog=None, committer=None):
    control_path = local_tree.abspath(osutils.pathjoin(subpath, "debian/control"))
    changelog_entries = []
    with ControlEditor(path=control_path) as editor:
        source = editor.source["Source"]
        bugs = all_mia_teammaint_bugs().intersection(get_package_bugs(source))
        if not bugs:
            raise ChangerError("nothing-to-do", "No MIA people")
        removed_uploaders = []
        fixed_bugs = []
        for bug in bugs:
            mia_people = get_mia_maintainers(bug)

            if mia_people is None:
                logging.warning('No MIA people (X-Debbugs-CC) found in bug %d', bug)
                continue

            removed_mia = drop_uploaders(editor, mia_people)

            if len(removed_mia) == 0:
                continue

            if len(removed_mia) == 1:
                description = "Remove MIA uploader %s." % removed_mia[0]
            else:
                description = "Remove MIA uploaders %s." % (
                    ", ".join(removed_mia)
                )
            if removed_mia == mia_people:
                description += " Closes: #%d" % bug
            changelog_entries.append(description)
            removed_uploaders.extend(removed_mia)

    if not changelog_entries:
        return MIAResult(source, removed_uploaders=[], bugs=fixed_bugs)
    if update_changelog in (True, None):
        add_changelog_entry(
            local_tree,
            osutils.pathjoin(subpath, "debian/changelog"),
            changelog_entries,
        )
    local_tree.commit(
        "Remove MIA uploaders.", committer=committer, allow_pointless=False
    )
    return MIAResult(source, removed_uploaders=removed_uploaders, bugs=fixed_bugs)


class MIAChanger(DebianChanger):

    name = "mia"

    def __init__(self, dry_run=False):
        self.dry_run = dry_run

    @classmethod
    def setup_parser(cls, parser):
        pass

    @classmethod
    def from_args(cls, args):
        return cls(dry_run=args.dry_run)

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
        try:
            result = tree_drop_mia_uploaders(
                local_tree, subpath, update_changelog=update_changelog,
                committer=committer)
        except FormattingUnpreservable as e:
            raise ChangerError(
                "formatting-unpreservable",
                "unable to preserve formatting while editing %s" % e.path,
            )
        except (ChangeConflict, GeneratedFile) as e:
            raise ChangerError(
                "generated-file", "unable to edit generated file: %r" % e
            )

        reporter.report_metadata("bugs", result.bugs)
        reporter.report_metadata("removed_uploaders", result.removed_uploaders)

        if not result.removed_uploaders:
            raise ChangerError(
                "nothing-to-do", "Unable to remove any MIA uploaders"
            )

        branches = [("main", None, base_revid, local_tree.last_revision())]

        tags = []

        return ChangerResult(
            description="Remove MIA uploaders.",
            mutator=result,
            branches=branches,
            tags=tags,
            sufficient_for_proposal=True,
            proposed_commit_message=("Remove MIA uploaders."),
        )

    def get_proposal_description(self, applied, description_format, existing_proposal):
        text = "Remove MIA uploaders:\n\n"
        for uploader in applied.removed_uploaders:
            text += " * %s\n" % uploader
        return text

    def describe(self, result, publish_result):
        logging.info(
            "Removed MIA uploaders: %s",
            publish_result.proposal.url,
        )

    @classmethod
    def describe_command(cls, command):
        return "Remove MIA maintainers."


if __name__ == "__main__":
    import sys

    sys.exit(run_mutator(MIAChanger))
