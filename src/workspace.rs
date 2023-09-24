use breezyshim::branch::{open as open_branch, Branch, BranchOpenError};
use breezyshim::tree::RevisionTree;
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

    pub fn start(&self) -> Result<(), PyErr> {
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
}
