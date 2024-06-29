use crate::publish::{DescriptionFormat, Error as PublishError, PublishResult};
use breezyshim::branch::Branch;
use breezyshim::error::Error as BrzError;
use breezyshim::forge::{Forge, MergeProposal};
use breezyshim::tree::WorkingTree;
use breezyshim::ControlDir;
use breezyshim::RevisionId;
use log::info;
use pyo3::PyErr;
use std::collections::HashMap;
use std::path::{Path, PathBuf};

pub fn fetch_colocated(
    controldir: &ControlDir,
    from_controldir: &ControlDir,
    additional_colocated_branches: &HashMap<&str, &str>,
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
                    None,
                    Some(true),
                    None,
                )?;
            }
            Err(BrzError::NotBranchError(..)) | Err(BrzError::NoColocatedBranchSupport) => {
                continue;
            }
            Err(e) => {
                return Err(e.into());
            }
        }
    }
    Ok(())
}

#[derive(Debug)]
pub enum Error {
    Python(PyErr),
    BrzError(BrzError),
    ForgeError(breezyshim::forge::Error),
    IOError(std::io::Error),
    UnknownFormat(String),
}

impl From<BrzError> for Error {
    fn from(e: BrzError) -> Self {
        match e {
            BrzError::Other(e) => Error::Python(e),
            BrzError::UnknownFormat(n) => Error::UnknownFormat(n),
            BrzError::AlreadyControlDir(_) => unreachable!(),
            e => Error::BrzError(e),
        }
    }
}

impl From<breezyshim::forge::Error> for Error {
    fn from(e: breezyshim::forge::Error) -> Self {
        Error::ForgeError(e)
    }
}

impl From<std::io::Error> for Error {
    fn from(e: std::io::Error) -> Self {
        Error::IOError(e)
    }
}

impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
        match self {
            Error::Python(e) => write!(f, "{}", e),
            Error::ForgeError(e) => write!(f, "{}", e),
            Error::IOError(e) => write!(f, "{}", e),
            Error::UnknownFormat(n) => write!(f, "Unknown format: {}", n),
            Error::BrzError(e) => write!(f, "{}", e),
        }
    }
}

impl From<PyErr> for Error {
    fn from(e: PyErr) -> Self {
        Error::Python(e)
    }
}

#[derive(Default)]
pub struct WorkspaceBuilder<'a> {
    main_branch: Option<&'a dyn Branch>,
    resume_branch: Option<&'a dyn Branch>,
    cached_branch: Option<&'a dyn Branch>,
    additional_colocated_branches: HashMap<&'a str, &'a str>,
    resume_branch_additional_colocated_branches: HashMap<&'a str, &'a str>,
    dir: Option<&'a Path>,
    path: Option<&'a Path>,
    format: Option<&'a str>,
}

impl<'a> WorkspaceBuilder<'a> {
    pub fn main_branch(mut self, main_branch: &'a dyn Branch) -> Self {
        self.main_branch = Some(main_branch);
        self
    }

    pub fn resume_branch(mut self, resume_branch: &'a dyn Branch) -> Self {
        self.resume_branch = Some(resume_branch);
        self
    }

    pub fn cached_branch(mut self, cached_branch: &'a dyn Branch) -> Self {
        self.cached_branch = Some(cached_branch);
        self
    }

    pub fn additional_colocated_branches(
        mut self,
        additional_colocated_branches: HashMap<&'a str, &'a str>,
    ) -> Self {
        self.additional_colocated_branches = additional_colocated_branches;
        self
    }

    pub fn resume_branch_additional_colocated_branches(
        mut self,
        resume_branch_additional_colocated_branches: HashMap<&'a str, &'a str>,
    ) -> Self {
        self.resume_branch_additional_colocated_branches =
            resume_branch_additional_colocated_branches;
        self
    }

    pub fn dir(mut self, dir: &'a Path) -> Self {
        self.dir = Some(dir);
        self
    }

    pub fn path(mut self, path: &'a Path) -> Self {
        self.path = Some(path);
        self
    }

    pub fn format(mut self, format: &'a str) -> Self {
        self.format = Some(format);
        self
    }

    pub fn build(self) -> Result<Workspace<'a>, Error> {
        let mut ws = Workspace::new(
            self.main_branch,
            self.resume_branch,
            self.cached_branch,
            self.additional_colocated_branches,
            self.resume_branch_additional_colocated_branches,
            self.dir,
            self.path,
            self.format,
        );

        ws.start()?;
        Ok(ws)
    }
}

struct WorkspaceState {
    base_revid: RevisionId,
    local_tree: WorkingTree,
    refreshed: bool,
    destroy_fn: Option<Box<dyn FnOnce() -> std::io::Result<()> + Send>>,
    main_colo_revid: HashMap<String, RevisionId>,
}

pub struct Workspace<'a> {
    main_branch: Option<&'a dyn Branch>,
    cached_branch: Option<&'a dyn Branch>,
    resume_branch: Option<&'a dyn Branch>,
    additional_colocated_branches: HashMap<&'a str, &'a str>,
    resume_branch_additional_colocated_branches: HashMap<&'a str, &'a str>,
    dir: Option<&'a Path>,
    path: Option<&'a Path>,
    state: Option<WorkspaceState>,
    format: Option<breezyshim::controldir::ControlDirFormat>,
}

impl<'a> Workspace<'a> {
    pub fn new(
        main_branch: Option<&'a dyn Branch>,
        resume_branch: Option<&'a dyn Branch>,
        cached_branch: Option<&'a dyn Branch>,
        additional_colocated_branches: HashMap<&'a str, &'a str>,
        resume_branch_additional_colocated_branches: HashMap<&'a str, &'a str>,
        dir: Option<&'a Path>,
        path: Option<&'a Path>,
        format: Option<impl breezyshim::controldir::AsFormat>,
    ) -> Self {
        Self {
            main_branch,
            resume_branch,
            cached_branch,
            additional_colocated_branches,
            resume_branch_additional_colocated_branches,
            path,
            dir,
            format: format.and_then(|f| f.as_format()),
            state: None,
        }
    }

    pub fn start(&mut self) -> Result<(), Error> {
        let mut td: Option<tempfile::TempDir> = None;
        let (sprout_base, sprout_coloc) = if let Some(cache_branch) = self.cached_branch {
            (
                Some(cache_branch),
                self.additional_colocated_branches.clone(),
            )
        } else if let Some(resume_branch) = self.resume_branch {
            (
                Some(resume_branch),
                self.resume_branch_additional_colocated_branches.clone(),
            )
        } else {
            (self.main_branch, self.additional_colocated_branches.clone())
        };

        let (local_tree, destroy_fn) = if let Some(sprout_base) = sprout_base {
            log::debug!("Creating sprout from {:?}", sprout_base.get_user_url());
            let (wt, dfn) = crate::utils::create_temp_sprout(
                sprout_base,
                Some(
                    sprout_coloc
                        .iter()
                        .map(|(k, v)| (k.to_string(), v.to_string()))
                        .collect(),
                ),
                self.dir,
                self.path,
            )?;
            (wt, Some(dfn))
        } else {
            if let Some(format) = self.format.as_ref() {
                log::debug!(
                    "Creating new empty tree with format {}",
                    format.get_format_description()
                );
            } else {
                log::debug!("Creating new empty tree");
            };

            let tp = if let Some(path) = self.path {
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
                Some(Box::new(|| -> std::io::Result<()> {
                    if let Some(td) = td {
                        td.close().unwrap();
                    }
                    Ok(())
                })
                    as Box<dyn FnOnce() -> Result<(), std::io::Error> + Send>),
            )
        };

        let mut main_colo_revid = std::collections::HashMap::new();

        let mut refreshed = false;

        if let Some(main_branch) = self.main_branch {
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

            if let Some(cached_branch) = self.cached_branch {
                log::debug!(
                    "Pulling in missing revisions from resume/main branch {:?}",
                    cached_branch.get_user_url()
                );

                match local_tree.pull(cached_branch, Some(true)) {
                    Ok(_) => {}
                    Err(BrzError::DivergedBranches) => {
                        unreachable!();
                    }
                    Err(e) => {
                        return Err(e.into());
                    }
                }
            }

            // At this point, we're either on the tip of the main branch or the tip of the resume
            // branch
            if let Some(resume_branch) = self.resume_branch {
                // If there's a resume branch at play, make sure it's derived from the main branch
                // *or* reset back to the main branch.
                log::debug!(
                    "Pulling in missing revisions from main branch {:?}",
                    main_branch.get_user_url()
                );

                match local_tree.pull(main_branch, Some(false)) {
                    Err(BrzError::DivergedBranches) => {
                        log::info!("restarting branch");
                        refreshed = true;
                        self.resume_branch = None;
                        self.resume_branch_additional_colocated_branches.clear();
                        match local_tree.pull(main_branch, Some(true)) {
                            Ok(_) => {}
                            Err(BrzError::DivergedBranches) => {
                                unreachable!();
                            }
                            Err(e) => {
                                return Err(e.into());
                            }
                        }
                        fetch_colocated(
                            &local_tree.branch().controldir(),
                            &main_branch.controldir(),
                            &self.additional_colocated_branches,
                        )?;
                    }
                    Ok(_) => {
                        fetch_colocated(
                            &local_tree.branch().controldir(),
                            &main_branch.controldir(),
                            &self.additional_colocated_branches,
                        )?;

                        if !self.resume_branch_additional_colocated_branches.is_empty() {
                            fetch_colocated(
                                &local_tree.branch().controldir(),
                                &resume_branch.controldir(),
                                &self.resume_branch_additional_colocated_branches,
                            )?;

                            self.additional_colocated_branches
                                .extend(self.resume_branch_additional_colocated_branches.iter());
                        }
                    }
                    Err(e) => {
                        log::warn!("Failed to pull from main branch: {}", e);
                    }
                }
            } else {
                fetch_colocated(
                    &local_tree.branch().controldir(),
                    &main_branch.controldir(),
                    &self.additional_colocated_branches,
                )?;
            }
        }

        self.state = Some(WorkspaceState {
            base_revid: local_tree.last_revision().unwrap(),
            local_tree,
            refreshed,
            main_colo_revid,
            destroy_fn,
        });

        Ok(())
    }

    fn state(&self) -> &WorkspaceState {
        self.state.as_ref().unwrap()
    }

    pub fn builder() -> WorkspaceBuilder<'a> {
        WorkspaceBuilder::default()
    }

    pub fn main_branch(&self) -> Option<&dyn Branch> {
        self.main_branch
    }

    pub fn set_main_branch(&mut self, branch: &'a dyn Branch) -> Result<(), Error> {
        self.main_branch = Some(branch);
        Ok(())
    }

    pub fn local_tree(&self) -> &WorkingTree {
        &self.state().local_tree
    }

    pub fn refreshed(&self) -> bool {
        self.state().refreshed
    }

    pub fn resume_branch(&self) -> Option<&dyn Branch> {
        self.resume_branch
    }

    pub fn path(&self) -> PathBuf {
        self.local_tree()
            .abspath(std::path::Path::new("."))
            .unwrap()
    }

    pub fn changes_since_main(&self) -> bool {
        Some(self.local_tree().branch().last_revision())
            != self.main_branch().map(|b| b.last_revision())
    }

    pub fn changes_since_base(&self) -> bool {
        Some(self.local_tree().branch().last_revision()) != self.base_revid()
    }

    pub fn base_revid(&self) -> Option<RevisionId> {
        self.state.as_ref().map(|s| s.base_revid.clone())
    }

    /// Have any branch changes at all been made?
    ///
    /// Includes changes that already existed in the resume branch
    pub fn any_branch_changes(&self) -> bool {
        self.changed_branches().iter().any(|(_, br, r)| br != r)
    }

    pub fn additional_colocated_branches(&self) -> HashMap<String, String> {
        self.additional_colocated_branches
            .iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect()
    }

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

    pub fn main_colo_revid(&self) -> HashMap<String, RevisionId> {
        self.state().main_colo_revid.clone()
    }

    pub fn base_tree(&self) -> Box<dyn breezyshim::tree::Tree> {
        self.state()
            .local_tree
            .revision_tree(&self.state().base_revid)
            .unwrap()
    }

    pub fn defer_destroy(&mut self) {
        self.state.as_mut().unwrap().destroy_fn = None;
    }

    pub fn publish_changes(
        &self,
        target_branch: Option<&dyn Branch>,
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
    ) -> Result<PublishResult, PublishError> {
        let main_branch = self.main_branch();
        crate::publish::publish_changes(
            self.local_tree().branch().as_ref(),
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
        )
    }

    pub fn propose(
        &self,
        name: &str,
        description: &str,
        target_branch: Option<&dyn Branch>,
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
    ) -> Result<(MergeProposal, bool), Error> {
        let main_branch = self.main_branch();
        let target_branch = target_branch.or(main_branch).unwrap();
        let forge = if let Some(forge) = forge {
            forge
        } else {
            breezyshim::forge::get_forge(target_branch)?
        };
        crate::publish::propose_changes(
            self.local_tree().branch().as_ref(),
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
        )
        .map_err(|e| e.into())
    }

    pub fn push_derived(
        &self,
        name: &str,
        target_branch: Option<&dyn Branch>,
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
            breezyshim::forge::get_forge(target_branch)?
        };
        crate::publish::push_derived_changes(
            self.local_tree().branch().as_ref(),
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

    pub fn push_tags(&self, tags: HashMap<String, RevisionId>) -> Result<(), Error> {
        self.push(Some(tags))
    }

    pub fn push(&self, tags: Option<HashMap<String, RevisionId>>) -> Result<(), Error> {
        let main_branch = self.main_branch().unwrap();

        let forge = match breezyshim::forge::get_forge(main_branch) {
            Ok(forge) => Some(forge),
            Err(breezyshim::forge::Error::UnsupportedForge(e)) => {
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
            self.local_tree().branch().as_ref(),
            main_branch,
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
        let mut result = vec![];
        for (k, v) in self.additional_colocated_branches().iter() {
            result.push((v.to_string(), k.to_string()));
        }
        result
    }

    pub fn show_diff(
        &self,
        outf: Box<dyn std::io::Write + Send>,
        old_label: Option<&str>,
        new_label: Option<&str>,
    ) -> Result<(), PyErr> {
        breezyshim::diff::show_diff_trees(
            self.base_tree().as_ref(),
            &self.local_tree().basis_tree(),
            outf,
            old_label,
            new_label,
        )
    }

    pub fn destroy(&mut self) -> Result<(), Error> {
        if let Some(state) = self.state.as_mut() {
            if let Some(destroy_fn) = state.destroy_fn.take() {
                destroy_fn()?;
            }
        }
        self.state = None;
        Ok(())
    }
}

impl Drop for Workspace<'_> {
    fn drop(&mut self) {
        if let Some(state) = self.state.as_mut() {
            if let Some(destroy_fn) = state.destroy_fn.take() {
                match destroy_fn() {
                    Ok(()) => {}
                    Err(e) => {
                        log::error!("Error destroying workspace: {}", e);
                    }
                }
            }
        }
    }
}

#[test]
fn test_create_workspace() {
    let mut ws = Workspace::builder().build().unwrap();

    assert_eq!(ws.local_tree().branch().name().as_ref().unwrap(), "");

    assert_eq!(
        ws.base_revid(),
        Some(breezyshim::revisionid::RevisionId::null())
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
        .commit("test commit", Some(true), None, None)
        .unwrap();

    assert!(ws.changes_since_main());
    assert!(ws.changes_since_base());
    assert_eq!(
        ws.changed_branches(),
        vec![("".to_string(), None, Some(revid))]
    );

    ws.destroy().unwrap();
}
