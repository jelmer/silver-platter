use crate::breezyshim::{Branch, Forge, RevisionId, Transport};
use crate::vcs::open_branch;
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

pub fn push_changes(
    local_branch: &Branch,
    main_branch: &Branch,
    forge: Option<&Forge>,
    possible_transports: Option<Vec<Transport>>,
    additional_colocated_branches: Option<Vec<(String, String)>>,
    dry_run: bool,
    tags: Option<std::collections::HashMap<String, RevisionId>>,
    stop_revision: Option<&RevisionId>,
) -> PyResult<()> {
    let push_url = if let Some(forge) = forge {
        forge.get_push_url(main_branch)
    } else {
        main_branch.get_user_url()
    };
    log::info!("pushing to {}", push_url);
    let target_branch = open_branch(push_url, possible_transports, None, None)?;
    if !dry_run {
        push_result(
            local_branch,
            &target_branch,
            additional_colocated_branches,
            tags,
            stop_revision,
        )
    } else {
        log::info!("dry run, not pushing");
        Ok(())
    }
}
