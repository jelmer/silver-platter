use breezyshim::branch::BranchOpenError;
use breezyshim::ControlDir;
use log::info;
use pyo3::PyErr;
use std::collections::HashMap;

pub fn fetch_colocated(
    controldir: &ControlDir,
    from_controldir: &ControlDir,
    additional_colocated_branches: HashMap<&str, &str>,
) -> Result<(), PyErr> {
    info!(
        "Fetching colocated branches: {:?}",
        additional_colocated_branches
    );

    for (from_branch_name, to_branch_name) in additional_colocated_branches.iter() {
        match from_controldir.open_branch(Some(from_branch_name)) {
            Ok(remote_colo_branch) => {
                controldir.push_branch(
                    remote_colo_branch.as_ref(),
                    Some(to_branch_name),
                    Some(true),
                    None,
                )?;
            }
            Err(BranchOpenError::NotBranchError(_))
            | Err(BranchOpenError::NoColocatedBranchSupport) => {
                continue;
            }
            Err(e) => {
                return Err(e.into());
            }
        }
    }
    Ok(())
}
