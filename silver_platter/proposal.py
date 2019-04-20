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

__all__ = [
    'UnsupportedHoster',
    'BranchChanger',
    'BranchChangerResult',
    'propose_or_push',
    ]

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
    local_branch.push(remote_branch)
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
      Tuple with (base_branch, existing_branch, existing_proposal)
      Base branch won't be None; The existing_branch and existing_proposal can
      be None.
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
                return (existing_branch, existing_branch, mp)
            else:
                merged_proposal = mp
        else:
            if merged_proposal is not None:
                note('There is a proposal that has already been merged at %s.',
                     merged_proposal.url)
                return (main_branch, existing_branch, None)
            else:
                # No related merge proposals found
                return (main_branch, None, None)


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
    if additional_branches is None:
        additional_branches = []
    if mode not in ('push', 'propose', 'attempt-push'):
        raise ValueError("invalid mode %r" % mode)

    def report(text, *args, **kwargs):
        note('%r: ' + text, *((changer,)+args), **kwargs)
    try:
        hoster = get_hoster(main_branch, possible_hosters=possible_hosters)
    except UnsupportedHoster as e:
        if mode != 'push':
            raise
        base_branch = main_branch
        existing_branch = None
        existing_proposal = None
        warning('Unsupported hoster (%s), will attempt to push to %s',
                e, main_branch.user_url)
    else:
        (base_branch, existing_branch, existing_proposal) = (
            find_existing_proposed(main_branch, hoster, name))
    # Need to overwrite if there is an existing branch in place that we're not
    # using as base.
    overwrite = (existing_branch and existing_branch != base_branch)
    main_branch_revid = main_branch.last_revision()
    base_branch_revid = base_branch.last_revision()
    local_tree, destroy = create_temp_sprout(base_branch, additional_branches)
    try:
        with local_tree.branch.lock_write():
            if (mode == 'propose' and
                    existing_branch is not None and
                    (refresh or
                        merge_conflicts(main_branch, local_tree.branch))):
                report('restarting branch')
                local_tree.update(revision=main_branch_revid)
                local_tree.branch.generate_revision_history(main_branch_revid)
                overwrite = True

        local_branch = local_tree.branch
        orig_revid = local_branch.last_revision()

        changer.make_changes(local_tree)

        if local_branch.last_revision() == main_branch_revid:
            if existing_proposal is not None:
                report('closing existing merge proposal - no new revisions')
                existing_proposal.close()
            return BranchChangerResult(
                    start_time, existing_proposal,
                    is_new=None, main_branch_revid=main_branch_revid,
                    base_branch_revid=base_branch_revid,
                    result_revid=local_branch.last_revision(),
                    local_branch=local_branch, destroy=destroy)
        if (orig_revid == local_branch.last_revision()
                and existing_proposal is not None):
            # No new revisions added on this iteration, but still diverged from
            # main branch.
            return BranchChangerResult(
                start_time, existing_proposal, is_new=False,
                main_branch_revid=main_branch_revid,
                base_branch_revid=base_branch_revid,
                result_revid=local_branch.last_revision(),
                local_branch=local_branch, destroy=destroy)

        stack = local_branch.get_config()
        stack.set_user_option('branch.fetch_tags', True)

        if mode in ('push', 'attempt-push'):
            push_url = hoster.get_push_url(main_branch)
            report('pushing to %s', push_url)
            target_branch = Branch.open(
                    push_url, possible_transports=possible_transports)
            if not dry_run:
                try:
                    push_result(
                        local_branch, target_branch,
                        additional_colocated_branches=additional_branches)
                except (errors.PermissionDenied, errors.LockFailed):
                    if mode == 'attempt-push':
                        report('push access denied, falling back to propose')
                        mode = 'propose'
                    else:
                        report('permission denied during push')
                        raise
                else:
                    changer.post_land(target_branch)
                    return BranchChangerResult(
                        start_time, existing_proposal, is_new=False,
                        main_branch_revid=main_branch_revid,
                        base_branch_revid=base_branch_revid,
                        result_revid=local_branch.last_revision(),
                        local_branch=local_branch, destroy=destroy)
            else:
                # If mode == 'attempt-push', then we're not 100% sure that this
                # would have happened or if we would have fallen back to
                # propose.
                return BranchChangerResult(
                    start_time, None, is_new=False,
                    main_branch_revid=main_branch_revid,
                    base_branch_revid=base_branch_revid,
                    result_revid=local_branch.last_revision(),
                    local_branch=local_branch, destroy=destroy)

        assert mode == 'propose'
        if not existing_branch and not changer.should_create_proposal():
            return BranchChangerResult(
                start_time, None, is_new=None,
                main_branch_revid=main_branch_revid,
                base_branch_revid=base_branch_revid,
                result_revid=None, local_branch=local_branch, destroy=destroy)

        mp_description = changer.get_proposal_description(existing_proposal)
        # TODO(jelmer): Do the same for additional branches that have changed?
        (proposal, is_new) = create_or_update_proposal(
            local_branch, main_branch, hoster, name, mp_description,
            existing_branch=existing_branch,
            existing_proposal=existing_proposal, overwrite=overwrite,
            labels=labels, dry_run=dry_run)

        return BranchChangerResult(
            start_time, proposal, is_new=is_new,
            main_branch_revid=main_branch_revid,
            base_branch_revid=base_branch_revid,
            result_revid=local_branch.last_revision(),
            local_branch=local_branch, destroy=destroy)
    except BaseException:
        destroy()
        raise


def create_or_update_proposal(
        local_branch, main_branch, hoster, name,
        mp_description, existing_branch=None, existing_proposal=None,
        overwrite=False, labels=None, dry_run=False):
    """Create or update a merge proposal.

    Args:
      local_branch: Local branch with changes to propose
      main_branch: Target branch to propose against
      hoster: Associated hoster for main branch
      mp_description: Merge proposal description
      existing_branch: Existing derived branch
      existing_proposal: Existing merge proposal
      overwrite: Whether to overwrite changes
      labels: Labels to add
      dry_run: Whether to just dry-run the change
    Returns:
      Tuple with (proposal, is_new)
    """
    if not dry_run:
        if existing_branch is not None:
            local_branch.push(existing_branch, overwrite=overwrite)
            remote_branch = existing_branch
        else:
            remote_branch, public_branch_url = hoster.publish_derived(
                local_branch, main_branch, name=name, overwrite=overwrite)
    if existing_proposal is not None:
        if not dry_run:
            existing_proposal.set_description(mp_description)
        return (existing_proposal, False)
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
