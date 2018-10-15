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

from breezy.branch import Branch
from breezy.trace import note
from breezy import (
    errors,
    merge as _mod_merge,
    )
from breezy.plugins.propose.propose import (
    get_hoster,
    NoMergeProposal,
    )


from .utils import TemporarySprout


def merge_conflicts(main_branch, other_branch):
    """Check whether two branches are conflicted when merged.

    Args:
      main_branch: Main branch to merge into
      other_branch: Branch to merge (and use for scratch access, needs write access)
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
        with tree_merger.make_preview_transform() as tt:
            return bool(tree_merger.cooked_conflicts)
    finally:
        _mod_merge.Merger.hooks['merge_file_content'] = old_file_content_mergers


class BranchChanger(object):

    def make_changes(self, local_tree):
        raise NotImplementedError(self.make_changes)

    def get_proposal_description(self, existing_proposal):
        raise NotImplementedError(self.get_proposal_description)

    def should_create_proposal(self):
        raise NotImplementedError(self.should_create_proposal)


def propose_or_push(main_branch, name, changer, mode, dry_run=False,
                    possible_transports=None, possible_hosters=None,
                    additional_branches=None):
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
    """
    if additional_branches is None:
        additional_branches = []
    if mode not in ('push', 'propose', 'attempt-push'):
        raise ValueError("invalid mode %r" % mode)
    def report(text, *args, **kwargs):
        note('%r: ' + text, *((changer,)+args), **kwargs)
    overwrite = False
    hoster = get_hoster(main_branch, possible_hosters=possible_hosters)
    try:
        existing_branch = hoster.get_derived_branch(main_branch, name=name)
    except errors.NotBranchError:
        base_branch = main_branch
        existing_branch = None
        existing_proposal = None
    else:
        report('Already proposed: %s (branch at %s)', name, existing_branch.user_url)
        base_branch = existing_branch
        try:
            existing_proposal = hoster.get_proposal(existing_branch, main_branch)
        except NoMergeProposal:
            existing_proposal = None
    with TemporarySprout(base_branch) as local_tree:
        # TODO(jelmer): Fetch these during the initial clone
        for branch_name in additional_branches:
            try:
                add_branch = main_branch.controldir.open_branch(name=branch_name)
            except NotBranchError:
                pass
            else:
                local_add_branch = local_tree.controldir.create_branch(name=branch_name)
                add_branch.push(local_add_branch)
            assert add_branch.last_revision() == local_add_branch.last_revision()
        with local_tree.branch.lock_read():
            if (mode == 'propose' and
                existing_branch is not None and
                merge_conflicts(main_branch, local_tree.branch)):
                report('branch is conflicted, restarting.')
                main_branch_revid = main_branch.last_revision()
                local_tree.update(revision=main_branch_revid)
                local_tree.branch.generate_revision_history(main_branch_revid)
                overwrite = True

        local_branch = local_tree.branch
        orig_revid = local_branch.last_revision()

        changer.make_changes(local_tree)

        if local_branch.last_revision() == main_branch.last_revision():
            if existing_proposal is not None:
                report('closing existing merge proposal - no new revisions')
                # TODO(jelmer): existing_proposal.close()
            return
        if orig_revid == local_branch.last_revision():
            # No new revisions added on this iteration, but still diverged from main branch.
            return

        if mode in ('push', 'attempt-push'):
            push_url = hoster.get_push_url(main_branch)
            report('pushing to %s', push_url)
            target_branch = Branch.open(push_url, possible_transports=possible_transports)
            if not dry_run:
                try:
                    local_branch.push(target_branch)
                except (errors.PermissionDenied, errors.LockFailed):
                    if mode == 'attempt-push':
                        report('push access denied, falling back to propose')
                        mode = 'propose'
                    else:
                        report('permission denied during push')
                        raise
                else:
                    for branch_name in additional_branches:
                        try:
                            add_branch = local_branch.controldir.open_branch(name=branch_name)
                        except NotBranchError:
                            pass
                        else:
                            target_branch.controldir.push_branch(add_branch, name=branch_name)
        if mode == 'propose':
            if not existing_branch and not changer.should_create_proposal():
                return
            if not dry_run:
                if existing_branch is not None:
                    local_branch.push(existing_branch, overwrite=overwrite)
                    remote_branch = existing_branch
                else:
                    remote_branch, public_branch_url = hoster.publish_derived(
                        local_branch, main_branch, name=name, overwrite=False)
            mp_description = changer.get_proposal_description(existing_proposal)
            if existing_proposal is not None:
                if not dry_run:
                    existing_proposal.set_description(mp_description)
                report('Updated proposal %s with fixes %r', existing_proposal.url,
                     [f for f, l in changer.applied])
            else:
                if not dry_run:
                    proposal_builder = hoster.get_proposer(remote_branch, main_branch)
                    try:
                        mp = proposal_builder.create_proposal(
                            description=mp_description, labels=[])
                    except errors.PermissionDenied:
                        report('Permission denied while trying to create proposal.')
                        raise
                report('Proposed fixes %r: %s', [f for f, l in changer.applied], mp.url)
