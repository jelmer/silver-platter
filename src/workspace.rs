use crate::publish::{DescriptionFormat, Error as PublishError, PublishResult};
use breezyshim::branch::{open as open_branch, Branch, BranchOpenError};
use breezyshim::forge::{Forge, MergeProposal};
use breezyshim::tree::{RevisionTree, WorkingTree};
use breezyshim::ControlDir;
use breezyshim::RevisionId;
use log::info;
use pyo3::prelude::*;
use pyo3::types::PyDict;
use pyo3::PyErr;
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use url::Url;

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
                    None,
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

#[derive(Debug)]
pub enum Error {
    Python(PyErr),
    ForgeError(breezyshim::forge::Error),
}

impl From<BranchOpenError> for Error {
    fn from(e: BranchOpenError) -> Self {
        Error::Python(e.into())
    }
}

impl From<breezyshim::forge::Error> for Error {
    fn from(e: breezyshim::forge::Error) -> Self {
        Error::ForgeError(e)
    }
}

impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
        match self {
            Error::Python(e) => write!(f, "{}", e),
            Error::ForgeError(e) => write!(f, "{}", e),
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

    pub fn build(self) -> Result<Workspace, Error> {
        let ws = Workspace::new(
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

pub struct Workspace(PyObject);

impl Workspace {
    pub fn new(
        main_branch: Option<&dyn Branch>,
        resume_branch: Option<&dyn Branch>,
        cached_branch: Option<&dyn Branch>,
        additional_colocated_branches: HashMap<&str, &str>,
        resume_branch_additional_colocated_branches: HashMap<&str, &str>,
        dir: Option<&Path>,
        path: Option<&Path>,
        format: Option<&str>,
    ) -> Self {
        Python::with_gil(|py| {
            let m = py.import("silver_platter.workspace").unwrap();
            let workspace_cls = m.getattr("Workspace").unwrap();
            let kwargs = PyDict::new(py);
            kwargs.set_item("main_branch", main_branch).unwrap();
            kwargs.set_item("resume_branch", resume_branch).unwrap();
            kwargs.set_item("cached_branch", cached_branch).unwrap();
            kwargs
                .set_item(
                    "additional_colocated_branches",
                    additional_colocated_branches,
                )
                .unwrap();
            kwargs
                .set_item(
                    "resume_branch_additional_colocated_branches",
                    resume_branch_additional_colocated_branches,
                )
                .unwrap();
            kwargs.set_item("dir", dir).unwrap();
            kwargs.set_item("path", path).unwrap();
            kwargs.set_item("format", format).unwrap();
            let workspace = workspace_cls.call((), Some(kwargs)).unwrap();
            Workspace(workspace.into())
        })
    }

    pub fn builder<'a>() -> WorkspaceBuilder<'a> {
        WorkspaceBuilder::default()
    }

    pub fn main_branch(&self) -> Option<Box<dyn Branch>> {
        Python::with_gil(|py| -> Option<Box<dyn Branch>> {
            let branch = self.0.getattr(py, "main_branch").unwrap();
            if branch.is_none(py) {
                return None;
            }
            Some(Box::new(breezyshim::branch::RegularBranch::new(branch)))
        })
    }

    pub fn set_main_branch(&self, branch: &dyn Branch) -> Result<(), Error> {
        Python::with_gil(|py| {
            self.0.setattr(py, "main_branch", branch.to_object(py))?;
            Ok(())
        })
    }

    pub fn set_main_branch_url(&self, url: &Url) -> Result<(), Error> {
        self.set_main_branch(breezyshim::branch::open(url)?.as_ref())
    }

    pub fn local_tree(&self) -> WorkingTree {
        Python::with_gil(|py| {
            let tree = self.0.getattr(py, "local_tree").unwrap();
            WorkingTree(tree)
        })
    }

    pub fn resume_branch(&self) -> Option<Box<dyn Branch>> {
        Python::with_gil(|py| {
            let branch: Option<PyObject> = self
                .0
                .getattr(py, "resume_branch")
                .unwrap()
                .extract(py)
                .unwrap();
            branch.map(|b| Box::new(breezyshim::branch::RegularBranch::new(b)) as Box<dyn Branch>)
        })
    }

    pub fn from_url(
        url: &Url,
        resume_branch: Option<&dyn Branch>,
        cached_branch: Option<&dyn Branch>,
        additional_colocated_branches: HashMap<&str, &str>,
        resume_branch_additional_colocated_branches: HashMap<&str, &str>,
        dir: Option<&Path>,
        path: Option<&Path>,
        format: Option<&str>,
    ) -> Self {
        let main_branch = open_branch(url).unwrap();
        Self::new(
            Some(main_branch.as_ref()),
            resume_branch,
            cached_branch,
            additional_colocated_branches,
            resume_branch_additional_colocated_branches,
            dir,
            path,
            format,
        )
    }

    pub fn path(&self) -> PathBuf {
        Python::with_gil(|py| {
            let path = self.0.call_method0(py, "path").unwrap();
            path.extract(py).unwrap()
        })
    }

    pub fn start(&self) -> Result<(), Error> {
        Python::with_gil(|py| {
            self.0.call_method0(py, "__enter__")?;
            Ok(())
        })
    }

    pub fn changes_since_main(&self) -> bool {
        Some(self.local_tree().branch().last_revision())
            != self.main_branch().map(|b| b.last_revision())
    }

    pub fn changes_since_base(&self) -> bool {
        Some(self.local_tree().branch().last_revision()) != self.base_revid()
    }

    pub fn base_revid(&self) -> Option<RevisionId> {
        Python::with_gil(|py| {
            self.0
                .getattr(py, "base_revid")
                .unwrap()
                .extract(py)
                .unwrap()
        })
    }

    /// Have any branch changes at all been made?
    ///
    /// Includes changes that already existed in the resume branch
    pub fn any_branch_changes(&self) -> bool {
        self.changed_branches().iter().any(|(_, br, r)| br != r)
    }

    pub fn additional_colocated_branches(&self) -> HashMap<String, String> {
        Python::with_gil(|py| {
            self.0
                .getattr(py, "additional_colocated_branches")
                .unwrap()
                .extract(py)
                .unwrap()
        })
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
                Err(BranchOpenError::NoColocatedBranchSupport) => continue,
                Err(BranchOpenError::NotBranchError(..)) => None,
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
        Python::with_gil(|py| {
            self.0
                .getattr(py, "main_colo_revid")
                .unwrap()
                .extract(py)
                .unwrap()
        })
    }

    pub fn base_tree(&self) -> RevisionTree {
        Python::with_gil(|py| {
            let tree = self.0.call_method0(py, "base_tree").unwrap();
            RevisionTree(tree)
        })
    }

    pub fn defer_destroy(&self) {
        Python::with_gil(|py| {
            self.0.call_method0(py, "defer_destroy").unwrap();
        })
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
            target_branch.or(main_branch.as_deref()).unwrap(),
            self.resume_branch().as_deref(),
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
        let target_branch = target_branch.or(main_branch.as_deref()).unwrap();
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
            self.resume_branch().as_deref(),
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

    pub fn push_tags(&self, tags: HashMap<String, RevisionId>) -> Result<(), Error> {
        Python::with_gil(|py| {
            let kwargs = PyDict::new(py);
            kwargs.set_item(
                "tags",
                tags.into_iter()
                    .map(|(k, v)| (k, (&v).to_object(py)))
                    .collect::<HashMap<_, _>>(),
            )?;
            self.0.call_method(py, "push_tags", (), Some(kwargs))?;
            Ok(())
        })
    }

    pub fn push(&self) -> Result<(), Error> {
        let main_branch = self.main_branch().unwrap();

        let forge = match breezyshim::forge::get_forge(main_branch.as_ref()) {
            Ok(forge) => Some(forge),
            Err(breezyshim::forge::Error::UnsupportedForge(e)) => {
                // We can't figure out what branch to resume from when there's no forge
                // that can tell us.
                log::warn!(
                    "Unsupported forge ({}), will attempt to push to {}",
                    e,
                    crate::vcs::full_branch_url(main_branch.as_ref()),
                );
                None
            }
            Err(e) => {
                return Err(e.into());
            }
        };

        crate::publish::push_changes(
            self.local_tree().branch().as_ref(),
            main_branch.as_ref(),
            forge.as_ref(),
            None,
            Some(
                self.inverse_additional_colocated_branches()
                    .into_iter()
                    .collect(),
            ),
            None,
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
            &self.base_tree(),
            self.local_tree().basis_tree().as_ref(),
            outf,
            old_label,
            new_label,
        )
    }
}

impl Drop for Workspace {
    fn drop(&mut self) {
        Python::with_gil(|py| {
            self.0
                .call_method1(py, "__exit__", (py.None(), py.None(), py.None()))
                .unwrap();
        })
    }
}

#[test]
fn test_create_workspace() {
    let ws = Workspace::builder().build().unwrap();

    assert_eq!(ws.local_tree().branch().name().as_ref().unwrap(), "");
}
