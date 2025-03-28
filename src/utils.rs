//! Utility functions for working with branches.
use breezyshim::branch::Branch;
use breezyshim::controldir::ControlDir;
use breezyshim::error::Error as BrzError;
use breezyshim::merge::{Error as MergeError, MergeType, Merger, MERGE_HOOKS};
use breezyshim::tree::WorkingTree;
use breezyshim::RevisionId;
use std::collections::HashMap;

/// A temporary sprout of a branch.
pub struct TempSprout {
    /// The working tree of the sprout.
    pub workingtree: WorkingTree,

    /// The temporary directory that the sprout is in.
    pub tempdir: Option<tempfile::TempDir>,
}

impl TempSprout {
    /// Create a temporary sprout of a branch.
    pub fn new(
        branch: &dyn Branch,
        additional_colocated_branches: Option<HashMap<String, String>>,
    ) -> Result<Self, BrzError> {
        let (wt, td) = create_temp_sprout(branch, additional_colocated_branches, None, None)?;
        Ok(Self {
            workingtree: wt,
            tempdir: td,
        })
    }

    /// Create a temporary sprout of a branch in a specific directory.
    pub fn new_in(
        branch: &dyn Branch,
        additional_colocated_branches: Option<HashMap<String, String>>,
        dir: &std::path::Path,
    ) -> Result<Self, BrzError> {
        let (wt, tempdir) =
            create_temp_sprout(branch, additional_colocated_branches, Some(dir), None)?;
        Ok(Self {
            workingtree: wt,
            tempdir,
        })
    }

    /// Create a temporary sprout of a branch with a specific path.
    pub fn new_in_path(
        branch: &dyn Branch,
        additional_colocated_branches: Option<HashMap<String, String>>,
        path: &std::path::Path,
    ) -> Result<Self, BrzError> {
        let (wt, tempdir) =
            create_temp_sprout(branch, additional_colocated_branches, None, Some(path))?;
        Ok(Self {
            workingtree: wt,
            tempdir,
        })
    }

    /// Return the tree of the sprout.
    pub fn tree(&self) -> &WorkingTree {
        &self.workingtree
    }
}

impl std::ops::Deref for TempSprout {
    type Target = WorkingTree;

    fn deref(&self) -> &Self::Target {
        &self.workingtree
    }
}

/// Create a temporary sprout of a branch.
///
/// This attempts to fetch the least amount of history as possible.
pub fn create_temp_sprout(
    branch: &dyn Branch,
    additional_colocated_branches: Option<HashMap<String, String>>,
    dir: Option<&std::path::Path>,
    path: Option<&std::path::Path>,
) -> Result<(WorkingTree, Option<tempfile::TempDir>), BrzError> {
    let (to_dir, td) = create_temp_sprout_cd(branch, additional_colocated_branches, dir, path)?;

    let wt = to_dir.open_workingtree()?;

    Ok((wt, td))
}

/// Create a temporary sprout of a branch.
///
/// This attempts to fetch the least amount of history as possible.
fn create_temp_sprout_cd(
    branch: &dyn Branch,
    additional_colocated_branches: Option<HashMap<String, String>>,
    dir: Option<&std::path::Path>,
    path: Option<&std::path::Path>,
) -> Result<(ControlDir, Option<tempfile::TempDir>), BrzError> {
    let (td, path) = if let Some(path) = path {
        // ensure that path is absolute
        assert!(path.is_absolute());
        (None, path.to_path_buf())
    } else {
        let td = if let Some(dir) = dir {
            tempfile::tempdir_in(dir).unwrap()
        } else {
            tempfile::tempdir().unwrap()
        };
        let path = td.path().to_path_buf();
        (Some(td), path)
    };

    // Only use stacking if the remote repository supports chks because of
    // https://bugs.launchpad.net/bzr/+bug/375013
    let use_stacking =
        branch.format().supports_stacking() && branch.repository().format().supports_chks();
    let to_url: url::Url = url::Url::from_directory_path(path).unwrap();

    // preserve whatever source format we have.
    let to_dir =
        branch
            .controldir()
            .sprout(to_url, Some(branch), Some(true), Some(use_stacking), None)?;
    // TODO(jelmer): Fetch these during the initial clone
    for (from_branch_name, to_branch_name) in additional_colocated_branches.unwrap_or_default() {
        let controldir = branch.controldir();
        match controldir.open_branch(Some(from_branch_name.as_str())) {
            Ok(add_branch) => {
                let local_add_branch = to_dir.create_branch(Some(to_branch_name.as_str()))?;
                add_branch.push(local_add_branch.as_ref(), false, None, None)?;
                assert_eq!(add_branch.last_revision(), local_add_branch.last_revision());
            }
            Err(BrzError::NotBranchError(..)) | Err(BrzError::NoColocatedBranchSupport) => {
                // Ignore branches that don't exist or don't support colocated branches.
            }
            Err(BrzError::DependencyNotPresent(e, d)) => {
                panic!("Need dependency to sprout branch: {} {}", e, d);
            }
            Err(err) => {
                return Err(err);
            }
        }
    }

    Ok((to_dir, td))
}

/// Check if there are any merge conflicts between two branches.
pub fn merge_conflicts(
    main_branch: &dyn Branch,
    other_branch: &dyn Branch,
    other_revision: Option<&RevisionId>,
) -> Result<bool, BrzError> {
    let other_revision = other_revision.map_or_else(|| other_branch.last_revision(), |r| r.clone());
    let other_repository = other_branch.repository();
    let graph = other_repository.get_graph();

    if graph.is_ancestor(&main_branch.last_revision(), &other_revision) {
        return Ok(false);
    }

    other_repository.fetch(
        &main_branch.repository(),
        Some(&main_branch.last_revision()),
    )?;

    // Reset custom merge hooks, since they could make it harder to detect
    // conflicted merges that would appear on the hosting site.
    let old_file_contents_mergers = MERGE_HOOKS.get("merge_file_content").unwrap();
    MERGE_HOOKS.clear("merge_file_contents").unwrap();

    let other_tree = other_repository.revision_tree(&other_revision).unwrap();
    let result = match Merger::from_revision_ids(
        &other_tree,
        other_branch,
        &main_branch.last_revision(),
        other_branch,
    ) {
        Ok(mut merger) => {
            merger.set_merge_type(MergeType::Merge3);
            let tree_merger = merger.make_merger().unwrap();
            let tt = tree_merger.make_preview_transform().unwrap();
            !tt.cooked_conflicts().unwrap().is_empty()
        }
        Err(MergeError::UnrelatedBranches) => {
            // Unrelated branches don't technically *have* to lead to
            // conflicts, but there's not a lot to be salvaged here, either.
            true
        }
    };
    for hook in old_file_contents_mergers {
        MERGE_HOOKS.add("merge_file_content", hook).unwrap();
    }
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_sprout() {
        let base = tempfile::tempdir().unwrap();
        let wt = breezyshim::controldir::create_standalone_workingtree(
            base.path(),
            &breezyshim::controldir::ControlDirFormat::default(),
        )
        .unwrap();
        let revid = wt
            .build_commit()
            .message("Initial commit")
            .allow_pointless(true)
            .commit()
            .unwrap();

        let sprout = TempSprout::new(wt.branch().as_ref(), None).unwrap();

        assert_eq!(sprout.last_revision().unwrap(), revid);
        let tree = sprout.tree();
        assert_eq!(tree.last_revision().unwrap(), revid);
        std::mem::drop(sprout);
    }

    #[test]
    fn test_sprout_in() {
        let base = tempfile::tempdir().unwrap();
        let wt = breezyshim::controldir::create_standalone_workingtree(
            base.path(),
            &breezyshim::controldir::ControlDirFormat::default(),
        )
        .unwrap();
        let revid = wt
            .build_commit()
            .message("Initial commit")
            .allow_pointless(true)
            .commit()
            .unwrap();

        let sprout = TempSprout::new_in(wt.branch().as_ref(), None, base.path()).unwrap();

        assert_eq!(sprout.last_revision().unwrap(), revid);
        let tree = sprout.tree();
        assert_eq!(tree.last_revision().unwrap(), revid);
        std::mem::drop(sprout);
    }

    #[test]
    fn test_sprout_in_path() {
        let base = tempfile::tempdir().unwrap();
        let target = tempfile::tempdir().unwrap();
        let wt = breezyshim::controldir::create_standalone_workingtree(
            base.path(),
            &breezyshim::controldir::ControlDirFormat::default(),
        )
        .unwrap();
        let revid = wt
            .build_commit()
            .message("Initial commit")
            .allow_pointless(true)
            .commit()
            .unwrap();

        let sprout = TempSprout::new_in_path(wt.branch().as_ref(), None, target.path()).unwrap();

        assert_eq!(sprout.last_revision().unwrap(), revid);
        let tree = sprout.tree();
        assert_eq!(tree.last_revision().unwrap(), revid);
        std::mem::drop(sprout);
    }
}
