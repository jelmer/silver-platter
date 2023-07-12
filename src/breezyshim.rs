use pyo3::import_exception;
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict};

import_exception!(breezy.commit, PointlessCommit);

pub struct WorkingTree(PyObject);

#[derive(Debug, PartialEq, Eq, PartialOrd, Ord, Clone)]
pub struct RevisionId(Vec<u8>);

impl RevisionId {
    pub fn as_bytes(&self) -> &[u8] {
        self.0.as_slice()
    }

    pub fn new(bytes: Vec<u8>) -> RevisionId {
        RevisionId(bytes)
    }
}

impl ToString for RevisionId {
    fn to_string(&self) -> String {
        String::from_utf8(self.0.clone()).unwrap()
    }
}

impl FromPyObject<'_> for RevisionId {
    fn extract(ob: &PyAny) -> PyResult<Self> {
        let bytes = ob.extract::<Vec<u8>>()?;
        Ok(RevisionId(bytes))
    }
}

impl ToPyObject for RevisionId {
    fn to_object(&self, py: Python) -> PyObject {
        PyBytes::new(py, self.0.as_slice()).to_object(py)
    }
}

impl IntoPy<PyObject> for RevisionId {
    fn into_py(self, py: Python) -> PyObject {
        PyBytes::new(py, self.0.as_slice()).to_object(py)
    }
}

impl WorkingTree {
    pub fn new(obj: PyObject) -> Result<WorkingTree, PyErr> {
        Ok(WorkingTree(obj))
    }

    pub fn last_revision(&self) -> Result<RevisionId, PyErr> {
        Python::with_gil(|py| {
            let last_revision = self.0.call_method0(py, "last_revision")?;
            Ok(RevisionId(last_revision.extract::<Vec<u8>>(py)?))
        })
    }

    pub fn abspath(&self, subpath: &str) -> Result<std::path::PathBuf, PyErr> {
        Python::with_gil(|py| {
            let abspath = self.0.call_method1(py, "abspath", (subpath,))?;
            abspath.extract(py)
        })
    }

    pub fn get_tag_dict(&self) -> Result<std::collections::HashMap<String, RevisionId>, PyErr> {
        Python::with_gil(|py| {
            let branch = self.0.getattr(py, "branch")?;
            let tags = branch.getattr(py, "tags")?;
            let tag_dict = tags.call_method0(py, "get_tag_dict")?;
            tag_dict.extract(py)
        })
    }

    pub fn commit(
        &self,
        message: &str,
        committer: Option<&str>,
        allow_pointless: bool,
    ) -> Result<RevisionId, CommitError> {
        Python::with_gil(|py| {
            let kwargs = PyDict::new(py);
            kwargs.set_item("allow_pointless", allow_pointless).unwrap();
            kwargs.set_item("message", message).unwrap();
            if let Some(committer) = committer {
                kwargs.set_item("committer", committer).unwrap();
            }
            let revid = self
                .0
                .call_method(py, "commit", (), Some(kwargs))
                .map_err(|e| {
                    if e.is_instance_of::<PointlessCommit>(py) {
                        CommitError::PointlessCommit
                    } else {
                        CommitError::Other(e)
                    }
                })?;
            Ok(revid.extract(py).unwrap())
        })
    }

    pub fn smart_add(&self, paths: &[&std::path::Path]) -> Result<(), PyErr> {
        let paths = paths
            .iter()
            .map(|p| p.to_str().unwrap())
            .collect::<Vec<_>>();
        Python::with_gil(|py| {
            self.0.call_method(py, "smart_add", (paths,), None)?;
            Ok(())
        })
    }
}

#[derive(Debug)]
pub enum CommitError {
    PointlessCommit,
    Other(PyErr),
}

impl std::fmt::Display for CommitError {
    fn fmt(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
        match self {
            CommitError::PointlessCommit => write!(f, "Pointless commit"),
            CommitError::Other(e) => write!(f, "Other error: {}", e),
        }
    }
}

impl std::error::Error for CommitError {}
