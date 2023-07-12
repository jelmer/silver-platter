use crate::breezyshim::{Branch, Forge, RevisionId};
use pyo3::import_exception;
use pyo3::prelude::*;

import_exception!(breezy.errors, NotBranchError);

fn _tag_selector_from_tags(
    tags: std::collections::HashMap<String, RevisionId>,
) -> impl Fn(String) -> bool {
    move |tag| tags.contains_key(tag.as_str())
}

pub fn push_derived_changes(
    local_branch: &Branch,
    main_branch: &Branch,
    forge: &Forge,
    name: &str,
    overwrite_existing: Option<bool>,
    owner: Option<&str>,
    tags: Option<std::collections::HashMap<String, RevisionId>>,
    stop_revision: Option<&RevisionId>,
) -> PyResult<(Branch, url::Url)> {
    let tags = tags.unwrap_or_default();
    let (remote_branch, public_branch_url) = forge.publish_derived(
        local_branch,
        main_branch,
        name,
        overwrite_existing,
        owner,
        stop_revision,
        Box::new(_tag_selector_from_tags(tags)),
    )?;
    Ok((remote_branch, public_branch_url))
}

pub fn push_result(
    local_branch: &Branch,
    remote_branch: &Branch,
    additional_colocated_branches: Option<Vec<(String, String)>>,
    tags: Option<std::collections::HashMap<String, RevisionId>>,
    stop_revision: Option<&RevisionId>,
) -> PyResult<()> {
    Python::with_gil(|py| {
        let tag_selector = Box::new(_tag_selector_from_tags(tags.clone().unwrap_or_default()));
        local_branch.push(remote_branch, false, stop_revision, tag_selector)?;

        for (from_branch_name, to_branch_name) in additional_colocated_branches.unwrap_or_default()
        {
            match local_branch
                .get_controldir()
                .open_branch(Some(from_branch_name.as_str()))
            {
                Ok(branch) => {
                    let tag_selector =
                        Box::new(_tag_selector_from_tags(tags.clone().unwrap_or_default()));
                    remote_branch.get_controldir().push_branch(
                        &branch,
                        Some(to_branch_name.as_str()),
                        tag_selector,
                    )?;
                }
                Err(e) if e.is_instance_of::<NotBranchError>(py) => {}
                Err(e) => return Err(e),
            };
        }
        Ok(())
    })
}
