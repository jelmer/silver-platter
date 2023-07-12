use crate::breezyshim::{Branch, Forge, MergeProposal, RevisionId, Transport};
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

/// Find an existing derived branch with the specified name, and proposal.
///
/// # Arguments:
///
/// * `main_branch` - Main branch
/// * `forge` - The forge
/// * `name` - Name of the derived branch
/// * `overwrite_unrelated` - Whether to overwrite existing (but unrelated) branches
/// * `owner` - Owner of the branch
/// * `preferred_schemes` - List of preferred schemes
///
/// # Returns:
///   Tuple with (resume_branch, overwrite_existing, existing_proposal)
///   The resume_branch is the branch to continue from; overwrite_existing
///   means there is an existing branch in place that should be overwritten.
pub fn find_existing_proposed(
    main_branch: &Branch,
    forge: &Forge,
    name: &str,
    overwrite_unrelated: bool,
    owner: Option<&str>,
    preferred_schemes: Option<&[&str]>,
) -> PyResult<(Option<Branch>, Option<bool>, Option<Vec<MergeProposal>>)> {
    Python::with_gil(|py| {
        let existing_branch =
            match forge.get_derived_branch(main_branch, name, owner, preferred_schemes) {
                Ok(branch) => branch,
                Err(e) if e.is_instance_of::<NotBranchError>(py) => {
                    return Ok((None, None, None));
                }
                Err(e) => return Err(e),
            };

        log::info!(
            "Branch {} already exists (branch at {})",
            name,
            crate::vcs::full_branch_url(&existing_branch)
        );

        let mut open_proposals = vec![];
        // If there is an open or rejected merge proposal, resume that.
        let mut merged_proposals = vec![];
        for mp in forge.iter_proposals(
            &existing_branch,
            main_branch,
            crate::breezyshim::MergeProposalStatus::All,
        )? {
            if !mp.is_closed()? && !mp.is_merged()? {
                open_proposals.push(mp);
            } else {
                merged_proposals.push(mp);
            }
        }
        if !open_proposals.is_empty() {
            Ok((Some(existing_branch), Some(false), Some(open_proposals)))
        } else if let Some(first_proposal) = merged_proposals.first() {
            log::info!(
                "There is a proposal that has already been merged at {}.",
                first_proposal.url()?
            );
            Ok((None, Some(true), None))
        } else {
            // No related merge proposals found, but there is an existing
            // branch (perhaps for a different target branch?)
            if overwrite_unrelated {
                Ok((None, Some(true), None))
            } else {
                //TODO(jelmer): What to do in this case?
                Ok((None, Some(false), None))
            }
        }
    })
}
