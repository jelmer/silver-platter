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

import datetime

from breezy.branch import Branch
from breezy.diff import show_diff_trees
from breezy.trace import (
    note,
    warning,
    )
from breezy import (
    errors,
    merge as _mod_merge,
    )
from breezy.plugins.propose.propose import (
    get_hoster,
    MergeProposal,
    UnsupportedHoster,
    )


from .utils import create_temp_sprout


__all__ = [
    'UnsupportedHoster',
    'BranchChanger',
    'BranchChangerResult',
    'propose_or_push',
    ]


def merge_conflicts(main_branch, other_branch):
    """Check whether two branches are conflicted when merged.

    Args:
      main_branch: Main branch to merge into
      other_branch: Branch to merge (and use for scratch access, needs write
                    access)
    Returns:
      boolean indicating whether the merge would result in conflicts
    """
    if other_branch.repository.get_graph().is_ancestor(
            main_branch.last_revision(), other_branch.last_revision()):
        return False

    other_branch.repository.fetch(
            main_branch.repository,
            revision_id=main_branch.last_revision())

    # Reset custom merge hooks, since they could make it harder to detect
    # conflicted merges that would appear on the hosting site.
    old_file_content_mergers = _mod_merge.Merger.hooks['merge_file_content']
    _mod_merge.Merger.hooks['merge_file_content'] = []
    try:
        merger = _mod_merge.Merger.from_revision_ids(
                other_branch.basis_tree(), other_branch=other_branch,
                other=main_branch.last_revision(), tree_branch=other_branch)
        merger.merge_type = _mod_merge.Merge3Merger
        tree_merger = merger.make_merger()
        with tree_merger.make_preview_transform():
            return bool(tree_merger.cooked_conflicts)
    finally:
        _mod_merge.Merger.hooks['merge_file_content'] = (
                old_file_content_mergers)


class BranchChanger(object):

    def make_changes(self, local_tree):
        raise NotImplementedError(self.make_changes)

    def get_proposal_description(self, existing_proposal):
        raise NotImplementedError(self.get_proposal_description)

    def should_create_proposal(self):
        raise NotImplementedError(self.should_create_proposal)

    def post_land(self, main_branch):
        """Called when changes land on the main branch.

        (either because they were directly pushed, or because a merge
         proposal was merged).
        """


class BranchChangerResult(object):
    """Result of a branch change action.

    :ivar merge_proposal: Relevant merge proposal, if one was created/updated.
    :ivar is_new: Whether the merge proposal is new
    :ivar start_time: Time at which processing began
    :ivar finish_time: Time at which processing ended
    :ivar main_branch_revid: Original revision id of the main branch
    :ivar base_branch_revid: Base branch revision id
    :ivar result_revid: Revision id for applied changes
    :ivar local_repository: Local repository for accessing revids
    """

    def __init__(self, start_time, merge_proposal, is_new, main_branch_revid,
                 base_branch_revid, result_revid, local_branch, destroy):
        self.merge_proposal = merge_proposal
        self.is_new = is_new
        self.start_time = start_time
        self.finish_time = datetime.datetime.now()
        self.main_branch_revid = main_branch_revid
        self.base_branch_revid = base_branch_revid
        self.result_revid = result_revid
        self.local_repository = local_branch.repository
        self._destroy = destroy

    def base_tree(self):
        return self.local_repository.revision_tree(self.base_branch_revid)

    def tree(self):
        if self.result_revid is None:
            return None
        return self.local_repository.revision_tree(self.result_revid)

    def cleanup(self):
        if self._destroy:
            self._destroy()

    def show_base_diff(self, outf):
        base_tree = self.base_tree()
        result_tree = self.tree()
        if result_tree:
            show_diff_trees(
                base_tree, result_tree, outf,
                old_label='upstream/',
                new_label=(
                    'proposed/' if self.merge_proposal else 'pushed/'))

    def __del__(self):
        self.cleanup()


class DryRunProposal(MergeProposal):
    """A merge proposal that is not actually created.

    :ivar url: URL for the merge proposal
    """

    def __init__(self, source_branch, target_branch, labels=None,
                 description=None):
        self.description = description
        self.closed = False
        self.labels = (labels or [])
        self.source_branch = source_branch
        self.target_branch = target_branch
        self.url = None

    @classmethod
    def from_existing(cls, mp, source_branch=None):
        if source_branch is None:
            source_branch = Branch.open(mp.get_source_branch_url())
        return cls(
            source_branch=source_branch,
            target_branch=Branch.open(mp.get_target_branch_url()),
            description=mp.get_description())

    def __repr__(self):
        return "%s(%r, %r)" % (
            self.__class__.__name__, self.source_branch, self.target_branch)

    def get_description(self):
        """Get the description of the merge proposal."""
        return self.description

    def set_description(self, description):
        self.description = description

    def get_source_branch_url(self):
        """Return the source branch."""
        return self.source_branch.user_url

    def get_target_branch_url(self):
        """Return the target branch."""
        return self.target_branch.user_url

    def close(self):
        """Close the merge proposal (without merging it)."""
        self.closed = True

    def is_merged(self):
        """Check whether this merge proposal has been merged."""
        return False


def push_result(local_branch, remote_branch,
                additional_colocated_branches=None):
    try:
        local_branch.push(remote_branch)
    except errors.LockFailed as e:
        # Almost certainly actually a PermissionDenied error..
        raise errors.PermissionDenied(path=remote_branch.user_url, extra=e)
    for branch_name in additional_colocated_branches or []:
        try:
            add_branch = local_branch.controldir.open_branch(
                name=branch_name)
        except errors.NotBranchError:
            pass
        else:
            remote_branch.controldir.push_branch(
                add_branch, name=branch_name)


def find_existing_proposed(main_branch, hoster, name):
    """Find an existing derived branch with the specified name, and proposal.

    Args:
      main_branch: Main branch
      hoster: The hoster
      name: Name of the derived branch
    Returns:
      Tuple with (resume_branch, overwrite_existing, existing_proposal)
      The resume_branch is the branch to continue from; overwrite_existing
      means there is an existing branch in place that should be overwritten.
    """
    try:
        existing_branch = hoster.get_derived_branch(main_branch, name=name)
    except errors.NotBranchError:
        return (main_branch, None, None)
    else:
        note('Branch %s already exists (branch at %s)', name,
             existing_branch.user_url)
        # If there is an open or rejected merge proposal, resume that.
        merged_proposal = None
        for mp in hoster.iter_proposals(
                existing_branch, main_branch, status='all'):
            if not mp.is_merged():
                return (existing_branch, False, mp)
            else:
                merged_proposal = mp
        else:
            if merged_proposal is not None:
                note('There is a proposal that has already been merged at %s.',
                     merged_proposal.url)
                return (None, True, None)
            else:
                # No related merge proposals found
                return (None, False, None)


class Workspace(object):
    """Workspace for creating changes to a branch.

    main_branch: The upstream branch
    resume_branch: Optional in-progress branch that we previously made changes
        on, and should ideally continue from.
    """

    def __init__(self, main_branch, resume_branch=None,
                 additional_branches=None):
        self.main_branch = main_branch
        self.main_branch_revid = main_branch.last_revision()
        self.resume_branch = resume_branch
        self.additional_branches = additional_branches or []
        self._destroy = None
        self.local_tree = None

    def __enter__(self):
        self.local_tree, self._destroy = create_temp_sprout(
            self.resume_branch or self.main_branch, self.additional_branches)
        self.refreshed = False
        with self.local_tree.branch.lock_write():
            if (self.resume_branch is not None and
                    merge_conflicts(
                        self.main_branch, self.local_tree.branch)):
                note('restarting branch')
                self.local_tree.update(revision=self.main_branch_revid)
                self.local_tree.branch.generate_revision_history(
                    self.main_branch_revid)
                self.resume_branch = None
            self.orig_revid = self.local_tree.last_revision()
        return self

    def defer_destroy(self):
        ret = self._destroy
        self._destroy = None
        return ret

    def changes_since_main(self):
        return self.local_tree.branch.last_revision() != self.main_branch_revid

    def changes_since_resume(self):
        return self.orig_revid != self.local_tree.branch.last_revision()

    def push(self, hoster=None, dry_run=False):
        if hoster is None:
            hoster = get_hoster(self.main_branch)
        return push_changes(
            self.local_tree.branch, self.main_branch, hoster=hoster,
            additional_colocated_branches=self.additional_colocated_branches,
            dry_run=dry_run)

    def propose(self, name, description, hoster=None, existing_proposal=None,
                overwrite_resume=None, labels=None, dry_run=False):
        if hoster is None:
            hoster = get_hoster(self.main_branch)
        return propose_changes(
            self.local_tree.branch, self.main_branch,
            hoster=hoster, name=name, description=description,
            resume_branch=self.resume_branch,
            existing_proposal=existing_proposal,
            overwrite_resume=overwrite_resume,
            labels=labels, dry_run=dry_run,
            additional_colocated_branches=self.additional_colocated_branches)

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._destroy:
            self._destroy()
            self._destroy = None
        return False


def enable_tag_pushing(branch):
    stack = branch.get_config()
    stack.set_user_option('branch.fetch_tags', True)


def propose_or_push(main_branch, name, changer, mode, dry_run=False,
                    possible_transports=None, possible_hosters=None,
                    additional_branches=None, refresh=False,
                    labels=None):
    """Create/update a merge proposal into a branch or push directly.

    Args:
      main_branch: Branch to create proposal for or push to
      name: Branch name (if creating a proposal)
      changer: An instance of `BranchChanger`
      mode: Mode (one of 'push', 'propose', 'attempt-push')
      dry_run: Whether to actually make remote changes
      possible_transports: Possible transports to reuse
      possible_hosters: Possible hosters to reuse
      additional_branches: Additional branches to fetch, if present
      refresh: Start over fresh when updating an existing branch for a merge
        proposal
      labels: Optional list of labels to set on merge proposal
    Returns:
      A BranchChangerResult
    """
    start_time = datetime.datetime.now()
    if mode not in ('push', 'propose', 'attempt-push'):
        raise ValueError("invalid mode %r" % mode)

    overwrite = False

    try:
        hoster = get_hoster(main_branch, possible_hosters=possible_hosters)
    except UnsupportedHoster as e:
        if mode != 'push':
            raise
        # We can't figure out what branch to resume from when there's no hoster
        # that can tell us.
        resume_branch = None
        existing_proposal = None
        warning('Unsupported hoster (%s), will attempt to push to %s',
                e, main_branch.user_url)
    else:
        (resume_branch, overwrite, existing_proposal) = (
            find_existing_proposed(main_branch, name))
    if refresh:
        resume_branch = None
    base_branch_revid = (resume_branch or main_branch).last_revision()
    with Workspace(
            main_branch,
            resume_branch=resume_branch,
            additional_branches=additional_branches) as ws:
        local_branch = ws.local_tree.branch

        changer.make_changes(ws.local_tree)

        enable_tag_pushing(local_branch)

        (proposal, is_new) = publish_changes(
            ws, mode, name,
            get_proposal_description=changer.get_proposal_description,
            dry_run=dry_run, hoster=hoster,
            allow_create_proposal=changer.should_create_proposal(),
            labels=labels, overwrite_existing=overwrite,
            existing_proposal=existing_proposal)

        if proposal is None:
            # TODO(jelmer): Is it safe to assume that if there is no
            # proposal that this was a push?
            changer.post_land(main_branch)
        return BranchChangerResult(
            start_time, proposal, is_new=is_new,
            main_branch_revid=ws.main_branch_revid,
            base_branch_revid=base_branch_revid,
            result_revid=local_branch.last_revision(),
            local_branch=local_branch, destroy=ws.defer_destroy())


def publish_changes(ws, mode, name, get_proposal_description, dry_run=False,
                    hoster=None, allow_create_proposal=True, labels=None,
                    overwrite_existing=True, existing_proposal=None):
    if not ws.changes_since_main():
        if existing_proposal is not None:
            note('closing existing merge proposal - no new revisions')
            existing_proposal.close()
        return (None, None)

    if not ws.changes_since_resume():
        # No new revisions added on this iteration, but changes since main
        # branch. We may not have gotten round to updating/creating the
        # merge proposal last time.
        note('No changes added; making sure merge proposal is up to date.')

    if hoster is None:
        hoster = get_hoster(ws.main_branch)
    if mode in ('push', 'attempt-push'):
        try:
            ws.push(hoster, dry_run=dry_run)
        except errors.PermissionDenied:
            if mode == 'attempt-push':
                note('push access denied, falling back to propose')
                mode = 'propose'
            else:
                note('permission denied during push')
                raise
        else:
            return (None, False)

    assert mode == 'propose'
    if not ws.resume_branch and not allow_create_proposal:
        # TODO(jelmer): Raise an exception of some sort here?
        return (None, False)

    mp_description = get_proposal_description(
        existing_proposal if ws.resume_branch else None)
    (proposal, is_new) = ws.propose(
        hoster, name, mp_description,
        existing_proposal=existing_proposal,
        labels=labels, dry_run=dry_run, overwrite_existing=overwrite_existing)

    return (proposal, is_new)


def push_changes(local_branch, main_branch, hoster, possible_transports=None,
                 additional_colocated_branches=None, dry_run=False):
    """Push changes to a branch."""
    push_url = hoster.get_push_url(main_branch)
    note('pushing to %s', push_url)
    target_branch = Branch.open(
        push_url, possible_transports=possible_transports)
    if not dry_run:
        push_result(local_branch, target_branch, additional_colocated_branches)


def propose_changes(
        local_branch, main_branch, hoster, name,
        mp_description, resume_branch=None, resume_proposal=None,
        overwrite_existing=True,
        labels=None, dry_run=False, additional_colocated_branches=None):
    """Create or update a merge proposal.

    Args:
      local_branch: Local branch with changes to propose
      main_branch: Target branch to propose against
      hoster: Associated hoster for main branch
      mp_description: Merge proposal description
      resume_branch: Existing derived branch
      resume_proposal: Existing merge proposal to resume
      overwrite_existing: Whether to overwrite any other existing branch
      labels: Labels to add
      dry_run: Whether to just dry-run the change
      additional_colocated_branches: Additional colocated branches to propose
    Returns:
      Tuple with (proposal, is_new)
    """
    # TODO(jelmer): Actually push additional_colocated_branches
    if not dry_run:
        if resume_branch is not None:
            local_branch.push(resume_branch)
            remote_branch = resume_branch
        else:
            remote_branch, public_branch_url = hoster.publish_derived(
                local_branch, main_branch, name=name,
                overwrite=overwrite_existing)
    if resume_proposal is not None:
        if dry_run:
            resume_proposal = DryRunProposal.from_existing(
                resume_proposal, source_branch=local_branch)
        resume_proposal.set_description(mp_description)
        return (resume_proposal, False)
    else:
        if not dry_run:
            proposal_builder = hoster.get_proposer(
                    remote_branch, main_branch)
            try:
                mp = proposal_builder.create_proposal(
                    description=mp_description, labels=labels)
            except errors.PermissionDenied:
                note('Permission denied while trying to create '
                     'proposal.')
                raise
        else:
            mp = DryRunProposal(
                local_branch, main_branch, labels=labels,
                description=mp_description)
        return (mp, True)
