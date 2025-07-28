//! Publishing changes
pub use crate::proposal::DescriptionFormat;
use crate::vcs::open_branch;
use crate::Mode;
use breezyshim::branch::{GenericBranch, MemoryBranch, PyBranch};

use breezyshim::error::Error as BrzError;
use breezyshim::forge::MergeProposalStatus;
use breezyshim::merge::{MergeType, Merger};
use breezyshim::repository::Repository;
use breezyshim::{Branch, Forge, MergeProposal, RevisionId, Transport};

use std::collections::HashMap;

/// Options for publishing changes
#[derive(Default)]
pub struct PublishOptions<'a> {
    /// Mode of publishing
    pub mode: Mode,
    /// Name of the branch/proposal
    pub name: &'a str,
    /// Forge instance
    pub forge: Option<&'a Forge>,
    /// Whether to allow creating proposals
    pub allow_create_proposal: bool,
    /// Labels for the proposal
    pub labels: Vec<String>,
    /// Whether to overwrite existing branches
    pub overwrite_existing: bool,
    /// Existing proposal to update
    pub existing_proposal: Option<MergeProposal>,
    /// Reviewers for the proposal
    pub reviewers: Vec<String>,
    /// Tags to push
    pub tags: Option<HashMap<String, RevisionId>>,
    /// Owner of derived branch
    pub derived_owner: Option<&'a str>,
    /// Allow collaboration
    pub allow_collaboration: bool,
    /// Stop at this revision
    pub stop_revision: Option<&'a RevisionId>,
    /// Enable auto-merge
    pub auto_merge: bool,
    /// Mark as work in progress
    pub work_in_progress: bool,
}

impl<'a> PublishOptions<'a> {
    /// Create new publish options
    pub fn new(name: &'a str, mode: Mode) -> Self {
        Self {
            name,
            mode,
            allow_create_proposal: true,
            ..Default::default()
        }
    }
}

fn _tag_selector_from_tags(
    tags: std::collections::HashMap<String, RevisionId>,
) -> impl Fn(String) -> bool {
    move |tag| tags.contains_key(tag.as_str())
}

fn _tag_selector_from_tags_ref(
    tags: &std::collections::HashMap<String, RevisionId>,
) -> Box<dyn Fn(String) -> bool + '_> {
    Box::new(move |tag| tags.contains_key(tag.as_str()))
}

/// Push derived changes
pub fn push_derived_changes(
    local_branch: &dyn PyBranch,
    main_branch: &dyn PyBranch,
    forge: &Forge,
    name: &str,
    overwrite_existing: Option<bool>,
    owner: Option<&str>,
    tags: Option<std::collections::HashMap<String, RevisionId>>,
    stop_revision: Option<&RevisionId>,
) -> Result<(Box<dyn Branch>, url::Url), BrzError> {
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

/// Push result
pub fn push_result(
    local_branch: &GenericBranch,
    remote_branch: &GenericBranch,
    additional_colocated_branches: Option<&[(String, String)]>,
    tags: Option<&std::collections::HashMap<String, RevisionId>>,
    stop_revision: Option<&RevisionId>,
) -> Result<(), BrzError> {
    let tag_selector = if let Some(tags) = tags {
        _tag_selector_from_tags(tags.clone())
    } else {
        _tag_selector_from_tags(std::collections::HashMap::new())
    };
    local_branch.push(remote_branch, false, stop_revision, Some(Box::new(tag_selector)))?;

    if let Some(branches) = additional_colocated_branches {
        for (from_branch_name, to_branch_name) in branches {
            match local_branch
                .controldir()
                .open_branch(Some(from_branch_name.as_str()))
            {
                Ok(branch) => {
                    let tag_selector = if let Some(tags) = tags {
                        Box::new(_tag_selector_from_tags(tags.clone()))
                    } else {
                        Box::new(_tag_selector_from_tags(std::collections::HashMap::new()))
                    };

                    // Use the remote branch's controldir for pushing colocated branches
                    // This is the correct approach since we're pushing to the same repository
                    let target_controldir = remote_branch.controldir();
                    target_controldir.push_branch(
                        branch.as_ref(),
                        Some(to_branch_name.as_str()),
                        None,
                        Some(false),
                        Some(tag_selector),
                    )?;
                }
                Err(BrzError::NotBranchError(..)) => {}
                Err(e) => return Err(e),
            };
        }
    }
    Ok(())
}

/// Push changes to a branch.
///
/// # Arguments
/// * `local_branch` - Local branch to push
/// * `main_branch` - Main branch to push to
/// * `forge` - Forge to push to
/// * `possible_transports` - Possible transports to use
/// * `additional_colocated_branches` - Additional colocated branches to push
/// * `tags` - Tags to push
/// * `stop_revision` - Revision to stop pushing at
pub fn push_changes(
    local_branch: &GenericBranch,
    main_branch: &GenericBranch,
    forge: Option<&Forge>,
    possible_transports: Option<&mut Vec<Transport>>,
    additional_colocated_branches: Option<Vec<(String, String)>>,
    tags: Option<std::collections::HashMap<String, RevisionId>>,
    stop_revision: Option<&RevisionId>,
) -> Result<(), Error> {
    let push_url = if let Some(forge) = forge {
        forge.get_push_url(main_branch)
    } else {
        main_branch.get_user_url()
    };
    log::info!("pushing to {}", push_url);
    let target_branch = open_branch(&push_url, possible_transports, None, None)?;
    push_result(
        local_branch,
        &target_branch,
        additional_colocated_branches.as_deref(),
        tags.as_ref(),
        stop_revision,
    )
    .map_err(Into::into)
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
#[allow(dead_code)]
pub fn find_existing_proposed(
    main_branch: &GenericBranch,
    forge: &Forge,
    name: &str,
    overwrite_unrelated: bool,
    owner: Option<&str>,
    _preferred_schemes: Option<&[&str]>,
) -> Result<
    (
        Option<GenericBranch>,
        Option<bool>,
        Option<Vec<MergeProposal>>,
    ),
    BrzError,
> {
    // GenericBranch implements PyBranch, so we can use it with forge operations
    match forge.get_derived_branch(main_branch, name, owner, None) {
        Ok(derived_branch) => {
            // Found existing derived branch
            let proposals =
                forge.iter_proposals(main_branch, main_branch, MergeProposalStatus::Open)?;

            // Convert derived_branch from Box<dyn Branch> to GenericBranch
            let derived_branch =
                crate::vcs::open_branch(&derived_branch.get_user_url(), None, None, None).map_err(
                    |e| match e {
                        crate::vcs::BranchOpenError::Missing { description, .. } => {
                            BrzError::NotBranchError(description, None)
                        }
                        crate::vcs::BranchOpenError::Unavailable { description, .. }
                        | crate::vcs::BranchOpenError::TemporarilyUnavailable {
                            description, ..
                        } => BrzError::ConnectionError(description),
                        crate::vcs::BranchOpenError::Unsupported { description, .. } => {
                            BrzError::UnknownFormat(description)
                        }
                        crate::vcs::BranchOpenError::RateLimited { description, .. } => {
                            BrzError::ConnectionError(description)
                        }
                        crate::vcs::BranchOpenError::Other(description) => {
                            BrzError::UnknownFormat(description)
                        }
                    },
                )?;

            // Filter proposals that are for our derived branch
            let derived_url = derived_branch.get_user_url();
            let matching_proposals: Vec<MergeProposal> = proposals
                .into_iter()
                .filter(|proposal| {
                    if let Ok(Some(source_url)) = proposal.get_source_branch_url() {
                        source_url == derived_url
                    } else {
                        false
                    }
                })
                .collect();

            Ok((
                Some(derived_branch),
                Some(!overwrite_unrelated),
                Some(matching_proposals),
            ))
        }
        Err(BrzError::NotBranchError(..)) => {
            // No existing derived branch found
            Ok((None, None, None))
        }
        Err(e) => Err(e),
    }
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
/// * `work_in_progress` - Mark merge proposal as work in progress
/// * `preferred_schemes` - List of preferred schemes
/// * `overwrite_unrelated` - Whether to overwrite existing (but unrelated) branches
///
/// # Returns
///   Tuple with (proposal, is_new)
pub fn propose_changes(
    local_branch: &GenericBranch,
    main_branch: &GenericBranch,
    forge: &Forge,
    name: &str,
    mp_description: &str,
    resume_branch: Option<&GenericBranch>,
    mut resume_proposal: Option<MergeProposal>,
    overwrite_existing: Option<bool>,
    labels: Option<Vec<String>>,
    commit_message: Option<&str>,
    title: Option<&str>,
    additional_colocated_branches: Option<Vec<(String, String)>>,
    allow_empty: Option<bool>,
    _reviewers: Option<Vec<String>>,
    tags: Option<HashMap<String, RevisionId>>,
    owner: Option<&str>,
    stop_revision: Option<&RevisionId>,
    _allow_collaboration: Option<bool>,
    auto_merge: Option<bool>,
    work_in_progress: Option<bool>,
) -> Result<(MergeProposal, bool), Error> {
    if !allow_empty.unwrap_or(false)
        && check_proposal_diff_empty(local_branch, main_branch, stop_revision)?
    {
        return Err(Error::EmptyMergeProposal);
    }
    let overwrite_existing = overwrite_existing.unwrap_or(true);

    // Handle pushing to remote branch
    if let Some(resume_branch) = resume_branch {
        // Push changes to the existing branch
        let tag_selector = tags.as_ref().map(|tag_map| {
            let tag_names: Vec<String> = tag_map.keys().cloned().collect();
            Box::new(move |tag: String| tag_names.contains(&tag)) as Box<dyn Fn(String) -> bool>
        });
        local_branch.push(
            resume_branch,
            overwrite_existing,
            stop_revision,
            tag_selector,
        )?;
    } else {
        let tag_selector = tags.as_ref().map(|tag_map| {
            let tag_names: Vec<String> = tag_map.keys().cloned().collect();
            Box::new(move |tag: String| tag_names.contains(&tag)) as Box<dyn Fn(String) -> bool>
        });
        let (_derived_branch, _public_branch_url) = forge.publish_derived(
            local_branch,
            main_branch,
            name,
            Some(overwrite_existing),
            owner,
            stop_revision,
            tag_selector,
        )?;
    }
    // Push additional colocated branches - GenericBranch implements PyBranch
    for (from_branch_name, to_branch_name) in additional_colocated_branches.unwrap_or_default() {
        match local_branch
            .controldir()
            .open_branch(Some(from_branch_name.as_str()))
        {
            Ok(from_branch) => {
                let tag_selector = tags.as_ref().map(|tag_map| {
                    let tag_names: Vec<String> = tag_map.keys().cloned().collect();
                    Box::new(move |tag: String| tag_names.contains(&tag))
                        as Box<dyn Fn(String) -> bool>
                });

                // Get the target controldir (either resume_branch or the derived branch we just pushed)
                let target_controldir = if let Some(resume_branch) = resume_branch {
                    resume_branch.controldir()
                } else {
                    // We need to get the derived branch controldir from forge
                    // For now, try to open from main_branch controldir with derived name
                    main_branch.controldir()
                };

                match target_controldir.push_branch(
                    from_branch.as_ref(),
                    Some(to_branch_name.as_str()),
                    None,        // stop_revision
                    Some(false), // overwrite
                    tag_selector,
                ) {
                    Ok(_) => log::debug!(
                        "Successfully pushed colocated branch {} -> {}",
                        from_branch_name,
                        to_branch_name
                    ),
                    Err(e) => log::warn!(
                        "Failed to push colocated branch {} -> {}: {}",
                        from_branch_name,
                        to_branch_name,
                        e
                    ),
                }
            }
            Err(BrzError::NotBranchError(..)) => {
                log::debug!("Colocated branch {} not found, skipping", from_branch_name);
            }
            Err(e) => {
                log::warn!(
                    "Error accessing colocated branch {}: {}",
                    from_branch_name,
                    e
                );
            }
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
                Err(BrzError::UnsupportedOperation(..)) => (),
                Err(e) => return Err(e.into()),
            }
        }
        if resume_proposal.get_commit_message()?.as_deref() != commit_message {
            match resume_proposal.set_commit_message(commit_message) {
                Ok(_) => (),
                Err(BrzError::UnsupportedOperation(..)) => (),
                Err(e) => return Err(e.into()),
            }
        }
        if resume_proposal.get_title()?.as_deref() != title {
            match resume_proposal.set_title(title) {
                Ok(_) => (),
                Err(BrzError::UnsupportedOperation(..)) => (),
                Err(e) => return Err(e.into()),
            }
        }
        Ok((resume_proposal, false))
    } else {
        // Create new proposal - GenericBranch implements PyBranch so we can use it
        let mut proposer = forge.get_proposer(main_branch, local_branch)?;
        proposer = proposer.description(mp_description);
        if let Some(title) = title {
            proposer = proposer.title(title);
        }
        if let Some(commit_message) = commit_message {
            proposer = proposer.commit_message(commit_message);
        }
        if let Some(labels) = labels {
            let label_refs: Vec<&str> = labels.iter().map(|s| s.as_str()).collect();
            proposer = proposer.labels(&label_refs);
        }
        if let Some(reviewers) = _reviewers {
            let reviewer_refs: Vec<&str> = reviewers.iter().map(|s| s.as_str()).collect();
            proposer = proposer.reviewers(&reviewer_refs);
        }
        if let Some(allow_collaboration) = _allow_collaboration {
            proposer = proposer.allow_collaboration(allow_collaboration);
        }
        if let Some(work_in_progress) = work_in_progress {
            proposer = proposer.work_in_progress(work_in_progress);
        }
        let proposal = proposer.build()?;

        // Set auto_merge if requested
        if let Some(auto_merge) = auto_merge {
            if auto_merge {
                // Call merge with auto=true to enable auto-merge
                match proposal.merge(true) {
                    Ok(_) => {}
                    Err(BrzError::UnsupportedOperation(..)) => {
                        // Some forges don't support auto-merge
                        log::debug!("Auto-merge not supported by this forge");
                    }
                    Err(e) => return Err(e.into()),
                }
            }
        }

        Ok((proposal, true))
    }
}

#[derive(Debug)]
/// Error type for publishing
pub enum Error {
    /// Diverged branches
    DivergedBranches(),

    /// An unrelated branch existed
    UnrelatedBranchExists,

    /// Other vcs error
    Other(BrzError),

    /// Unsupported forge
    UnsupportedForge(url::Url),

    /// Forge login required
    ForgeLoginRequired,

    /// Insufficient changes for new proposal
    InsufficientChangesForNewProposal,

    /// Branch open error
    BranchOpenError(crate::vcs::BranchOpenError),

    /// Empty merge proposal
    EmptyMergeProposal,

    /// Permission denied
    PermissionDenied,

    /// No target branch
    NoTargetBranch,
}

impl From<BrzError> for Error {
    fn from(e: BrzError) -> Self {
        match e {
            BrzError::DivergedBranches => Error::DivergedBranches(),
            BrzError::NotBranchError(..) => Error::UnrelatedBranchExists,
            BrzError::PermissionDenied(..) => Error::PermissionDenied,
            BrzError::UnsupportedForge(s) => Error::UnsupportedForge(s),
            BrzError::ForgeLoginRequired => Error::ForgeLoginRequired,
            _ => Error::Other(e),
        }
    }
}

impl From<crate::vcs::BranchOpenError> for Error {
    fn from(e: crate::vcs::BranchOpenError) -> Self {
        Error::BranchOpenError(e)
    }
}

impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
        match self {
            Error::DivergedBranches() => write!(f, "Diverged branches"),
            Error::Other(e) => write!(f, "{}", e),
            Error::UnsupportedForge(u) => write!(f, "Unsupported forge: {}", u),
            Error::ForgeLoginRequired => write!(f, "Forge login required"),
            Error::BranchOpenError(e) => write!(f, "{}", e),
            Error::EmptyMergeProposal => write!(f, "Empty merge proposal"),
            Error::PermissionDenied => write!(f, "Permission denied"),
            Error::UnrelatedBranchExists => write!(f, "Unrelated branch exists"),
            Error::InsufficientChangesForNewProposal => {
                write!(f, "Insufficient changes for new proposal")
            }
            Error::NoTargetBranch => write!(f, "No target branch"),
        }
    }
}

#[cfg(feature = "pyo3")]
impl From<Error> for pyo3::PyErr {
    fn from(e: Error) -> Self {
        use pyo3::import_exception_bound;
        use pyo3::prelude::*;
        import_exception_bound!(breezy.errors, NotBranchError);
        import_exception_bound!(breezy.errors, UnsupportedOperation);
        import_exception_bound!(breezy.errors, MergeProposalExists);
        import_exception_bound!(breezy.errors, PermissionDenied);
        import_exception_bound!(breezy.errors, DivergedBranches);
        import_exception_bound!(breezy.forge, UnsupportedForge);
        import_exception_bound!(breezy.forge, ForgeLoginRequired);
        import_exception_bound!(silver_platter, EmptyMergeProposal);
        import_exception_bound!(silver_platter, UnrelatedBranchExists);
        import_exception_bound!(silver_platter, InsufficientChangesForNewProposal);
        import_exception_bound!(silver_platter, NoTargetBranch);

        match e {
            Error::DivergedBranches() => PyErr::new::<DivergedBranches, _>("DivergedBranches"),
            Error::Other(e) => e.into(),
            Error::BranchOpenError(e) => e.into(),
            Error::UnsupportedForge(u) => PyErr::new::<UnsupportedForge, _>(u.to_string()),
            Error::ForgeLoginRequired => PyErr::new::<ForgeLoginRequired, _>("ForgeLoginRequired"),
            Error::UnrelatedBranchExists => {
                PyErr::new::<UnrelatedBranchExists, _>("UnrelatedBranchExists")
            }
            Error::PermissionDenied => PyErr::new::<PermissionDenied, _>("PermissionDenied"),
            Error::EmptyMergeProposal => PyErr::new::<EmptyMergeProposal, _>("EmptyMergeProposal"),
            Error::InsufficientChangesForNewProposal => {
                PyErr::new::<InsufficientChangesForNewProposal, _>(
                    "InsufficientChangesForNewProposal",
                )
            }
            Error::NoTargetBranch => PyErr::new::<NoTargetBranch, _>(()),
        }
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
/// * `auto_merge` - Enable merging once CI passes
/// * `work_in_progress` - Mark merge proposal as work in progress
pub fn publish_changes(
    local_branch: &GenericBranch,
    main_branch: &GenericBranch,
    resume_branch: Option<&GenericBranch>,
    mut mode: Mode,
    name: &str,
    get_proposal_description: impl FnOnce(DescriptionFormat, Option<&MergeProposal>) -> String,
    get_proposal_commit_message: Option<impl FnOnce(Option<&MergeProposal>) -> Option<String>>,
    get_proposal_title: Option<impl FnOnce(Option<&MergeProposal>) -> Option<String>>,
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
    auto_merge: Option<bool>,
    work_in_progress: Option<bool>,
) -> Result<PublishResult, Error> {
    let stop_revision_owned;
    let stop_revision = match stop_revision {
        Some(r) => r,
        None => {
            stop_revision_owned = local_branch.last_revision();
            &stop_revision_owned
        }
    };
    let allow_create_proposal = allow_create_proposal.unwrap_or(true);

    // Only modes that don't require forge operations can work without a forge
    if forge.is_none() && mode != Mode::Push && mode != Mode::AttemptPush {
        return Err(Error::UnsupportedForge(main_branch.get_user_url()));
    }

    // forge will be cloned only when needed for the result

    if *stop_revision == main_branch.last_revision() {
        if let Some(existing_proposal) = existing_proposal.as_ref() {
            log::info!("closing existing merge proposal - no new revisions");
            existing_proposal.close()?;
        }
        return Ok(PublishResult {
            mode,
            target_branch: main_branch.get_user_url(),
            forge: forge.cloned(),
            proposal: existing_proposal,
            is_new: Some(false),
        });
    }

    if let Some(resume_branch) = resume_branch {
        if resume_branch.last_revision() == *stop_revision {
            // No new revisions added on this iteration, but changes since main
            // branch. We may not have gotten round to updating/creating the
            // merge proposal last time.
            log::info!("No changes added; making sure merge proposal is up to date.");
        }
    }
    let write_lock = main_branch.lock_write()?;
    match mode {
        Mode::PushDerived => {
            let forge_ref = forge.as_ref().unwrap(); // We checked above that forge is required for this mode
            let (_remote_branch, _public_url) = push_derived_changes(
                local_branch,
                main_branch,
                forge_ref,
                name,
                overwrite_existing,
                derived_owner,
                tags,
                Some(&stop_revision),
            )?;
            return Ok(PublishResult {
                mode,
                target_branch: main_branch.get_user_url(),
                forge: forge.cloned(),
                proposal: None,
                is_new: None,
            });
        }
        Mode::Push | Mode::AttemptPush => {
            let read_lock = local_branch.lock_read()?;
            // breezy would do this check too, but we want to be *really* sure.
            let graph = local_branch.repository().get_graph();
            if !graph.is_ancestor(&main_branch.last_revision(), &stop_revision)? {
                return Err(Error::DivergedBranches());
            }
            std::mem::drop(read_lock);
            match push_changes(
                local_branch,
                main_branch,
                forge,
                None,
                None,
                tags.clone(),
                Some(&stop_revision),
            ) {
                Err(e @ Error::PermissionDenied) => {
                    if mode == Mode::AttemptPush {
                        log::info!("push access denied, falling back to propose");
                        mode = Mode::Propose;
                    } else {
                        log::info!("permission denied during push");
                        return Err(e);
                    }
                }
                Ok(_) => {
                    return Ok(PublishResult {
                        proposal: None,
                        mode,
                        target_branch: main_branch.get_user_url(),
                        forge: forge.cloned(),
                        is_new: None,
                    });
                }
                Err(e) => {
                    return Err(e);
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

    let forge = forge.ok_or(Error::UnsupportedForge(main_branch.get_user_url()))?;

    let mp_description = get_proposal_description(
        forge.merge_proposal_description_format().parse().unwrap(),
        if resume_branch.is_some() {
            existing_proposal.as_ref()
        } else {
            None
        },
    );
    let commit_message = if let Some(get_proposal_commit_message) = get_proposal_commit_message {
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
    let title = if let Some(title) = title {
        Some(title)
    } else {
        match breezyshim::forge::determine_title(mp_description.as_str()) {
            Ok(title) => Some(title),
            Err(e) => {
                log::warn!("Failed to determine title from description: {}", e);
                None
            }
        }
    };
    let (proposal, is_new) = propose_changes(
        local_branch,
        main_branch,
        &forge, // We checked above that forge is required for Propose mode
        name,
        mp_description.as_str(),
        resume_branch,
        existing_proposal,
        overwrite_existing,
        labels,
        commit_message.as_deref(),
        title.as_deref(),
        None,
        None,
        reviewers,
        tags,
        derived_owner,
        Some(&stop_revision),
        allow_collaboration,
        auto_merge,
        work_in_progress,
    )?;
    std::mem::drop(write_lock);
    Ok(PublishResult {
        mode,
        proposal: Some(proposal),
        is_new: Some(is_new),
        target_branch: main_branch.get_user_url(),
        forge: Some(forge.clone()),
    })
}

/// Publish a set of changes using builder pattern.
///
/// This is a cleaner alternative to publish_changes with fewer parameters.
pub fn publish_changes_with_options(
    local_branch: &GenericBranch,
    main_branch: &GenericBranch,
    resume_branch: Option<&GenericBranch>,
    options: PublishOptions,
    get_proposal_description: impl FnOnce(DescriptionFormat, Option<&MergeProposal>) -> String,
    get_proposal_commit_message: Option<impl FnOnce(Option<&MergeProposal>) -> Option<String>>,
    get_proposal_title: Option<impl FnOnce(Option<&MergeProposal>) -> Option<String>>,
) -> Result<PublishResult, Error> {
    publish_changes(
        local_branch,
        main_branch,
        resume_branch,
        options.mode,
        options.name,
        get_proposal_description,
        get_proposal_commit_message,
        get_proposal_title,
        options.forge,
        Some(options.allow_create_proposal),
        Some(options.labels),
        Some(options.overwrite_existing),
        options.existing_proposal,
        Some(options.reviewers),
        options.tags,
        options.derived_owner,
        Some(options.allow_collaboration),
        options.stop_revision,
        Some(options.auto_merge),
        Some(options.work_in_progress),
    )
}

/// Publish result
pub struct PublishResult {
    /// Publish mode
    pub mode: Mode,

    /// Merge proposal
    pub proposal: Option<MergeProposal>,

    /// Whether the proposal is new
    pub is_new: Option<bool>,

    /// Target branch
    pub target_branch: url::Url,

    /// Forge
    pub forge: Option<Forge>,
}

/// Check whether a proposal has any changes.
pub fn check_proposal_diff_empty(
    other_branch: &dyn PyBranch,
    main_branch: &dyn PyBranch,
    stop_revision: Option<&RevisionId>,
) -> Result<bool, BrzError> {
    let stop_revision_owned;
    let stop_revision = match stop_revision {
        Some(rev) => rev,
        None => {
            stop_revision_owned = other_branch.last_revision();
            &stop_revision_owned
        }
    };
    let main_revid = main_branch.last_revision();
    let other_repository = other_branch.repository();
    other_repository.fetch(&main_branch.repository(), Some(&main_revid))?;

    let lock = other_branch.lock_read();
    let main_tree = other_repository.revision_tree(&main_revid)?;
    let revision_graph = other_repository.get_graph();
    let tree_branch = MemoryBranch::new(&other_repository, None, &main_revid);
    let mut merger = Merger::new(&tree_branch, &main_tree, &revision_graph);
    merger.set_other_revision(&stop_revision, other_branch)?;
    if merger.find_base()?.is_none() {
        merger.set_base_revision(&RevisionId::null(), other_branch)?;
    }
    merger.set_merge_type(MergeType::Merge3);
    let tree_merger = merger.make_merger()?;
    let tt = tree_merger.make_preview_transform()?;
    let mut changes = tt.iter_changes()?;
    std::mem::drop(lock);
    Ok(!changes.any(|_| true))
}

/// Enable tag pushing for a branch
pub fn enable_tag_pushing(branch: &dyn Branch) -> Result<(), BrzError> {
    let config = branch.get_config();
    config.set_user_option("branch.fetch_tags", true)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use breezyshim::tree::MutableTree;
    use breezyshim::WorkingTree;

    #[test]
    fn test_no_new_commits() {
        use breezyshim::controldir::create_standalone_workingtree;
        use breezyshim::controldir::ControlDirFormat;
        let td = tempfile::tempdir().unwrap();
        let orig = td.path().join("orig");
        let tree = create_standalone_workingtree(&orig, &ControlDirFormat::default()).unwrap();

        std::fs::write(orig.join("a"), "a").unwrap();
        MutableTree::add(&tree, &[std::path::Path::new("a")]).unwrap();
        tree.build_commit().message("blah").commit().unwrap();

        let proposal_url = url::Url::from_file_path(orig.join("proposal")).unwrap();

        let proposal = tree
            .controldir()
            .sprout(proposal_url, None, None, None, None)
            .unwrap()
            .open_branch(None)
            .unwrap();
        assert!(check_proposal_diff_empty(proposal.as_ref(), &tree.branch(), None).unwrap());
    }

    #[test]
    fn test_no_op_commits() {
        use breezyshim::controldir::create_standalone_workingtree;
        use breezyshim::controldir::ControlDirFormat;
        let td = tempfile::tempdir().unwrap();
        let orig = td.path().join("orig");
        let tree = create_standalone_workingtree(&orig, &ControlDirFormat::default()).unwrap();

        std::fs::write(orig.join("a"), "a").unwrap();
        MutableTree::add(&tree, &[std::path::Path::new("a")]).unwrap();
        tree.build_commit().message("blah").commit().unwrap();

        let proposal_url = url::Url::from_file_path(orig.join("proposal")).unwrap();

        let proposal = tree
            .controldir()
            .sprout(proposal_url, None, None, None, None)
            .unwrap()
            .open_workingtree()
            .unwrap();
        proposal
            .build_commit()
            .message("another commit that is pointless")
            .commit()
            .unwrap();

        assert!(check_proposal_diff_empty(&proposal.branch(), &tree.branch(), None).unwrap());
    }

    #[test]
    fn test_indep() {
        use breezyshim::bazaar::tree::MutableInventoryTree;
        use breezyshim::bazaar::FileId;
        use breezyshim::controldir::create_standalone_workingtree;
        use breezyshim::controldir::ControlDirFormat;
        let td = tempfile::tempdir().unwrap();
        let orig = td.path().join("orig");
        let tree = create_standalone_workingtree(&orig, &ControlDirFormat::default()).unwrap();

        std::fs::write(orig.join("a"), "a").unwrap();
        MutableTree::add(&tree, &[std::path::Path::new("a")]).unwrap();
        tree.build_commit().message("blah").commit().unwrap();

        std::fs::write(orig.join("b"), "b").unwrap();
        std::fs::write(orig.join("c"), "c").unwrap();
        MutableTree::add(
            &tree,
            &[std::path::Path::new("b"), std::path::Path::new("c")],
        )
        .unwrap();
        tree.build_commit().message("independent").commit().unwrap();

        let proposal_path = orig.join("proposal");
        let proposal_url = url::Url::from_file_path(proposal_path.as_path()).unwrap();

        let proposal = tree
            .controldir()
            .sprout(proposal_url, None, None, None, None)
            .unwrap()
            .open_workingtree()
            .unwrap();

        assert!(proposal_path.exists());

        std::fs::write(proposal_path.join("b"), "b").unwrap();

        if proposal.supports_setting_file_ids() {
            MutableInventoryTree::add(
                &proposal,
                &[std::path::Path::new("b")],
                &[FileId::from("b")],
            )
            .unwrap();
        } else {
            MutableTree::add(&proposal, &[std::path::Path::new("b")]).unwrap();
        }
        proposal
            .build_commit()
            .message("not pointless")
            .commit()
            .unwrap();

        assert!(check_proposal_diff_empty(&proposal.branch(), &tree.branch(), None).unwrap());

        std::mem::drop(td);
    }

    #[test]
    fn test_changes() {
        use breezyshim::controldir::create_standalone_workingtree;
        use breezyshim::controldir::ControlDirFormat;
        let td = tempfile::tempdir().unwrap();
        let orig = td.path().join("orig");
        let tree = create_standalone_workingtree(&orig, &ControlDirFormat::default()).unwrap();
        std::fs::write(orig.join("a"), "a").unwrap();
        MutableTree::add(&tree, &[std::path::Path::new("a")]).unwrap();
        tree.build_commit().message("blah").commit().unwrap();

        let proposal_url = url::Url::from_file_path(td.path().join("proposal")).unwrap();
        let proposal_tree = tree
            .controldir()
            .sprout(proposal_url, None, None, None, None)
            .unwrap()
            .open_workingtree()
            .unwrap();
        std::fs::write(proposal_tree.basedir().join("b"), "b").unwrap();
        MutableTree::add(&proposal_tree, &[std::path::Path::new("b")]).unwrap();
        proposal_tree
            .build_commit()
            .message("not pointless")
            .commit()
            .unwrap();

        assert!(!check_proposal_diff_empty(&proposal_tree.branch(), &tree.branch(), None).unwrap());
    }

    #[test]
    fn test_push_result() {
        use breezyshim::controldir::{
            create_branch_convenience, create_standalone_workingtree, ControlDirFormat,
        };
        let td = tempfile::tempdir().unwrap();
        let target_path = td.path().join("target");
        let source_path = td.path().join("source");
        let target_url = url::Url::from_file_path(target_path).unwrap();
        let _target_branch =
            create_branch_convenience(&target_url, None, &ControlDirFormat::default()).unwrap();
        let target = crate::vcs::open_branch(&target_url, None, None, None).unwrap();
        let source =
            create_standalone_workingtree(&source_path, &ControlDirFormat::default()).unwrap();
        let revid = source
            .build_commit()
            .message("Some change")
            .commit()
            .unwrap();
        push_result(&source.branch(), &target, None, None, None).unwrap();
        assert_eq!(target.last_revision(), revid);
    }
}
