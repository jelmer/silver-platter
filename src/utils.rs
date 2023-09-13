use breezyshim::branch::{Branch, BranchOpenError};
use breezyshim::merge::{Error as MergeError, MergeType, Merger, MERGE_HOOKS};
use breezyshim::tree::WorkingTree;
use breezyshim::RevisionId;
use pyo3::PyErr;
use std::collections::HashMap;

pub enum Error {
    Other(PyErr),
}

impl From<PyErr> for Error {
    fn from(e: PyErr) -> Self {
        Error::Other(e)
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
) -> Result<(WorkingTree, Box<dyn FnOnce() -> std::io::Result<()> + Send>), Error> {
    let (td, path) = if let Some(path) = path {
        std::fs::create_dir(path).unwrap();
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
    let to_dir = branch
        .controldir()
        .sprout(to_url, Some(branch), Some(true), Some(use_stacking));
    // TODO(jelmer): Fetch these during the initial clone
    for (from_branch_name, to_branch_name) in additional_colocated_branches.unwrap_or_default() {
        let controldir = branch.controldir();
        match controldir.open_branch(Some(from_branch_name.as_str())) {
            Ok(add_branch) => {
                let local_add_branch = to_dir.create_branch(Some(to_branch_name.as_str()))?;
                add_branch.push(local_add_branch.as_ref(), false, None, None)?;
                assert_eq!(add_branch.last_revision(), local_add_branch.last_revision());
            }
            Err(BranchOpenError::NotBranchError(_))
            | Err(BranchOpenError::NoColocatedBranchSupport) => {
                // Ignore branches that don't exist or don't support colocated branches.
            }
            Err(BranchOpenError::DependencyNotPresent(e, d)) => {
                panic!("Need dependency to sprout branch: {} {}", e, d);
            }
            Err(BranchOpenError::Other(err)) => {
                return Err(err.into());
            }
        }
    }
    let wt = to_dir.open_workingtree()?;

    let destroy = Box::new(|| {
        if let Some(td) = td {
            td.close()
        } else {
            Ok(())
        }
    });

    Ok((wt, destroy))
}

pub fn merge_conflicts(
    main_branch: &dyn Branch,
    other_branch: &dyn Branch,
    other_revision: Option<&RevisionId>,
) -> bool {
    let other_revision = other_revision.map_or_else(|| other_branch.last_revision(), |r| r.clone());
    let other_repository = other_branch.repository();
    let graph = other_repository.get_graph();

    if graph.is_ancestor(&main_branch.last_revision(), &other_revision) {
        return false;
    }

    other_repository
        .fetch(
            &main_branch.repository(),
            Some(&main_branch.last_revision()),
        )
        .unwrap();

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
    result
}
