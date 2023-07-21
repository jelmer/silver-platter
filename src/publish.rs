use crate::vcs::open_branch;
use crate::Mode;
use breezyshim::{Branch, Forge, MergeProposal, RevisionId, Transport};
use pyo3::exceptions::PyPermissionError;
use pyo3::import_exception;
use pyo3::prelude::*;
use std::collections::HashMap;

import_exception!(breezy.errors, NotBranchError);
import_exception!(breezy.errors, UnsupportedOperation);
import_exception!(breezy.errors, MergeProposalExists);
import_exception!(breezy.errors, PermissionDenied);

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
        Some(Box::new(_tag_selector_from_tags(tags))),
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
        local_branch.push(remote_branch, false, stop_revision, Some(tag_selector))?;

        for (from_branch_name, to_branch_name) in additional_colocated_branches.unwrap_or_default()
        {
            match local_branch
                .controldir()
                .open_branch(Some(from_branch_name.as_str()))
            {
                Ok(branch) => {
                    let tag_selector =
                        Box::new(_tag_selector_from_tags(tags.clone().unwrap_or_default()));
                    remote_branch.controldir().push_branch(
                        &branch,
                        Some(to_branch_name.as_str()),
                        Some(false),
                        Some(tag_selector),
                    )?;
                }
                Err(breezyshim::branch::BranchOpenError::NotBranchError(_)) => {}
                Err(e) => return Err(e.into()),
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
    push_result(
        local_branch,
        &target_branch,
        additional_colocated_branches,
        tags,
        stop_revision,
    )
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
            breezyshim::MergeProposalStatus::All,
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

/// Create or update a merge proposal.
///
/// # Arguments
///
/// * `local_branch` - Local branch with changes to propose
/// * `main_branch` - Target branch to propose against
/// * `forge` - Associated forge for main branch
/// * `mp_description` - Merge proposal description
/// * `resume_branch` - Existing derived branch
/// * `resume_proposal` - Existing merge proposal to resume
/// * `overwrite_existing` - Whether to overwrite any other existing branch
/// * `labels` - Labels to add
/// * `commit_message` - Optional commit message
/// * `title` - Optional title
/// * `additional_colocated_branches` - Additional colocated branches to propose
/// * `allow_empty` - Whether to allow empty merge proposals
/// * `reviewers` - List of reviewers
/// * `tags` - Tags to push (None for default behaviour)
/// * `owner` - Derived branch owner
/// * `stop_revision` - Revision to stop pushing at
/// * `allow_collaboration` - Allow target branch owners to modify source branch
/// * `auto_merge` - Enable merging once CI passes
/// * `preferred_schemes` - List of preferred schemes
/// * `overwrite_unrelated` - Whether to overwrite existing (but unrelated) branches
///
/// # Returns
///   Tuple with (proposal, is_new)
pub fn propose_changes(
    local_branch: &Branch,
    main_branch: &Branch,
    forge: &Forge,
    name: &str,
    mp_description: &str,
    resume_branch: Option<&Branch>,
    mut resume_proposal: Option<MergeProposal>,
    overwrite_existing: Option<bool>,
    labels: Option<Vec<String>>,
    commit_message: Option<&str>,
    title: Option<&str>,
    additional_colocated_branches: Option<Vec<(String, String)>>,
    allow_empty: Option<bool>,
    reviewers: Option<Vec<String>>,
    tags: Option<HashMap<String, RevisionId>>,
    owner: Option<&str>,
    stop_revision: Option<&RevisionId>,
    allow_collaboration: Option<bool>,
    auto_merge: Option<bool>,
) -> PyResult<(MergeProposal, bool)> {
    Python::with_gil(|py| {
        if !allow_empty.unwrap_or(false) {
            check_proposal_diff(local_branch, main_branch, stop_revision)?;
        }
        let overwrite_existing = overwrite_existing.unwrap_or(true);
        let remote_branch = if let Some(resume_branch) = resume_branch {
            local_branch.push(
                resume_branch,
                overwrite_existing,
                stop_revision,
                tags.as_ref().map(|ts| {
                    Box::new(_tag_selector_from_tags(ts.clone())) as Box<dyn Fn(String) -> bool>
                }),
            )?;
            std::borrow::Cow::Borrowed(resume_branch)
        } else {
            std::borrow::Cow::Owned(
                forge
                    .publish_derived(
                        local_branch,
                        main_branch,
                        name,
                        Some(overwrite_existing),
                        owner,
                        stop_revision,
                        tags.clone().map(|ts| {
                            Box::new(_tag_selector_from_tags(ts)) as Box<dyn Fn(String) -> bool>
                        }),
                    )?
                    .0,
            )
        };
        for (from_branch_name, to_branch_name) in additional_colocated_branches.unwrap_or_default()
        {
            match local_branch
                .controldir()
                .open_branch(Some(from_branch_name.as_str()))
            {
                Ok(b) => {
                    remote_branch.controldir().push_branch(
                        &b,
                        Some(to_branch_name.as_str()),
                        Some(overwrite_existing),
                        tags.clone().map(|ts| {
                            Box::new(_tag_selector_from_tags(ts)) as Box<dyn Fn(String) -> bool>
                        }),
                    )?;
                }
                Err(breezyshim::branch::BranchOpenError::NotBranchError(_)) => {}
                Err(e) => return Err(e.into()),
            }
        }
        if let Some(mp) = resume_proposal.as_ref() {
            if mp.is_closed()? {
                match mp.reopen() {
                    Ok(_) => {}
                    Err(e) => {
                        log::info!(
                            "Reopening existing proposal failed ({}). Creating new proposal.",
                            e
                        );
                        resume_proposal = None;
                    }
                }
            }
        }
        if let Some(resume_proposal) = resume_proposal.take() {
            // Check that the proposal doesn't already has this description.
            // Setting the description (regardless of whether it changes)
            // causes Launchpad to send emails.
            if resume_proposal.get_description()?.as_deref() != Some(mp_description) {
                match resume_proposal.set_description(Some(mp_description)) {
                    Ok(_) => (),
                    Err(e) if e.is_instance_of::<UnsupportedOperation>(py) => (),
                    Err(e) => return Err(e),
                }
            }
            if resume_proposal.get_commit_message()?.as_deref() != commit_message {
                match resume_proposal.set_commit_message(commit_message) {
                    Ok(_) => (),
                    Err(e) if e.is_instance_of::<UnsupportedOperation>(py) => (),
                    Err(e) => return Err(e),
                }
            }
            if resume_proposal.get_title()?.as_deref() != title {
                match resume_proposal.set_title(title) {
                    Ok(_) => (),
                    Err(e) if e.is_instance_of::<UnsupportedOperation>(py) => (),
                    Err(e) => return Err(e),
                }
            }
            Ok((resume_proposal, false))
        } else {
            let mut proposal_builder = forge.get_proposer(&remote_branch, main_branch)?;
            if forge.supports_merge_proposal_commit_message() {
                if let Some(commit_message) = commit_message {
                    proposal_builder = proposal_builder.commit_message(commit_message);
                }
            }
            if forge.supports_merge_proposal_title() {
                if let Some(title) = title {
                    proposal_builder = proposal_builder.title(title);
                }
            }
            if let Some(allow_collaboration) = allow_collaboration {
                proposal_builder = proposal_builder.allow_collaboration(allow_collaboration);
            }
            proposal_builder = proposal_builder.description(mp_description);
            if let Some(labels) = labels {
                proposal_builder = proposal_builder.labels(
                    labels
                        .iter()
                        .map(|s| s.as_str())
                        .collect::<Vec<_>>()
                        .as_slice(),
                );
            }
            if let Some(reviewers) = reviewers {
                proposal_builder = proposal_builder.reviewers(
                    reviewers
                        .iter()
                        .map(|s| s.as_str())
                        .collect::<Vec<_>>()
                        .as_slice(),
                );
            }
            let mp: MergeProposal = match proposal_builder.build() {
                Ok(mp) => mp,
                Err(e) if e.is_instance_of::<MergeProposalExists>(py) => {
                    let proposal = e.value(py).getattr("existing_proposal")?;
                    if !proposal.is_none() {
                        MergeProposal::new(proposal.to_object(py))
                    } else {
                        return Err(e);
                    }
                }
                Err(e) if e.is_instance_of::<PermissionDenied>(py) => {
                    log::info!("Permission denied while trying to create proposal.");
                    return Err(e);
                }
                Err(e) => return Err(e),
            };
            if auto_merge.unwrap_or(false) {
                mp.merge(true)?;
            }
            Ok((mp, true))
        }
    })
}

pub fn check_proposal_diff(
    other_branch: &Branch,
    main_branch: &Branch,
    stop_revision: Option<&RevisionId>,
) -> PyResult<()> {
    Python::with_gil(|py| {
        let svp_publish = py.import("silver_platter.publish")?;
        let check_proposal_diff = svp_publish.getattr("check_proposal_diff")?;

        check_proposal_diff.call1((
            &other_branch.0,
            &main_branch.0,
            stop_revision.map(|r| r.as_bytes()),
        ))?;

        Ok(())
    })
}

#[derive(Debug)]
pub enum Error {
    DivergedBranches(),
    Other(PyErr),
    InsufficientChangesForNewProposal,
}

impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
        match self {
            Error::DivergedBranches() => write!(f, "Diverged branches"),
            Error::Other(e) => write!(f, "{}", e),
            Error::InsufficientChangesForNewProposal => {
                write!(f, "Insufficient changes for new proposal")
            }
        }
    }
}

impl From<PyErr> for Error {
    fn from(e: PyErr) -> Self {
        Error::Other(e)
    }
}

/// Publish a set of changes.
///
/// # Arguments
/// * `local_branch` - Local branch to publish
/// * `main_branch` - Main branch to publish to
/// * `resume_branch` - Branch to resume publishing from
/// * `mode` - Mode to use ('push', 'push-derived', 'propose')
/// * `name` - Branch name to push
/// * `get_proposal_description` - Function to retrieve proposal description
/// * `get_proposal_commit_message` - Function to retrieve proposal commit message
/// * `get_proposal_title` - Function to retrieve proposal title
/// * `forge` - Forge, if known
/// * `allow_create_proposal` - Whether to allow creating proposals
/// * `labels` - Labels to set for any merge proposals
/// * `overwrite_existing` - Whether to overwrite existing (but unrelated) branch
/// * `existing_proposal` - Existing proposal to update
/// * `reviewers` - List of reviewers for merge proposal
/// * `tags` - Tags to push (None for default behaviour)
/// * `derived_owner` - Name of any derived branch
/// * `allow_collaboration` - Whether to allow target branch owners to modify source branch.
pub fn publish_changes(
    local_branch: &Branch,
    main_branch: &Branch,
    resume_branch: Option<&Branch>,
    mut mode: Mode,
    name: &str,
    get_proposal_description: impl Fn(&str, Option<&MergeProposal>) -> String,
    get_proposal_commit_message: Option<impl Fn(Option<&MergeProposal>) -> Option<String>>,
    get_proposal_title: Option<impl Fn(Option<&MergeProposal>) -> Option<String>>,
    forge: Option<&Forge>,
    allow_create_proposal: Option<bool>,
    labels: Option<Vec<String>>,
    overwrite_existing: Option<bool>,
    existing_proposal: Option<MergeProposal>,
    reviewers: Option<Vec<String>>,
    tags: Option<HashMap<String, RevisionId>>,
    derived_owner: Option<&str>,
    allow_collaboration: Option<bool>,
    stop_revision: Option<&RevisionId>,
) -> Result<PublishResult, Error> {
    Python::with_gil(|py| {
        let stop_revision =
            stop_revision.map_or_else(|| local_branch.last_revision(), |r| r.clone());
        let allow_create_proposal = allow_create_proposal.unwrap_or(true);

        let forge = forge.map_or_else(|| breezyshim::forge::get_forge(main_branch), |f| f.clone());

        if stop_revision == main_branch.last_revision() {
            if let Some(existing_proposal) = existing_proposal.as_ref() {
                log::info!("closing existing merge proposal - no new revisions");
                existing_proposal.close()?;
            }
            return Ok(PublishResult {
                mode,
                target_branch: main_branch.clone(),
                forge: forge.clone(),
                proposal: existing_proposal,
                is_new: Some(false),
            });
        }

        if let Some(resume_branch) = resume_branch {
            if resume_branch.last_revision() == stop_revision {
                // No new revisions added on this iteration, but changes since main
                // branch. We may not have gotten round to updating/creating the
                // merge proposal last time.
                log::info!("No changes added; making sure merge proposal is up to date.");
            }
        }
        match mode {
            Mode::PushDerived => {
                let (remote_branch, public_url) = push_derived_changes(
                    local_branch,
                    main_branch,
                    &forge,
                    name,
                    overwrite_existing,
                    derived_owner,
                    tags,
                    Some(&stop_revision),
                )?;
                return Ok(PublishResult {
                    mode,
                    target_branch: main_branch.clone(),
                    forge: forge.clone(),
                    proposal: None,
                    is_new: None,
                });
            }
            Mode::Push | Mode::AttemptPush => {
                let read_lock = local_branch.lock_read()?;
                // breezy would do this check too, but we want to be *really* sure.
                let graph = local_branch.repository().get_graph();
                if !graph.is_ancestor(&main_branch.last_revision(), &stop_revision) {
                    return Err(Error::DivergedBranches());
                }
                std::mem::drop(read_lock);
                match push_changes(
                    local_branch,
                    main_branch,
                    Some(&forge),
                    None,
                    None,
                    tags.clone(),
                    Some(&stop_revision),
                ) {
                    Err(e) if e.is_instance_of::<PermissionDenied>(py) => {
                        if mode == Mode::AttemptPush {
                            log::info!("push access denied, falling back to propose");
                            mode = Mode::Propose;
                        } else {
                            log::info!("permission denied during push");
                            return Err(e.into());
                        }
                    }
                    Ok(o) => {
                        return Ok(PublishResult {
                            proposal: None,
                            mode,
                            target_branch: main_branch.clone(),
                            forge: forge.clone(),
                            is_new: None,
                        });
                    }
                    Err(e) => {
                        return Err(e.into());
                    }
                }
            }
            Mode::Bts => {
                unimplemented!();
            }
            Mode::Propose => { // Handled below
            }
        }

        assert_eq!(mode, Mode::Propose);
        if resume_branch.is_none() && !allow_create_proposal {
            return Err(Error::InsufficientChangesForNewProposal);
        }

        let mp_description = get_proposal_description(
            forge.merge_proposal_description_format().as_str(),
            if resume_branch.is_some() {
                existing_proposal.as_ref()
            } else {
                None
            },
        );
        let commit_message = if let Some(get_proposal_commit_message) = get_proposal_commit_message
        {
            get_proposal_commit_message(if resume_branch.is_some() {
                existing_proposal.as_ref()
            } else {
                None
            })
        } else {
            None
        };
        let title = if let Some(get_proposal_title) = get_proposal_title {
            get_proposal_title(if resume_branch.is_some() {
                existing_proposal.as_ref()
            } else {
                None
            })
        } else {
            None
        };
        let title =
            title.unwrap_or_else(|| breezyshim::forge::determine_title(mp_description.as_str()));
        let (proposal, is_new) = propose_changes(
            local_branch,
            main_branch,
            &forge,
            name,
            mp_description.as_str(),
            resume_branch,
            existing_proposal,
            overwrite_existing,
            labels,
            commit_message.as_deref(),
            Some(title.as_str()),
            None,
            None,
            reviewers,
            tags,
            derived_owner,
            Some(&stop_revision),
            allow_collaboration,
            None,
        )?;
        Ok(PublishResult {
            mode,
            proposal: Some(proposal),
            is_new: Some(is_new),
            target_branch: main_branch.clone(),
            forge: forge.clone(),
        })
    })
}

pub struct PublishResult {
    pub mode: Mode,
    pub proposal: Option<MergeProposal>,
    pub is_new: Option<bool>,
    pub target_branch: Branch,
    pub forge: Forge,
}
