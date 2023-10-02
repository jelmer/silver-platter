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
pub enum Error {}

impl std::fmt::Display for Error {
    fn fmt(&self, _f: &mut std::fmt::Formatter) -> std::fmt::Result {
        todo!()
    }
}

impl From<PyErr> for Error {
    fn from(_e: PyErr) -> Self {
        todo!()
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

    pub fn main_branch(&self) -> Box<dyn Branch> {
        Python::with_gil(|py| {
            let branch = self.0.getattr(py, "main_branch").unwrap();
            Box::new(breezyshim::branch::RegularBranch::new(branch))
        })
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
        Python::with_gil(|py| {
            self.0
                .call_method0(py, "changes_since_main")
                .unwrap()
                .extract(py)
                .unwrap()
        })
    }

    pub fn changes_since_base(&self) -> bool {
        Python::with_gil(|py| {
            self.0
                .call_method0(py, "changes_since_base")
                .unwrap()
                .extract(py)
                .unwrap()
        })
    }

    pub fn any_branch_changes(&self) -> bool {
        Python::with_gil(|py| {
            self.0
                .call_method0(py, "any_branch_changes")
                .unwrap()
                .extract(py)
                .unwrap()
        })
    }

    pub fn changed_branches(&self) -> Vec<(String, Option<RevisionId>, Option<RevisionId>)> {
        Python::with_gil(|py| {
            self.0
                .call_method0(py, "changed_branches")
                .unwrap()
                .extract::<Vec<(String, Option<RevisionId>, Option<RevisionId>)>>(py)
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
        let _target_branch = target_branch.unwrap_or_else(|| main_branch.as_ref());
        crate::publish::publish_changes(
            self.local_tree().branch().as_ref(),
            self.main_branch().as_ref(),
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
            self.0.call_method0(py, "__exit__").unwrap();
        })
    }
}
