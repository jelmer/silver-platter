use breezyshim::branch::{Branch, BranchOpenError};
use breezyshim::tree::WorkingTree;
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
    branch: &Branch,
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
                add_branch.push(&local_add_branch, false, None, None)?;
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
