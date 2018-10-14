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
