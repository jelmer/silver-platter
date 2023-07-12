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

pub struct Forge(PyObject);

fn py_tag_selector(py: Python, tag_selector: Box<dyn Fn(String) -> bool>) -> PyResult<PyObject> {
    #[pyclass(unsendable)]
    struct PyTagSelector(Box<dyn Fn(String) -> bool>);

    #[pymethods]
    impl PyTagSelector {
        fn __call__(&self, tag: String) -> bool {
            (self.0)(tag)
        }
    }
    Ok(PyTagSelector(tag_selector).into_py(py))
}

impl Forge {
    pub fn new(obj: PyObject) -> Self {
        Forge(obj)
    }

    pub fn publish_derived(
        &self,
        local_branch: &Branch,
        main_branch: &Branch,
        name: &str,
        overwrite_existing: Option<bool>,
        owner: Option<&str>,
        stop_revision: Option<&RevisionId>,
        tag_selector: Box<dyn Fn(String) -> bool>,
    ) -> PyResult<(Branch, url::Url)> {
        Python::with_gil(|py| {
            let kwargs = PyDict::new(py);
            kwargs.set_item("local_branch", &local_branch.0)?;
            kwargs.set_item("main_branch", &main_branch.0)?;
            kwargs.set_item("name", name)?;
            if let Some(overwrite_existing) = overwrite_existing {
                kwargs.set_item("overwrite_existing", overwrite_existing)?;
            }
            if let Some(owner) = owner {
                kwargs.set_item("owner", owner)?;
            }
            if let Some(stop_revision) = stop_revision {
                kwargs.set_item("stop_revision", stop_revision)?;
            }
            kwargs.set_item("tag_selector", py_tag_selector(py, tag_selector)?)?;
            let (b, u): (PyObject, String) = self
                .0
                .call_method(py, "publish_derived", (), Some(kwargs))?
                .extract(py)?;
            Ok((Branch(b), u.parse::<url::Url>().unwrap()))
        })
    }
}

impl FromPyObject<'_> for Forge {
    fn extract(ob: &PyAny) -> PyResult<Self> {
        Ok(Forge(ob.to_object(ob.py())))
    }
}

impl ToPyObject for Forge {
    fn to_object(&self, py: Python) -> PyObject {
        self.0.to_object(py)
    }
}

pub struct Branch(PyObject);

impl Branch {
    pub fn new(obj: PyObject) -> Self {
        Branch(obj)
    }

    pub fn get_controldir(&self) -> ControlDir {
        Python::with_gil(|py| ControlDir::new(self.0.getattr(py, "controldir").unwrap()).unwrap())
    }

    pub fn push(
        &self,
        remote_branch: &Branch,
        overwrite: bool,
        stop_revision: Option<&RevisionId>,
        tag_selector: Box<dyn Fn(String) -> bool>,
    ) -> PyResult<()> {
        Python::with_gil(|py| {
            let kwargs = PyDict::new(py);
            kwargs.set_item("overwrite", overwrite)?;
            if let Some(stop_revision) = stop_revision {
                kwargs.set_item("stop_revision", stop_revision)?;
            }
            kwargs.set_item("tag_selector", py_tag_selector(py, tag_selector)?)?;
            self.0
                .call_method(py, "push", (&remote_branch.0,), Some(kwargs))?;
            Ok(())
        })
    }
}

impl FromPyObject<'_> for Branch {
    fn extract(ob: &PyAny) -> PyResult<Self> {
        Ok(Branch(ob.to_object(ob.py())))
    }
}

impl ToPyObject for Branch {
    fn to_object(&self, py: Python) -> PyObject {
        self.0.to_object(py)
    }
}

pub struct ControlDir(PyObject);

impl ControlDir {
    pub fn new(obj: PyObject) -> PyResult<Self> {
        Ok(Self(obj))
    }

    pub fn open_branch(&self, branch_name: Option<&str>) -> PyResult<Branch> {
        Python::with_gil(|py| {
            let branch = self
                .0
                .call_method(py, "open_branch", (branch_name,), None)?
                .extract(py)?;
            Ok(Branch(branch))
        })
    }

    pub fn push_branch(
        &self,
        source_branch: &Branch,
        to_branch_name: Option<&str>,
        tag_selector: Box<dyn Fn(String) -> bool>,
    ) -> PyResult<Branch> {
        Python::with_gil(|py| {
            let kwargs = PyDict::new(py);
            if let Some(to_branch_name) = to_branch_name {
                kwargs.set_item("name", to_branch_name)?;
            }
            kwargs.set_item("tag_selector", py_tag_selector(py, tag_selector)?)?;
            let result =
                self.0
                    .call_method(py, "push_branch", (&source_branch.0,), Some(kwargs))?;
            Ok(Branch(result.getattr(py, "target_branch")?))
        })
    }
}

pub fn get_forge(branch: &Branch) -> Forge {
    Python::with_gil(|py| {
        let m = py.import("breezy.forge").unwrap();
        let forge = m.call_method1("get_forge", (&branch.0,)).unwrap();
        Forge(forge.to_object(py))
    })
}

pub fn determine_title(description: &str) -> String {
    Python::with_gil(|py| {
        let m = py.import("breezy.forge").unwrap();
        let title = m.call_method1("determine_title", (description,)).unwrap();
        title.extract::<String>()
    })
    .unwrap()
}
