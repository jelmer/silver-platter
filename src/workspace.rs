//! Workspace for preparing changes for publication
use crate::publish::{DescriptionFormat, Error as PublishError, PublishResult};
use breezyshim::branch::{Branch, GenericBranch};
use breezyshim::controldir::ControlDirFormat;
use breezyshim::error::Error as BrzError;
use breezyshim::forge::{Forge, MergeProposal};
use breezyshim::repository::Repository;
use breezyshim::tree::{MutableTree, RevisionTree, WorkingTree};
use breezyshim::ControlDir;
use breezyshim::RevisionId;
use std::collections::HashMap;
use std::path::PathBuf;

fn fetch_colocated(
    _controldir: &dyn ControlDir<
        Branch = GenericBranch,
        Repository = breezyshim::repository::GenericRepository,
        WorkingTree = breezyshim::workingtree::GenericWorkingTree,
    >,
    from_controldir: &dyn ControlDir<
        Branch = GenericBranch,
        Repository = breezyshim::repository::GenericRepository,
        WorkingTree = breezyshim::workingtree::GenericWorkingTree,
    >,
    additional_colocated_branches: &HashMap<&str, &str>,
) -> Result<(), BrzError> {
    log::debug!(
        "Fetching colocated branches: {:?}",
        additional_colocated_branches
    );

    for (from_branch_name, to_branch_name) in additional_colocated_branches.iter() {
        match from_controldir.open_branch(Some(from_branch_name)) {
            Ok(remote_colo_branch) => {
                // GenericBranch implements PyBranch, so we can push colocated branches
                match _controldir.push_branch(
                    remote_colo_branch.as_ref(),
                    Some(to_branch_name),
                    None,        // stop_revision
                    Some(false), // overwrite
                    None,        // tag_selector
                ) {
                    Ok(_) => {
                        log::debug!(
                            "Successfully fetched colocated branch {} -> {}",
                            from_branch_name,
                            to_branch_name
                        );
                    }
                    Err(e) => {
                        log::warn!(
                            "Failed to fetch colocated branch {} -> {}: {}",
                            from_branch_name,
                            to_branch_name,
                            e
                        );
                    }
                }
            }
            Err(BrzError::NotBranchError(..)) | Err(BrzError::NoColocatedBranchSupport) => {
                continue;
            }
            Err(e) => {
                return Err(e);
            }
        }
    }
    Ok(())
}

#[derive(Debug)]
/// An error that can occur when working with a workspace
pub enum Error {
    /// An error from the Breezy shim
    BrzError(BrzError),

    /// An I/O error
    IOError(std::io::Error),

    /// Unknown format was specified
    UnknownFormat(String),

    /// Permission denied
    PermissionDenied(Option<String>),

    /// Other error
    Other(String),
}

impl From<BrzError> for Error {
    fn from(e: BrzError) -> Self {
        match e {
            BrzError::UnknownFormat(n) => Error::UnknownFormat(n),
            BrzError::AlreadyControlDir(_) => unreachable!(),
            BrzError::PermissionDenied(_, m) => Error::PermissionDenied(m),
            e => Error::BrzError(e),
        }
    }
}

impl From<std::io::Error> for Error {
    fn from(e: std::io::Error) -> Self {
        Error::IOError(e)
    }
}

impl From<PublishError> for Error {
    fn from(e: PublishError) -> Self {
        match e {
            PublishError::Other(e) => Error::BrzError(e),
            e => Error::Other(format!("{:?}", e)),
        }
    }
}

impl From<crate::vcs::BranchOpenError> for Error {
    fn from(e: crate::vcs::BranchOpenError) -> Self {
        Error::Other(e.to_string())
    }
}

impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
        match self {
            Error::IOError(e) => write!(f, "{}", e),
            Error::UnknownFormat(n) => write!(f, "Unknown format: {}", n),
            Error::BrzError(e) => write!(f, "{}", e),
            Error::PermissionDenied(m) => write!(f, "Permission denied: {:?}", m),
            Error::Other(e) => write!(f, "{}", e),
        }
    }
}

#[derive(Default)]
/// A builder for a workspace
pub struct WorkspaceBuilder {
    main_branch: Option<GenericBranch>,
    resume_branch: Option<GenericBranch>,
    cached_branch: Option<GenericBranch>,
    additional_colocated_branches: HashMap<String, String>,
    resume_branch_additional_colocated_branches: HashMap<String, String>,
    dir: Option<PathBuf>,
    path: Option<PathBuf>,
    format: Option<ControlDirFormat>,
}

impl WorkspaceBuilder {
    /// Set the main branch
    pub fn main_branch(mut self, main_branch: GenericBranch) -> Self {
        self.main_branch = Some(main_branch);
        self
    }

    /// Set the resume branch
    pub fn resume_branch(mut self, resume_branch: GenericBranch) -> Self {
        self.resume_branch = Some(resume_branch);
        self
    }

    /// Set the cached branch
    pub fn cached_branch(mut self, cached_branch: GenericBranch) -> Self {
        self.cached_branch = Some(cached_branch);
        self
    }

    /// Set the additional colocated branches
    pub fn additional_colocated_branches(
        mut self,
        additional_colocated_branches: HashMap<String, String>,
    ) -> Self {
        self.additional_colocated_branches = additional_colocated_branches;
        self
    }

    /// Set the additional colocated branches for the resume branch
    pub fn resume_branch_additional_colocated_branches(
        mut self,
        resume_branch_additional_colocated_branches: HashMap<String, String>,
    ) -> Self {
        self.resume_branch_additional_colocated_branches =
            resume_branch_additional_colocated_branches;
        self
    }

    /// Set the containing directory to use for the workspace
    pub fn dir(mut self, dir: PathBuf) -> Self {
        self.dir = Some(dir);
        self
    }

    /// Set the path to the workspace
    pub fn path(mut self, path: PathBuf) -> Self {
        self.path = Some(path);
        self
    }

    /// Set the control dir format to use.
    ///
    /// This defaults to the format of the remote branch.
    pub fn format(mut self, format: impl breezyshim::controldir::AsFormat) -> Self {
        self.format = format.as_format();
        self
    }

    /// Build the workspace
    pub fn build(self) -> Result<Workspace, Error> {
        let mut ws = Workspace {
            main_branch: self.main_branch,
            resume_branch: self.resume_branch,
            cached_branch: self.cached_branch,
            additional_colocated_branches: self.additional_colocated_branches,
            resume_branch_additional_colocated_branches: self
                .resume_branch_additional_colocated_branches,
            path: self.path,
            dir: self.dir,
            format: self.format,
            state: None,
        };

        ws.start()?;
        Ok(ws)
    }
}

struct WorkspaceState {
    base_revid: RevisionId,
    local_tree: breezyshim::workingtree::GenericWorkingTree,
    refreshed: bool,
    tempdir: Option<tempfile::TempDir>,
    main_colo_revid: HashMap<String, RevisionId>,
}

/// A place in which changes can be prepared for publication
pub struct Workspace {
    main_branch: Option<GenericBranch>,
    cached_branch: Option<GenericBranch>,
    resume_branch: Option<GenericBranch>,
    additional_colocated_branches: HashMap<String, String>,
    resume_branch_additional_colocated_branches: HashMap<String, String>,
    dir: Option<PathBuf>,
    path: Option<PathBuf>,
    state: Option<WorkspaceState>,
    format: Option<breezyshim::controldir::ControlDirFormat>,
}

impl Workspace {
    /// Create a new temporary workspace
    pub fn temporary() -> Result<Self, Error> {
        let td = tempfile::tempdir().unwrap();
        let path = td.path().to_path_buf();
        let _ = td.keep(); // Keep the temporary directory
        Self::builder().dir(path).build()
    }

    /// Create a new workspace from a main branch URL
    pub fn from_url(url: &url::Url) -> Result<Self, Error> {
        let branch = crate::vcs::open_branch(url, None, None, None)?;
        Self::builder().main_branch(branch).build()
    }

    /// Start this workspace
    fn start(&mut self) -> Result<(), Error> {
        if self.state.is_some() {
            panic!("Workspace already started");
        }
        let mut td: Option<tempfile::TempDir> = None;
        // First, clone the main branch from the most efficient source
        let (sprout_base, sprout_coloc) = if let Some(cache_branch) = self.cached_branch.as_ref() {
            (Some(cache_branch), &self.additional_colocated_branches)
        } else if let Some(resume_branch) = self.resume_branch.as_ref() {
            (
                Some(resume_branch),
                &self.resume_branch_additional_colocated_branches,
            )
        } else {
            (
                self.main_branch.as_ref(),
                &self.additional_colocated_branches,
            )
        };

        let (local_tree, td) = if let Some(sprout_base) = sprout_base {
            log::debug!("Creating sprout from {}", sprout_base.get_user_url());
            let (wt, td) = crate::utils::create_temp_sprout(
                sprout_base,
                Some(
                    sprout_coloc
                        .iter()
                        .map(|(k, v)| (k.clone(), v.clone()))
                        .collect(),
                ),
                self.dir.as_deref(),
                self.path.as_deref(),
            )?;
            (wt, td)
        } else {
            if let Some(format) = self.format.as_ref() {
                log::debug!(
                    "Creating new empty tree with format {}",
                    format.get_format_description()
                );
            } else {
                log::debug!("Creating new empty tree");
            };

            let tp = if let Some(path) = self.path.as_deref() {
                std::fs::create_dir_all(path)?;
                path.to_path_buf()
            } else {
                td = Some(if let Some(dir) = self.dir.as_ref() {
                    tempfile::tempdir_in(dir)?
                } else {
                    tempfile::tempdir()?
                });
                td.as_ref().unwrap().path().to_path_buf()
            };
            (
                breezyshim::controldir::create_standalone_workingtree(
                    tp.as_path(),
                    self.format
                        .as_ref()
                        .unwrap_or(&breezyshim::controldir::ControlDirFormat::default()),
                )?,
                td,
            )
        };

        if let Some(path) = self.path.as_ref() {
            breezyshim::clean_tree::clean_tree(path, true, true, true, false, true)?;
        }

        let mut main_colo_revid = std::collections::HashMap::new();

        let mut refreshed = false;

        // If there is a main branch, ensure that revisions match
        if let Some(main_branch) = self.main_branch.as_ref() {
            for (from_name, _to_name) in self.additional_colocated_branches.iter() {
                match main_branch.controldir().open_branch(Some(from_name)) {
                    Ok(branch) => {
                        main_colo_revid.insert(from_name.to_string(), branch.last_revision());
                    }
                    Err(BrzError::NotBranchError(..)) => {}
                    Err(BrzError::NoColocatedBranchSupport) => {}
                    Err(e) => {
                        log::warn!("Failed to open colocated branch {}: {}", from_name, e);
                    }
                }
            }

            if let Some(cached_branch) = self.cached_branch.as_ref() {
                log::debug!(
                    "Pulling in missing revisions from resume/main branch {:?}",
                    cached_branch.get_user_url()
                );

                let from_branch = if let Some(resume_branch) = self.resume_branch.as_ref() {
                    resume_branch
                } else {
                    main_branch
                };

                match local_tree.pull(from_branch, Some(true), None, None) {
                    Ok(_) => {}
                    Err(BrzError::DivergedBranches) => {
                        unreachable!();
                    }
                    Err(e) => {
                        return Err(e.into());
                    }
                }

                assert_eq!(
                    local_tree.last_revision().unwrap(),
                    main_branch.last_revision()
                );
            }

            // At this point, we're either on the tip of the main branch or the tip of the resume
            // branch
            if let Some(resume_branch) = self.resume_branch.as_ref() {
                // If there's a resume branch at play, make sure it's derived from the main branch
                // *or* reset back to the main branch.
                log::debug!(
                    "Pulling in missing revisions from main branch {:?}",
                    main_branch.get_user_url()
                );

                match local_tree.pull(main_branch, Some(false), None, None) {
                    Err(BrzError::DivergedBranches) => {
                        log::info!("restarting branch");
                        refreshed = true;
                        self.resume_branch = None;
                        self.resume_branch_additional_colocated_branches.clear();
                        match local_tree.pull(main_branch, Some(true), None, None) {
                            Ok(_) => {}
                            Err(BrzError::DivergedBranches) => {
                                unreachable!();
                            }
                            Err(e) => {
                                return Err(e.into());
                            }
                        }
                        fetch_colocated(
                            local_tree.branch().controldir().as_ref(),
                            main_branch.controldir().as_ref(),
                            &self
                                .additional_colocated_branches
                                .iter()
                                .map(|(k, v)| (k.as_str(), v.as_str()))
                                .collect(),
                        )?;
                    }
                    Ok(_) => {
                        fetch_colocated(
                            local_tree.branch().controldir().as_ref(),
                            main_branch.controldir().as_ref(),
                            &self
                                .additional_colocated_branches
                                .iter()
                                .map(|(k, v)| (k.as_str(), v.as_str()))
                                .collect(),
                        )?;

                        if !self.resume_branch_additional_colocated_branches.is_empty() {
                            fetch_colocated(
                                local_tree.branch().controldir().as_ref(),
                                resume_branch.controldir().as_ref(),
                                &self
                                    .resume_branch_additional_colocated_branches
                                    .iter()
                                    .map(|(k, v)| (k.as_str(), v.as_str()))
                                    .collect(),
                            )?;

                            self.additional_colocated_branches
                                .extend(self.resume_branch_additional_colocated_branches.drain());
                        }
                    }
                    Err(e) => {
                        log::warn!("Failed to pull from main branch: {}", e);
                    }
                }
            } else {
                fetch_colocated(
                    local_tree.branch().controldir().as_ref(),
                    main_branch.controldir().as_ref(),
                    &self
                        .additional_colocated_branches
                        .iter()
                        .map(|(k, v)| (k.as_str(), v.as_str()))
                        .collect(),
                )?;
            }
        }

        self.state = Some(WorkspaceState {
            base_revid: local_tree.last_revision().unwrap(),
            local_tree: local_tree,
            refreshed,
            main_colo_revid,
            tempdir: td,
        });

        Ok(())
    }

    /// Return the state of the workspace
    fn state(&self) -> &WorkspaceState {
        self.state.as_ref().unwrap()
    }

    /// Create a new workspace builder
    pub fn builder() -> WorkspaceBuilder {
        WorkspaceBuilder::default()
    }

    /// Return the main branch
    pub fn main_branch(&self) -> Option<&GenericBranch> {
        self.main_branch.as_ref()
    }

    /// Set the main branch
    pub fn set_main_branch(&mut self, branch: GenericBranch) -> Result<(), Error> {
        self.main_branch = Some(branch);
        Ok(())
    }

    /// Return the cached branch
    pub fn local_tree(&self) -> &breezyshim::workingtree::GenericWorkingTree {
        &self.state().local_tree
    }

    /// Return whether the workspace has been refreshed
    ///
    /// In other words, whether the workspace has been reset to the main branch
    pub fn refreshed(&self) -> bool {
        self.state().refreshed
    }

    /// Return the resume branch
    pub fn resume_branch(&self) -> Option<&GenericBranch> {
        self.resume_branch.as_ref()
    }

    /// Return the path to the workspace
    pub fn path(&self) -> PathBuf {
        self.local_tree()
            .abspath(std::path::Path::new("."))
            .unwrap()
    }

    /// Return whether there are changes since the main branch
    pub fn changes_since_main(&self) -> bool {
        Some(self.local_tree().branch().last_revision())
            != self.main_branch().map(|b| b.last_revision())
    }

    /// Return whether there are changes since the base revision
    pub fn changes_since_base(&self) -> bool {
        self.base_revid() != Some(&self.local_tree().branch().last_revision())
    }

    /// Return the base revision id
    pub fn base_revid(&self) -> Option<&RevisionId> {
        self.state.as_ref().map(|s| &s.base_revid)
    }

    /// Have any branch changes at all been made?
    ///
    /// Includes changes that already existed in the resume branch
    pub fn any_branch_changes(&self) -> bool {
        self.changed_branches().iter().any(|(_, br, r)| br != r)
    }

    /// Return the additional colocated branches
    pub fn additional_colocated_branches(&self) -> &HashMap<String, String> {
        &self.additional_colocated_branches
    }

    /// Return the branches that have changed
    pub fn changed_branches(&self) -> Vec<(String, Option<RevisionId>, Option<RevisionId>)> {
        let main_branch = self.main_branch();
        let mut branches = vec![(
            main_branch
                .as_ref()
                .map_or_else(|| "".to_string(), |b| b.name().unwrap()),
            main_branch.map(|b| b.last_revision()),
            Some(self.local_tree().last_revision().unwrap()),
        )];

        let local_controldir = self.local_tree().controldir();

        for (from_name, to_name) in self.additional_colocated_branches().iter() {
            let to_revision = match local_controldir.open_branch(Some(to_name)) {
                Ok(b) => Some(b.last_revision()),
                Err(BrzError::NoColocatedBranchSupport) => continue,
                Err(BrzError::NotBranchError(..)) => None,
                Err(e) => {
                    panic!("Unexpected error opening branch {}: {}", to_name, e);
                }
            };

            let from_revision = self.main_colo_revid().get(from_name).cloned();

            branches.push((from_name.to_string(), from_revision, to_revision));
        }

        branches
    }

    /// Return the main colocated branch revision ids
    pub fn main_colo_revid(&self) -> &HashMap<String, RevisionId> {
        &self.state().main_colo_revid
    }

    /// Return the basis tree
    pub fn base_tree(&self) -> Result<Box<RevisionTree>, BrzError> {
        let base_revid = &self.state().base_revid;
        match self.state().local_tree.revision_tree(base_revid) {
            Ok(t) => Ok(t),
            Err(BrzError::NoSuchRevisionInTree(revid)) => {
                // Fall back to repository if the working tree doesn't have the revision
                Ok(Box::new(
                    self.local_tree()
                        .branch()
                        .repository()
                        .revision_tree(&revid)?,
                ))
            }
            Err(e) => Err(e),
        }
    }

    /// Defer destroying the workspace, even if the Workspace is dropped
    pub fn defer_destroy(&mut self) -> std::path::PathBuf {
        let tempdir = self.state.as_mut().unwrap().tempdir.take().unwrap();

        tempdir.keep()
    }

    /// Publish the changes back to the main branch
    pub fn publish_changes(
        &self,
        target_branch: Option<&GenericBranch>,
        mode: crate::Mode,
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
    ) -> Result<PublishResult, PublishError> {
        let main_branch = self.main_branch();
        crate::publish::publish_changes(
            &self.local_tree().branch(),
            target_branch.or(main_branch).unwrap(),
            self.resume_branch(),
            mode,
            name,
            get_proposal_description,
            get_proposal_commit_message,
            get_proposal_title,
            forge,
            allow_create_proposal,
            labels,
            overwrite_existing,
            existing_proposal,
            reviewers,
            tags,
            derived_owner,
            allow_collaboration,
            stop_revision,
            auto_merge,
            work_in_progress,
        )
    }

    /// Propose the changes against the main branch
    pub fn propose(
        &self,
        name: &str,
        description: &str,
        target_branch: Option<&GenericBranch>,
        forge: Option<Forge>,
        existing_proposal: Option<MergeProposal>,
        tags: Option<HashMap<String, RevisionId>>,
        labels: Option<Vec<String>>,
        overwrite_existing: Option<bool>,
        commit_message: Option<&str>,
        allow_collaboration: Option<bool>,
        title: Option<&str>,
        allow_empty: Option<bool>,
        reviewers: Option<Vec<String>>,
        owner: Option<&str>,
        auto_merge: Option<bool>,
        work_in_progress: Option<bool>,
    ) -> Result<(MergeProposal, bool), Error> {
        let main_branch = self.main_branch();
        let target_branch = target_branch.or_else(|| main_branch).unwrap();
        let forge = if let Some(forge) = forge {
            forge
        } else {
            // GenericBranch implements PyBranch, so we can use forge operations
            match breezyshim::forge::get_forge(target_branch) {
                Ok(forge) => forge,
                Err(e) => return Err(Error::BrzError(e)),
            }
        };
        crate::publish::propose_changes(
            &self.local_tree().branch(),
            target_branch,
            &forge,
            name,
            description,
            self.resume_branch(),
            existing_proposal,
            overwrite_existing,
            labels,
            commit_message,
            title,
            Some(self.inverse_additional_colocated_branches()),
            allow_empty,
            reviewers,
            tags,
            owner,
            None,
            allow_collaboration,
            auto_merge,
            work_in_progress,
        )
        .map_err(|e| e.into())
    }

    /// Push a new derived branch
    pub fn push_derived(
        &self,
        name: &str,
        target_branch: Option<&GenericBranch>,
        forge: Option<Forge>,
        tags: Option<HashMap<String, RevisionId>>,
        overwrite_existing: Option<bool>,
        owner: Option<&str>,
    ) -> Result<(Box<dyn Branch>, url::Url), Error> {
        let main_branch = self.main_branch();
        let target_branch = target_branch.or(main_branch).unwrap();
        let forge = if let Some(forge) = forge {
            forge
        } else {
            // GenericBranch implements PyBranch, so we can use forge operations
            match breezyshim::forge::get_forge(target_branch) {
                Ok(forge) => forge,
                Err(e) => return Err(Error::BrzError(e)),
            }
        };
        crate::publish::push_derived_changes(
            &self.local_tree().branch(),
            target_branch,
            &forge,
            name,
            overwrite_existing,
            owner,
            tags,
            None,
        )
        .map_err(|e| e.into())
    }

    /// Push the specified tags to the main branch
    pub fn push_tags(&self, tags: HashMap<String, RevisionId>) -> Result<(), Error> {
        self.push(Some(tags))
    }

    /// Push the changes back to the main branch
    pub fn push(&self, tags: Option<HashMap<String, RevisionId>>) -> Result<(), Error> {
        let main_branch = self.main_branch().unwrap();

        // Get forge for the main branch
        let forge = match breezyshim::forge::get_forge(main_branch) {
            Ok(forge) => Some(forge),
            Err(breezyshim::error::Error::UnsupportedForge(e)) => {
                // We can't figure out what branch to resume from when there's no forge
                // that can tell us.
                log::warn!(
                    "Unsupported forge ({}), will attempt to push to {}",
                    e,
                    crate::vcs::full_branch_url(main_branch),
                );
                None
            }
            Err(e) => {
                return Err(e.into());
            }
        };

        crate::publish::push_changes(
            &self.local_tree().branch(),
            &self.local_tree().branch(), // This is a hack - need proper GenericBranch
            forge.as_ref(),
            None,
            Some(
                self.inverse_additional_colocated_branches()
                    .into_iter()
                    .collect(),
            ),
            tags,
            None,
        )
        .map_err(Into::into)
    }

    fn inverse_additional_colocated_branches(&self) -> Vec<(String, String)> {
        self.additional_colocated_branches()
            .iter()
            .map(|(k, v)| (v.clone(), k.clone()))
            .collect()
    }

    /// Show the diff between the base tree and the local tree
    pub fn show_diff(
        &self,
        outf: Box<dyn std::io::Write + Send>,
        old_label: Option<&str>,
        new_label: Option<&str>,
    ) -> Result<(), BrzError> {
        let base_tree = self.base_tree()?;
        let basis_tree = self.local_tree().basis_tree()?;
        breezyshim::diff::show_diff_trees(
            base_tree.as_ref(),
            &basis_tree,
            outf,
            old_label,
            new_label,
        )
    }

    /// Destroy this workspace
    pub fn destroy(&mut self) -> Result<(), Error> {
        self.state = None;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    use breezyshim::controldir::ControlDirFormat;

    #[test]
    fn test_create_workspace() {
        let mut ws = Workspace::builder().build().unwrap();

        assert_eq!(ws.local_tree().branch().name().as_ref().unwrap(), "");

        assert_eq!(
            ws.base_revid(),
            Some(&breezyshim::revisionid::RevisionId::null())
        );

        // There are changes since the branch is created
        assert!(ws.changes_since_main());
        assert!(!ws.changes_since_base());
        assert_eq!(
            ws.changed_branches(),
            vec![(
                "".to_string(),
                None,
                Some(breezyshim::revisionid::RevisionId::null())
            )]
        );

        let revid = ws
            .local_tree()
            .build_commit()
            .message("test commit")
            .allow_pointless(true)
            .commit()
            .unwrap();

        assert!(ws.changes_since_main());
        assert!(ws.changes_since_base());
        assert_eq!(
            ws.changed_branches(),
            vec![("".to_string(), None, Some(revid))]
        );

        ws.destroy().unwrap();
    }

    #[test]
    fn test_temporary() {
        let ws = Workspace::temporary().unwrap();

        assert_eq!(ws.local_tree().branch().name().as_ref().unwrap(), "");

        assert_eq!(
            ws.base_revid(),
            Some(&breezyshim::revisionid::RevisionId::null())
        );

        // There are changes since the branch is created
        assert!(ws.changes_since_main());
        assert!(!ws.changes_since_base());
        assert_eq!(
            ws.changed_branches(),
            vec![(
                "".to_string(),
                None,
                Some(breezyshim::revisionid::RevisionId::null())
            )]
        );
    }

    #[test]
    fn test_nascent() {
        let td = tempfile::tempdir().unwrap();
        let origin = breezyshim::controldir::create_standalone_workingtree(
            &td.path().join("origin"),
            &ControlDirFormat::default(),
        )
        .unwrap();

        let ws_dir = td.path().join("ws");
        std::fs::create_dir(&ws_dir).unwrap();

        let ws = Workspace::builder()
            .main_branch(origin.branch())
            .dir(ws_dir)
            .build()
            .unwrap();

        assert!(!ws.changes_since_main());
        assert!(!ws.any_branch_changes());
        assert!(!ws.changes_since_base());
        ws.local_tree()
            .build_commit()
            .message("A change")
            .commit()
            .unwrap();

        assert_eq!(ws.path(), ws.local_tree().basedir().join("."));

        assert!(ws.changes_since_main());
        assert!(ws.changes_since_base());
        assert!(ws.any_branch_changes());
        assert_eq!(
            vec![(
                "".to_string(),
                Some(breezyshim::revisionid::RevisionId::null()),
                Some(ws.local_tree().last_revision().unwrap())
            )],
            ws.changed_branches()
        );

        std::mem::drop(td);
    }

    #[test]
    fn test_without_main() {
        let td = tempfile::tempdir().unwrap();

        let ws = Workspace::builder()
            .dir(td.path().to_path_buf())
            .build()
            .unwrap();

        assert!(ws.changes_since_main());
        assert!(ws.any_branch_changes());
        assert!(!ws.changes_since_base());
        ws.local_tree()
            .build_commit()
            .message("A change")
            .commit()
            .unwrap();
        assert!(ws.changes_since_main());
        assert!(ws.changes_since_base());
        assert!(ws.any_branch_changes());
        assert_eq!(
            vec![(
                "".to_string(),
                None,
                Some(ws.local_tree().last_revision().unwrap())
            )],
            ws.changed_branches()
        );
        std::mem::drop(ws);
        std::mem::drop(td);
    }

    #[test]
    fn test_basic() {
        let td = tempfile::tempdir().unwrap();

        let origin = breezyshim::controldir::create_standalone_workingtree(
            &td.path().join("origin"),
            &ControlDirFormat::default(),
        )
        .unwrap();

        let revid1 = origin
            .build_commit()
            .message("first commit")
            .commit()
            .unwrap();

        let ws_dir = td.path().join("ws");
        std::fs::create_dir(&ws_dir).unwrap();

        let ws = Workspace::builder()
            .main_branch(origin.branch())
            .dir(ws_dir)
            .build()
            .unwrap();

        assert!(!ws.changes_since_main());
        assert!(!ws.any_branch_changes());
        assert!(!ws.changes_since_base());

        ws.local_tree()
            .build_commit()
            .message("A change")
            .commit()
            .unwrap();

        assert!(ws.changes_since_main());
        assert!(ws.changes_since_base());
        assert!(ws.any_branch_changes());
        assert_eq!(
            vec![(
                "".to_string(),
                Some(revid1),
                Some(ws.local_tree().last_revision().unwrap())
            )],
            ws.changed_branches()
        );
        std::mem::drop(td);
    }

    #[test]
    fn test_cached_branch_up_to_date() {
        let td = tempfile::tempdir().unwrap();

        let origin = breezyshim::controldir::create_standalone_workingtree(
            &td.path().join("origin"),
            &ControlDirFormat::default(),
        )
        .unwrap();
        let revid1 = origin
            .build_commit()
            .message("first commit")
            .commit()
            .unwrap();

        let cached = origin
            .branch()
            .controldir()
            .sprout(
                url::Url::from_directory_path(td.path().join("cached")).unwrap(),
                None,
                None,
                None,
                None,
            )
            .unwrap();

        let ws_dir = td.path().join("ws");
        std::fs::create_dir(&ws_dir).unwrap();

        let ws = Workspace::builder()
            .main_branch(origin.branch())
            .cached_branch(*cached.open_branch(None).unwrap())
            .dir(ws_dir)
            .build()
            .unwrap();

        assert!(!ws.changes_since_main());
        assert!(!ws.any_branch_changes());
        assert!(!ws.changes_since_base());
        assert_eq!(ws.local_tree().last_revision().unwrap(), revid1);

        std::mem::drop(td);
    }

    #[test]
    fn test_cached_branch_out_of_date() {
        let td = tempfile::tempdir().unwrap();

        let origin = breezyshim::controldir::create_standalone_workingtree(
            &td.path().join("origin"),
            &ControlDirFormat::default(),
        )
        .unwrap();
        origin
            .build_commit()
            .message("first commit")
            .commit()
            .unwrap();

        let cached = origin
            .branch()
            .controldir()
            .sprout(
                url::Url::from_directory_path(td.path().join("cached")).unwrap(),
                None,
                None,
                None,
                None,
            )
            .unwrap();

        let revid2 = origin
            .build_commit()
            .message("second commit")
            .commit()
            .unwrap();

        let ws_dir = td.path().join("ws");
        std::fs::create_dir(&ws_dir).unwrap();

        let ws = Workspace::builder()
            .main_branch(origin.branch())
            .cached_branch(*cached.open_branch(None).unwrap())
            .dir(ws_dir)
            .build()
            .unwrap();

        assert!(!ws.changes_since_main());
        assert!(!ws.any_branch_changes());
        assert!(!ws.changes_since_base());
        assert_eq!(ws.local_tree().last_revision().unwrap(), revid2);

        std::mem::drop(td);
    }

    fn commit_on_colo<C: ControlDir + ?Sized>(
        controldir: &C,
        to_location: &std::path::Path,
        message: &str,
    ) -> RevisionId {
        let colo_branch = controldir.create_branch(Some("colo")).unwrap();
        let colo_checkout = colo_branch.create_checkout(to_location).unwrap();

        colo_checkout
            .build_commit()
            .message(message)
            .commit()
            .unwrap()
    }

    #[test]
    fn test_colocated() {
        let td = tempfile::tempdir().unwrap();

        let origin = breezyshim::controldir::create_standalone_workingtree(
            &td.path().join("origin"),
            &ControlDirFormat::default(),
        )
        .unwrap();
        let revid1 = origin.build_commit().message("main").commit().unwrap();

        let colo_revid1 = commit_on_colo(
            &*origin.branch().controldir(),
            &td.path().join("colo"),
            "Another",
        );

        assert_eq!(origin.branch().last_revision(), revid1);

        let ws_dir = td.path().join("ws");
        std::fs::create_dir(&ws_dir).unwrap();

        let ws = Workspace::builder()
            .main_branch(origin.branch())
            .dir(ws_dir)
            .additional_colocated_branches(
                vec![("colo".to_string(), "colo".to_string())]
                    .into_iter()
                    .collect(),
            )
            .build()
            .unwrap();

        assert!(!ws.changes_since_main());
        assert!(!ws.any_branch_changes());
        assert!(!ws.changes_since_base());

        ws.local_tree()
            .build_commit()
            .message("A change")
            .commit()
            .unwrap();

        assert!(ws.changes_since_main());
        assert!(ws.changes_since_base());
        assert!(ws.any_branch_changes());
        assert_eq!(
            vec![
                (
                    "".to_string(),
                    Some(revid1),
                    Some(ws.local_tree().last_revision().unwrap())
                ),
                (
                    "colo".to_string(),
                    Some(colo_revid1.clone()),
                    Some(colo_revid1.clone())
                ),
            ],
            ws.changed_branches()
        );
        std::mem::drop(td);
    }

    #[test]
    fn test_resume_continue() {
        let td = tempfile::tempdir().unwrap();

        let origin = breezyshim::controldir::create_standalone_workingtree(
            &td.path().join("origin"),
            &ControlDirFormat::default(),
        )
        .unwrap();

        let revid1 = origin
            .build_commit()
            .message("first commit")
            .commit()
            .unwrap();

        let resume = origin
            .branch()
            .controldir()
            .sprout(
                url::Url::from_directory_path(td.path().join("resume")).unwrap(),
                None,
                None,
                None,
                None,
            )
            .unwrap();

        let resume_tree = resume.open_workingtree().unwrap();

        let resume_revid1 = resume_tree
            .build_commit()
            .message("resume")
            .commit()
            .unwrap();

        let ws_dir = td.path().join("ws");
        std::fs::create_dir(&ws_dir).unwrap();

        let ws = Workspace::builder()
            .main_branch(origin.branch())
            .resume_branch(resume_tree.branch())
            .dir(ws_dir)
            .build()
            .unwrap();

        assert!(ws.changes_since_main());
        assert!(ws.any_branch_changes());
        assert!(!ws.refreshed());
        assert!(!ws.changes_since_base());

        assert_eq!(ws.local_tree().last_revision().unwrap(), resume_revid1);
        assert_eq!(
            vec![("".to_string(), Some(revid1), Some(resume_revid1))],
            ws.changed_branches()
        );

        std::mem::drop(td);
    }

    #[test]
    fn test_resume_discard() {
        let td = tempfile::tempdir().unwrap();

        let origin = breezyshim::controldir::create_standalone_workingtree(
            &td.path().join("origin"),
            &ControlDirFormat::default(),
        )
        .unwrap();
        origin
            .build_commit()
            .message("first commit")
            .commit()
            .unwrap();

        let resume = origin
            .branch()
            .controldir()
            .sprout(
                url::Url::from_directory_path(td.path().join("resume")).unwrap(),
                None,
                None,
                None,
                None,
            )
            .unwrap();
        let revid2 = origin
            .build_commit()
            .message("second commit")
            .commit()
            .unwrap();

        let resume_tree = resume.open_workingtree().unwrap();
        resume_tree
            .build_commit()
            .message("resume")
            .commit()
            .unwrap();

        let ws_dir = td.path().join("ws");
        std::fs::create_dir(&ws_dir).unwrap();

        let ws = Workspace::builder()
            .main_branch(origin.branch())
            .resume_branch(resume_tree.branch())
            .dir(ws_dir)
            .build()
            .unwrap();

        assert!(!ws.changes_since_main());
        assert!(!ws.any_branch_changes());
        assert!(ws.refreshed());

        assert!(!ws.changes_since_base());
        assert_eq!(ws.local_tree().last_revision().unwrap(), revid2);

        assert_eq!(
            vec![("".to_string(), Some(revid2.clone()), Some(revid2.clone()))],
            ws.changed_branches()
        );
        std::mem::drop(td);
    }

    #[test]
    fn test_resume_continue_with_unchanged_colocated() {
        let td = tempfile::tempdir().unwrap();

        let origin = breezyshim::controldir::create_standalone_workingtree(
            &td.path().join("origin"),
            &ControlDirFormat::default(),
        )
        .unwrap();

        let revid1 = origin
            .build_commit()
            .message("first commit")
            .commit()
            .unwrap();

        let colo_revid1 = commit_on_colo(
            &*origin.branch().controldir(),
            &td.path().join("colo"),
            "First colo",
        );

        let resume = origin
            .branch()
            .controldir()
            .sprout(
                url::Url::from_directory_path(td.path().join("resume")).unwrap(),
                None,
                None,
                None,
                None,
            )
            .unwrap();

        let resume_tree = resume.open_workingtree().unwrap();

        let resume_revid1 = resume_tree
            .build_commit()
            .message("resume")
            .commit()
            .unwrap();

        let ws_dir = td.path().join("ws");
        std::fs::create_dir(&ws_dir).unwrap();

        let ws = Workspace::builder()
            .main_branch(origin.branch())
            .resume_branch(resume_tree.branch())
            .dir(ws_dir)
            .additional_colocated_branches(
                vec![("colo".to_string(), "colo".to_string())]
                    .into_iter()
                    .collect(),
            )
            .build()
            .unwrap();

        assert!(ws.changes_since_main());
        assert!(ws.any_branch_changes());
        assert!(!ws.refreshed());
        assert!(!ws.changes_since_base());
        assert_eq!(ws.local_tree().last_revision().unwrap(), resume_revid1);
        assert_eq!(
            vec![
                ("".to_string(), Some(revid1), Some(resume_revid1)),
                (
                    "colo".to_string(),
                    Some(colo_revid1.clone()),
                    Some(colo_revid1.clone())
                ),
            ],
            ws.changed_branches()
        );
        std::mem::drop(td);
    }

    #[test]
    fn test_resume_discard_with_unchanged_colocated() {
        let td = tempfile::tempdir().unwrap();

        let origin = breezyshim::controldir::create_standalone_workingtree(
            &td.path().join("origin"),
            &ControlDirFormat::default(),
        )
        .unwrap();

        origin
            .build_commit()
            .message("first commit")
            .commit()
            .unwrap();

        let colo_revid1 = commit_on_colo(
            &*origin.branch().controldir(),
            &td.path().join("colo"),
            "First colo",
        );

        let resume = origin
            .branch()
            .controldir()
            .sprout(
                url::Url::from_directory_path(td.path().join("resume")).unwrap(),
                None,
                None,
                None,
                None,
            )
            .unwrap();

        commit_on_colo(
            resume.as_ref(),
            &td.path().join("resume-colo"),
            "First colo on resume",
        );

        let revid2 = origin
            .build_commit()
            .message("second commit")
            .commit()
            .unwrap();
        let resume_tree = resume.open_workingtree().unwrap();
        resume_tree
            .build_commit()
            .message("resume")
            .commit()
            .unwrap();

        let ws_dir = td.path().join("ws");
        std::fs::create_dir(&ws_dir).unwrap();

        let ws = Workspace::builder()
            .main_branch(origin.branch())
            .resume_branch(resume_tree.branch())
            .dir(ws_dir)
            .additional_colocated_branches(
                vec![("colo".to_string(), "colo".to_string())]
                    .into_iter()
                    .collect(),
            )
            .build()
            .unwrap();

        assert!(!ws.changes_since_main());
        assert!(!ws.any_branch_changes());
        assert!(ws.refreshed());
        assert!(!ws.changes_since_base());
        assert_eq!(ws.local_tree().last_revision().unwrap(), revid2);
        assert_eq!(
            vec![
                ("".to_string(), Some(revid2.clone()), Some(revid2.clone())),
                (
                    "colo".to_string(),
                    Some(colo_revid1.clone()),
                    Some(colo_revid1.clone())
                ),
            ],
            ws.changed_branches()
        );
        std::mem::drop(td);
    }

    #[test]
    fn test_defer_destroy() {
        let td = tempfile::tempdir().unwrap();

        let origin = breezyshim::controldir::create_standalone_workingtree(
            &td.path().join("origin"),
            &ControlDirFormat::default(),
        )
        .unwrap();
        origin
            .build_commit()
            .message("first commit")
            .commit()
            .unwrap();

        let ws_dir = td.path().join("ws");
        std::fs::create_dir(&ws_dir).unwrap();

        let mut ws = Workspace::builder()
            .main_branch(origin.branch())
            .dir(ws_dir)
            .build()
            .unwrap();

        let tempdir = ws.defer_destroy();

        assert!(tempdir.exists());

        std::mem::drop(ws);

        assert!(tempdir.exists());
        std::mem::drop(td);
    }
}
