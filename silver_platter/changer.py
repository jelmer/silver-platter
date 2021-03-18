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

import argparse
from typing import List, Optional, Any, Dict, Tuple

from breezy.propose import MergeProposal
from breezy.workingtree import WorkingTree

from .publish import (
    PublishResult,
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


class GenericChanger(object):
    """A class which can make and explain changes to a generic project in VCS."""

    name: str

    @classmethod
    def setup_parser(cls, parser: argparse.ArgumentParser) -> None:
        raise NotImplementedError(cls.setup_parser)

    @classmethod
    def from_args(cls, args: List[str]) -> "GenericChanger":
        raise NotImplementedError(cls.from_args)

    def suggest_branch_name(self) -> str:
        raise NotImplementedError(self.suggest_branch_name)

    def make_changes(
        self,
        local_tree: WorkingTree,
        subpath: str,
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
