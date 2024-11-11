use pyo3::exceptions::{PyRuntimeError, PyTypeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyType};
use pyo3::{create_exception, import_exception};
use silver_platter::codemod::Error as CodemodError;
use silver_platter::{CommitPending, Mode};
use silver_platter::{RevisionId, WorkingTree};
use std::collections::HashMap;
use std::os::unix::io::FromRawFd;
use std::path::{Path, PathBuf};

create_exception!(
    silver_platter,
    UnrelatedBranchExists,
    pyo3::exceptions::PyException
);

create_exception!(
    silver_platter,
    PreCheckFailed,
    pyo3::exceptions::PyException
);

create_exception!(
    silver_platter,
    PostCheckFailed,
    pyo3::exceptions::PyException
);

create_exception!(
    silver_platter,
    ScriptMadeNoChanges,
    pyo3::exceptions::PyException
);
create_exception!(silver_platter, ScriptFailed, pyo3::exceptions::PyException);
create_exception!(
    silver_platter,
    ScriptNotFound,
    pyo3::exceptions::PyException
);
create_exception!(
    silver_platter,
    DetailedFailure,
    pyo3::exceptions::PyException
);
create_exception!(
    silver_platter,
    ResultFileFormatError,
    pyo3::exceptions::PyException
);
create_exception!(
    silver_platter,
    InsufficientChangesForNewProposal,
    pyo3::exceptions::PyException
);
create_exception!(
    silver_platter,
    EmptyMergeProposal,
    pyo3::exceptions::PyException
);
create_exception!(
    silver_platter,
    MissingChangelog,
    pyo3::exceptions::PyException
);
import_exception!(breezy.errors, DivergedBranches);

#[pyclass]
struct Recipe(silver_platter::recipe::Recipe);

fn json_to_py<'a, 'b, 'py>(py: Python<'py>, value: &'b serde_json::Value) -> Bound<'a, PyAny>
where
    'py: 'a,
{
    match value {
        serde_json::Value::Null => py.None().into_bound(py),
        serde_json::Value::Bool(b) => {
            let o = pyo3::types::PyBool::new_bound(py, *b).into_py(py);
            o.into_bound(py)
        }
        serde_json::Value::Number(n) => {
            let n: PyObject = if let Some(n) = n.as_u64() {
                n.into_py(py)
            } else if let Some(n) = n.as_i64() {
                n.into_py(py)
            } else if let Some(n) = n.as_f64() {
                n.into_py(py)
            } else {
                unreachable!()
            };
            n.into_bound(py)
        }
        serde_json::Value::String(s) => pyo3::types::PyString::new_bound(py, s.as_str()).into_any(),
        serde_json::Value::Array(a) => {
            let list = pyo3::types::PyList::empty_bound(py);
            for v in a {
                list.append(json_to_py(py, v)).unwrap();
            }
            list.into_any()
        }
        serde_json::Value::Object(o) => {
            let dict = pyo3::types::PyDict::new_bound(py);
            for (k, v) in o {
                dict.set_item(k, json_to_py(py, v)).unwrap();
            }
            dict.into_any()
        }
    }
}

fn py_to_json(obj: &Bound<PyAny>) -> PyResult<serde_json::Value> {
    if obj.is_none() {
        Ok(serde_json::Value::Null)
    } else if let Ok(b) = obj.downcast::<pyo3::types::PyBool>() {
        Ok(serde_json::Value::Bool(b.is_true()))
    } else if let Ok(f) = obj.downcast::<pyo3::types::PyFloat>() {
        Ok(serde_json::Value::Number(
            serde_json::Number::from_f64(f.value()).unwrap(),
        ))
    } else if let Ok(s) = obj.downcast::<pyo3::types::PyString>() {
        Ok(serde_json::Value::String(s.to_string_lossy().to_string()))
    } else if let Ok(l) = obj.downcast::<pyo3::types::PyList>() {
        Ok(serde_json::Value::Array(
            l.iter()
                .map(|x| py_to_json(&x))
                .collect::<PyResult<Vec<_>>>()?,
        ))
    } else if let Ok(d) = obj.downcast::<pyo3::types::PyDict>() {
        let mut ret = serde_json::Map::new();
        for (k, v) in d.iter() {
            let k = k.extract::<String>()?;
            let v = py_to_json(&v)?;
            ret.insert(k, v);
        }
        Ok(serde_json::Value::Object(ret))
    } else {
        Err(PyTypeError::new_err(("unsupported type",)))
    }
}

#[pymethods]
impl Recipe {
    #[classmethod]
    fn from_path(_type: &Bound<PyType>, path: PathBuf) -> PyResult<Self> {
        let recipe = silver_platter::recipe::Recipe::from_path(path.as_path())?;
        Ok(Recipe(recipe))
    }

    #[getter]
    fn name(&self) -> Option<&str> {
        self.0.name.as_deref()
    }

    #[getter]
    fn resume(&self) -> Option<bool> {
        self.0.resume
    }

    #[getter]
    fn labels(&self) -> Option<Vec<String>> {
        self.0.labels.clone()
    }

    #[getter]
    fn commit_pending(&self) -> Option<bool> {
        match self.0.commit_pending {
            CommitPending::Auto => None,
            CommitPending::Yes => Some(true),
            CommitPending::No => Some(false),
        }
    }

    #[getter]
    fn command(&self) -> Option<Vec<String>> {
        self.0.command.as_ref().map(|v| v.argv())
    }

    #[getter]
    fn mode(&self) -> Option<String> {
        self.0.mode.as_ref().map(|m| m.to_string())
    }

    fn render_merge_request_title(&self, context: &Bound<PyAny>) -> PyResult<Option<String>> {
        let merge_request = if let Some(mp) = self.0.merge_request.as_ref() {
            mp
        } else {
            return Ok(None);
        };
        let context = py_dict_to_tera_context(context)?;
        merge_request.render_title(&context).map_err(|e| {
            PyRuntimeError::new_err(format!("Failed to render merge request title: {}", e))
        })
    }

    fn render_merge_request_commit_message(
        &self,
        context: &Bound<PyAny>,
    ) -> PyResult<Option<String>> {
        let merge_request = if let Some(mp) = self.0.merge_request.as_ref() {
            mp
        } else {
            return Ok(None);
        };
        let context = py_dict_to_tera_context(context)?;
        merge_request.render_commit_message(&context).map_err(|e| {
            PyRuntimeError::new_err(format!(
                "Failed to render merge request commit message: {}",
                e
            ))
        })
    }

    fn render_merge_request_description(
        &self,
        format: &str,
        context: &Bound<PyAny>,
    ) -> PyResult<Option<String>> {
        let merge_request = if let Some(mp) = self.0.merge_request.as_ref() {
            mp
        } else {
            return Ok(None);
        };
        let context = py_dict_to_tera_context(context)?;
        let format = match format {
            "markdown" => silver_platter::proposal::DescriptionFormat::Markdown,
            "html" => silver_platter::proposal::DescriptionFormat::Html,
            "plain" => silver_platter::proposal::DescriptionFormat::Plain,
            _ => {
                return Err(PyValueError::new_err(format!(
                    "Invalid merge request description format: {}",
                    format
                )))
            }
        };
        merge_request
            .render_description(format, &context)
            .map_err(|e| {
                PyRuntimeError::new_err(format!(
                    "Failed to render merge request description: {}",
                    e
                ))
            })
    }
}

fn py_dict_to_tera_context(py_dict: &Bound<PyAny>) -> PyResult<tera::Context> {
    let mut context = tera::Context::new();
    if py_dict.is_none() {
        return Ok(context);
    }
    let py_dict = py_dict.extract::<Bound<PyDict>>()?;
    for (key, value) in py_dict.iter() {
        let key = key.extract::<String>()?;
        if let Ok(value) = value.extract::<String>() {
            context.insert(key, &value);
        } else if let Ok(value) = value.extract::<usize>() {
            context.insert(key, &value);
        } else {
            return Err(PyTypeError::new_err(format!(
                "Unsupported type for key '{}'",
                key
            )));
        }
    }
    Ok(context)
}

#[pyfunction]
fn derived_branch_name(url: &str) -> PyResult<&str> {
    let branch_name = silver_platter::derived_branch_name(url);
    Ok(branch_name)
}

#[pyclass]
struct CommandResult(silver_platter::codemod::CommandResult);

#[pymethods]
impl CommandResult {
    #[getter]
    fn value(&self) -> Option<u32> {
        self.0.value
    }

    #[getter]
    fn description(&self) -> Option<&str> {
        self.0.description.as_deref()
    }

    #[getter]
    fn serialized_context(&self) -> Option<&str> {
        self.0.serialized_context.as_deref()
    }

    #[getter]
    fn tags(&self) -> Vec<(String, Option<RevisionId>)> {
        self.0.tags.clone()
    }

    #[getter]
    fn target_branch_url(&self) -> Option<&str> {
        self.0.target_branch_url.as_ref().map(|u| u.as_str())
    }

    #[getter]
    fn old_revision(&self) -> RevisionId {
        self.0.old_revision.clone()
    }

    #[getter]
    fn new_revision(&self) -> RevisionId {
        self.0.new_revision.clone()
    }

    #[getter]
    fn context<'a, 'py>(&self, py: Python<'py>) -> Option<Bound<'a, PyAny>>
    where
        'py: 'a,
    {
        self.0.context.as_ref().map(|c| json_to_py(py, c))
    }
}

#[pyfunction]
#[pyo3(signature = (local_tree, script, subpath=None, commit_pending=None, resume_metadata=None, committer=None, extra_env=None, stderr=None))]
fn script_runner(
    py: Python,
    local_tree: PyObject,
    script: PyObject,
    subpath: Option<PathBuf>,
    commit_pending: Option<bool>,
    resume_metadata: Option<PyObject>,
    committer: Option<&str>,
    extra_env: Option<std::collections::HashMap<String, String>>,
    stderr: Option<PyObject>,
) -> PyResult<PyObject> {
    let script = if let Ok(script) = script.extract::<Vec<String>>(py) {
        script
    } else {
        vec![
            "sh".to_string(),
            "-c".to_string(),
            script.extract::<String>(py)?,
        ]
    };

    silver_platter::codemod::script_runner(
        &WorkingTree::from(local_tree),
        script
            .iter()
            .map(|s| s.as_str())
            .collect::<Vec<_>>()
            .as_slice(),
        subpath
            .as_ref()
            .map_or_else(|| std::path::Path::new(""), |p| p.as_path()),
        match commit_pending {
            None => CommitPending::Auto,
            Some(true) => CommitPending::Yes,
            Some(false) => CommitPending::No,
        },
        resume_metadata
            .map(|m| py_to_json(m.bind(py)).unwrap())
            .as_ref(),
        committer,
        extra_env,
        if let Some(stderr) = stderr {
            let fd = stderr
                .call_method0(py, "fileno")?
                .extract::<i32>(py)
                .unwrap();
            let f = unsafe { std::fs::File::from_raw_fd(fd) };
            std::process::Stdio::from(f)
        } else {
            std::process::Stdio::inherit()
        },
    )
    .map(|result| CommandResult(result).into_py(py))
    .map_err(|err| match err {
        CodemodError::ScriptMadeNoChanges => ScriptMadeNoChanges::new_err("Script made no changes"),
        CodemodError::ExitCode(code) => {
            ScriptFailed::new_err(format!("Script failed with exit code {}", code))
        }
        CodemodError::ScriptNotFound => ScriptNotFound::new_err("Script not found"),
        CodemodError::Detailed(df) => {
            DetailedFailure::new_err(format!("Script failed: {}", df.description.unwrap()))
        }
        CodemodError::Json(err) => {
            ResultFileFormatError::new_err(format!("Result file format error: {}", err))
        }
        CodemodError::Io(err) => err.into(),
        CodemodError::Other(err) => PyRuntimeError::new_err(format!("Script failed: {}", err)),
        CodemodError::Utf8(err) => err.into(),
    })
}

#[pyclass]
struct Forge(silver_platter::Forge);

#[pyfunction]
#[pyo3(signature = (local_branch, main_branch, forge, name, overwrite_existing=None, owner=None, tags=None, stop_revision=None))]
fn push_derived_changes(
    py: Python,
    local_branch: PyObject,
    main_branch: PyObject,
    forge: PyObject,
    name: &str,
    overwrite_existing: Option<bool>,
    owner: Option<&str>,
    tags: Option<std::collections::HashMap<String, RevisionId>>,
    stop_revision: Option<RevisionId>,
) -> PyResult<(PyObject, String)> {
    let (b, u) = silver_platter::publish::push_derived_changes(
        &silver_platter::GenericBranch::new(local_branch),
        &silver_platter::GenericBranch::new(main_branch),
        &silver_platter::Forge::from(forge),
        name,
        overwrite_existing,
        owner,
        tags,
        stop_revision.as_ref(),
    )?;
    Ok((b.to_object(py), u.to_string()))
}

#[pyclass]
struct CandidateList(silver_platter::candidates::Candidates);

#[pymethods]
impl CandidateList {
    #[classmethod]
    fn from_path(_type: &Bound<PyType>, path: PathBuf) -> PyResult<Self> {
        Ok(Self(silver_platter::candidates::Candidates::from_path(
            path.as_path(),
        )?))
    }

    #[getter]
    fn candidates(&self) -> Vec<Candidate> {
        self.0
            .candidates()
            .iter()
            .map(|c| Candidate(c.clone()))
            .collect()
    }
}

#[pyclass]
struct Candidate(silver_platter::candidates::Candidate);

#[pymethods]
impl Candidate {
    #[getter]
    fn url(&self) -> &str {
        self.0.url.as_str()
    }

    #[getter]
    fn name(&self) -> Option<&str> {
        self.0.name.as_deref()
    }

    #[getter]
    fn branch(&self) -> Option<&str> {
        self.0.branch.as_deref()
    }

    #[getter]
    fn subpath(&self) -> Option<&Path> {
        self.0.subpath.as_deref()
    }

    #[getter]
    fn default_mode(&self) -> Option<String> {
        self.0.default_mode.as_ref().map(|m| m.to_string())
    }
}

#[pyfunction]
#[pyo3(signature = (local_branch, main_branch, forge=None, possible_transports=None, additional_colocated_branches=None, tags=None, stop_revision=None))]
fn push_changes(
    local_branch: PyObject,
    main_branch: PyObject,
    forge: Option<PyObject>,
    possible_transports: Option<Vec<PyObject>>,
    additional_colocated_branches: Option<Vec<(String, String)>>,
    tags: Option<std::collections::HashMap<String, RevisionId>>,
    stop_revision: Option<RevisionId>,
) -> PyResult<()> {
    let mut possible_transports: Option<Vec<silver_platter::Transport>> =
        possible_transports.map(|t| t.into_iter().map(silver_platter::Transport::new).collect());
    silver_platter::publish::push_changes(
        &silver_platter::GenericBranch::new(local_branch),
        &silver_platter::GenericBranch::new(main_branch),
        forge.map(silver_platter::Forge::from).as_ref(),
        possible_transports.as_mut(),
        additional_colocated_branches,
        tags,
        stop_revision.as_ref(),
    )?;
    Ok(())
}

#[pyfunction]
#[pyo3(signature = (local_branch, remote_branch, additional_colocated_branches=None, tags=None, stop_revision=None))]
fn push_result(
    local_branch: PyObject,
    remote_branch: PyObject,
    additional_colocated_branches: Option<Vec<(String, String)>>,
    tags: Option<std::collections::HashMap<String, RevisionId>>,
    stop_revision: Option<RevisionId>,
) -> PyResult<()> {
    silver_platter::publish::push_result(
        &silver_platter::GenericBranch::new(local_branch),
        &silver_platter::GenericBranch::new(remote_branch),
        additional_colocated_branches,
        tags,
        stop_revision.as_ref(),
    )?;
    Ok(())
}

#[pyfunction]
fn full_branch_url(branch: PyObject) -> PyResult<String> {
    Ok(
        silver_platter::vcs::full_branch_url(&silver_platter::GenericBranch::new(branch))
            .to_string(),
    )
}

#[pyclass]
struct MergeProposal(silver_platter::MergeProposal);

#[pyfunction]
#[pyo3(signature = (main_branch, forge, name, overwrite_unrelated, owner=None, preferred_schemes=None))]
fn find_existing_proposed(
    py: Python,
    main_branch: PyObject,
    forge: PyObject,
    name: &str,
    overwrite_unrelated: bool,
    owner: Option<&str>,
    preferred_schemes: Option<Vec<String>>,
) -> PyResult<(Option<PyObject>, Option<bool>, Option<Vec<MergeProposal>>)> {
    let main_branch = silver_platter::GenericBranch::new(main_branch);
    let forge = silver_platter::Forge::from(forge);
    let preferred_schemes = preferred_schemes
        .as_ref()
        .map(|s| s.iter().map(|s| s.as_ref()).collect::<Vec<_>>());
    let (b, o, p) = silver_platter::publish::find_existing_proposed(
        &main_branch,
        &forge,
        name,
        overwrite_unrelated,
        owner,
        preferred_schemes.as_deref(),
    )?;
    Ok((
        b.map(|x| x.to_object(py)),
        o,
        p.map(|p| p.into_iter().map(MergeProposal).collect()),
    ))
}

#[pyfunction]
#[pyo3(signature = (local_branch, main_branch, forge, name, mp_description, resume_branch=None, resume_proposal=None, overwrite_existing=None, labels=None, commit_message=None, title=None, additional_colocated_branches=None, allow_empty=None, reviewers=None, tags=None, owner=None, stop_revision=None, allow_collaboration=None, auto_merge=None))]
fn propose_changes(
    local_branch: PyObject,
    main_branch: PyObject,
    forge: &Forge,
    name: &str,
    mp_description: &str,
    resume_branch: Option<PyObject>,
    resume_proposal: Option<&MergeProposal>,
    overwrite_existing: Option<bool>,
    labels: Option<Vec<String>>,
    commit_message: Option<&str>,
    title: Option<&str>,
    additional_colocated_branches: Option<Vec<(String, String)>>,
    allow_empty: Option<bool>,
    reviewers: Option<Vec<String>>,
    tags: Option<std::collections::HashMap<String, RevisionId>>,
    owner: Option<&str>,
    stop_revision: Option<RevisionId>,
    allow_collaboration: Option<bool>,
    auto_merge: Option<bool>,
) -> PyResult<(MergeProposal, bool)> {
    let resume_branch = resume_branch.map(|b| breezyshim::branch::GenericBranch::new(b));
    silver_platter::publish::propose_changes(
        &breezyshim::branch::GenericBranch::new(local_branch),
        &breezyshim::branch::GenericBranch::new(main_branch),
        &forge.0,
        name,
        mp_description,
        resume_branch
            .as_ref()
            .map(|b| b as &dyn silver_platter::Branch),
        resume_proposal.as_ref().map(|p| p.0.clone()),
        overwrite_existing,
        labels,
        commit_message,
        title,
        additional_colocated_branches,
        allow_empty,
        reviewers,
        tags,
        owner,
        stop_revision.as_ref(),
        allow_collaboration,
        auto_merge,
    )
    .map(|(p, b)| (MergeProposal(p), b))
    .map_err(Into::into)
}

#[pyclass]
struct PublishResult(silver_platter::publish::PublishResult);

#[pymethods]
impl PublishResult {
    #[getter]
    fn is_new(&self) -> Option<bool> {
        self.0.is_new
    }
    #[getter]
    fn forge(&self, py: Python) -> Option<PyObject> {
        Some(self.0.forge.to_object(py))
    }
}

#[pyfunction]
#[pyo3(signature = (local_branch, main_branch, mode, name, get_proposal_description, resume_branch=None, get_proposal_commit_message=None, get_proposal_title=None, forge=None, allow_create_proposal=None, labels=None, overwrite_existing=None, existing_proposal=None, reviewers=None, tags=None, derived_owner=None, allow_collaboration=None, stop_revision=None, auto_merge=None))]
fn publish_changes(
    local_branch: PyObject,
    main_branch: PyObject,
    mode: Mode,
    name: &str,
    get_proposal_description: PyObject,
    resume_branch: Option<PyObject>,
    get_proposal_commit_message: Option<PyObject>,
    get_proposal_title: Option<PyObject>,
    forge: Option<&Forge>,
    allow_create_proposal: Option<bool>,
    labels: Option<Vec<String>>,
    overwrite_existing: Option<bool>,
    existing_proposal: Option<&MergeProposal>,
    reviewers: Option<Vec<String>>,
    tags: Option<std::collections::HashMap<String, RevisionId>>,
    derived_owner: Option<&str>,
    allow_collaboration: Option<bool>,
    stop_revision: Option<RevisionId>,
    auto_merge: Option<bool>,
) -> PyResult<PublishResult> {
    let get_proposal_description =
        |format: silver_platter::proposal::DescriptionFormat,
         proposal: Option<&silver_platter::MergeProposal>| {
            Python::with_gil(|py| {
                let proposal = proposal.map(|mp| MergeProposal(mp.clone()));
                get_proposal_description
                    .call1(py, (format.to_string(), proposal))
                    .unwrap()
                    .extract(py)
                    .unwrap()
            })
        };
    let get_proposal_commit_message = get_proposal_commit_message.map(|f| {
        move |proposal: Option<&silver_platter::MergeProposal>| -> Option<String> {
            Python::with_gil(|py| {
                let proposal = proposal.map(|mp| MergeProposal(mp.clone()));
                f.call1(py, (proposal,)).unwrap().extract(py).unwrap()
            })
        }
    });
    let get_proposal_title = get_proposal_title.map(|f| {
        move |proposal: Option<&silver_platter::MergeProposal>| -> Option<String> {
            Python::with_gil(|py| {
                let proposal = proposal.map(|mp| MergeProposal(mp.clone()));
                f.call1(py, (proposal,)).unwrap().extract(py).unwrap()
            })
        }
    });
    let resume_branch = resume_branch.map(breezyshim::branch::GenericBranch::new);
    Ok(PublishResult(silver_platter::publish::publish_changes(
        &breezyshim::branch::GenericBranch::new(local_branch),
        &breezyshim::branch::GenericBranch::new(main_branch),
        resume_branch
            .as_ref()
            .map(|b| b as &dyn silver_platter::Branch),
        mode,
        name,
        get_proposal_description,
        get_proposal_commit_message,
        get_proposal_title,
        forge.map(|f| &f.0),
        allow_create_proposal,
        labels,
        overwrite_existing,
        existing_proposal.map(|p| p.0.clone()),
        reviewers,
        tags,
        derived_owner,
        allow_collaboration,
        stop_revision.as_ref(),
        auto_merge,
    )?))
}

#[pyclass]
struct DestroyFn(Option<Box<dyn FnOnce() -> std::io::Result<()> + Send>>);

#[pymethods]
impl DestroyFn {
    fn __call__(&mut self) -> PyResult<()> {
        if let Some(f) = self.0.take() {
            Ok(f()?)
        } else {
            Err(PyRuntimeError::new_err("Already called"))
        }
    }
}

/// Run a script before making any changes to a tree.
///
/// Args:
///   tree: The working tree to operate in
///   script: Command to run
/// Raises:
///   PreCheckFailed: If the pre-check failed
#[pyfunction]
fn run_pre_check(tree: PyObject, script: &str) -> PyResult<()> {
    let tree = WorkingTree::from(tree);
    silver_platter::checks::run_pre_check(tree, script).map_err(|e| match e {
        silver_platter::checks::PreCheckFailed => PreCheckFailed::new_err(()),
    })
}

/// Run a script after making any changes to a tree.
///
/// Args:
///   tree: The working tree to operate in
///   script: Command to run
///   since_revid: The revision to run the script since
/// Raises:
///   PreCheckFailed: If the pre-check failed
#[pyfunction]
fn run_post_check(tree: PyObject, script: &str, since_revid: RevisionId) -> PyResult<()> {
    let tree = WorkingTree::from(tree);
    silver_platter::checks::run_post_check(tree, script, &since_revid).map_err(|e| match e {
        silver_platter::checks::PostCheckFailed => PostCheckFailed::new_err(()),
    })
}

#[pyfunction]
#[pyo3(signature = (local_branch, target_branch, stop_revision=None))]
fn check_proposal_diff(
    local_branch: PyObject,
    target_branch: PyObject,
    stop_revision: Option<RevisionId>,
) -> PyResult<()> {
    let local_branch = breezyshim::branch::GenericBranch::new(local_branch);
    let target_branch = breezyshim::branch::GenericBranch::new(target_branch);
    if silver_platter::publish::check_proposal_diff_empty(
        &local_branch,
        &target_branch,
        stop_revision.as_ref(),
    )? {
        Err(EmptyMergeProposal::new_err(()))
    } else {
        Ok(())
    }
}

#[cfg(feature = "debian")]
pub(crate) mod debian {
    use super::*;
    use silver_platter::debian::codemod::Error as DebianCodemodError;

    #[cfg(feature = "debian")]
    #[pyfunction]
    pub fn pick_additional_colocated_branches(main_branch: PyObject) -> HashMap<String, String> {
        silver_platter::debian::pick_additional_colocated_branches(
            &breezyshim::branch::GenericBranch::new(main_branch),
        )
    }

    #[pyclass]
    pub(crate) struct DebianCommandResult(silver_platter::debian::codemod::CommandResult);

    #[pymethods]
    impl DebianCommandResult {
        #[getter]
        fn value(&self) -> Option<u32> {
            self.0.value
        }

        #[getter]
        fn description(&self) -> &str {
            self.0.description.as_str()
        }

        #[getter]
        fn serialized_context(&self) -> Option<&str> {
            self.0.serialized_context.as_deref()
        }

        #[getter]
        fn tags(&self) -> Vec<(String, Option<RevisionId>)> {
            self.0.tags.clone()
        }

        #[getter]
        fn target_branch_url(&self) -> Option<&str> {
            self.0.target_branch_url.as_ref().map(|u| u.as_str())
        }

        #[getter]
        fn old_revision(&self) -> RevisionId {
            self.0.old_revision.clone()
        }

        #[getter]
        fn new_revision(&self) -> RevisionId {
            self.0.new_revision.clone()
        }

        #[getter]
        fn context<'a, 'py>(&self, py: Python<'py>) -> Option<Bound<'a, PyAny>>
        where
            'py: 'a,
        {
            self.0.context.as_ref().map(|c| json_to_py(py, c))
        }
    }

    #[pyfunction]
    #[pyo3(signature = (local_tree, script, subpath=None, commit_pending=None, resume_metadata=None, committer=None, extra_env=None, stderr=None, update_changelog=None))]
    pub(crate) fn debian_script_runner(
        py: Python,
        local_tree: PyObject,
        script: PyObject,
        subpath: Option<PathBuf>,
        commit_pending: Option<bool>,
        resume_metadata: Option<PyObject>,
        committer: Option<&str>,
        extra_env: Option<std::collections::HashMap<String, String>>,
        stderr: Option<PyObject>,
        update_changelog: Option<bool>,
    ) -> PyResult<PyObject> {
        let script = if let Ok(script) = script.extract::<Vec<String>>(py) {
            script
        } else {
            vec![
                "sh".to_string(),
                "-c".to_string(),
                script.extract::<String>(py)?,
            ]
        };

        silver_platter::debian::codemod::script_runner(
            &WorkingTree::from(local_tree),
            script
                .iter()
                .map(|s| s.as_str())
                .collect::<Vec<_>>()
                .as_slice(),
            subpath
                .as_ref()
                .map_or_else(|| std::path::Path::new(""), |p| p.as_path()),
            match commit_pending {
                Some(true) => CommitPending::Yes,
                Some(false) => CommitPending::No,
                None => CommitPending::Auto,
            },
            resume_metadata
                .map(|m| py_to_json(m.bind(py)).unwrap())
                .as_ref(),
            committer,
            extra_env,
            match stderr {
                Some(stderr) => {
                    let fd = stderr
                        .call_method0(py, "fileno")?
                        .extract::<i32>(py)
                        .unwrap();
                    let f = unsafe { std::fs::File::from_raw_fd(fd) };
                    std::process::Stdio::from(f)
                }
                None => std::process::Stdio::inherit(),
            },
            update_changelog,
        )
        .map(|result| DebianCommandResult(result).into_py(py))
        .map_err(|err| match err {
            DebianCodemodError::ScriptMadeNoChanges => {
                ScriptMadeNoChanges::new_err("Script made no changes")
            }
            DebianCodemodError::ExitCode(code) => {
                ScriptFailed::new_err(format!("Script failed with exit code {}", code))
            }
            DebianCodemodError::ScriptNotFound => ScriptNotFound::new_err("Script not found"),
            DebianCodemodError::Detailed(df) => {
                DetailedFailure::new_err(format!("Script failed: {}", df.description.unwrap()))
            }
            DebianCodemodError::Json(err) => {
                ResultFileFormatError::new_err(format!("Result file format error: {}", err))
            }
            DebianCodemodError::Io(err) => err.into(),
            DebianCodemodError::Other(err) => {
                PyRuntimeError::new_err(format!("Script failed: {}", err))
            }
            DebianCodemodError::Utf8(err) => err.into(),
            DebianCodemodError::ChangelogParse(e) => {
                MissingChangelog::new_err(format!("Failed to parse changelog {}", e))
            }
            DebianCodemodError::MissingChangelog(p) => {
                MissingChangelog::new_err(format!("Missing changelog entry for {}", p.display()))
            }
        })
    }

    #[pyfunction]
    pub(crate) fn get_maintainer_from_env(
        env: HashMap<String, String>,
    ) -> Option<(String, String)> {
        debian_changelog::get_maintainer_from_env(|k| env.get(k).map(|s| s.to_string()))
    }

    #[pyfunction]
    pub(crate) fn is_debcargo_package(tree: PyObject, path: &str) -> PyResult<bool> {
        let tree = WorkingTree::from(tree);
        Ok(silver_platter::debian::is_debcargo_package(
            &tree,
            std::path::Path::new(path),
        ))
    }

    #[pyfunction]
    pub(crate) fn control_files_in_root(tree: PyObject, path: &str) -> PyResult<bool> {
        let tree = WorkingTree::from(tree);
        Ok(silver_platter::debian::control_files_in_root(
            &tree,
            std::path::Path::new(path),
        ))
    }

    #[pyclass]
    pub(crate) struct ChangelogBehaviour(silver_platter::debian::ChangelogBehaviour);

    #[pymethods]
    impl ChangelogBehaviour {
        #[getter]
        fn get_update_changelog(&self) -> bool {
            self.0.update_changelog
        }

        #[getter]
        fn get_explanation(&self) -> String {
            self.0.explanation.clone()
        }
    }

    #[pyfunction]
    pub(crate) fn guess_update_changelog(
        tree: PyObject,
        debian_path: &str,
    ) -> Option<ChangelogBehaviour> {
        let tree = WorkingTree::from(tree);
        silver_platter::debian::guess_update_changelog(&tree, std::path::Path::new(debian_path))
            .map(ChangelogBehaviour)
    }

    #[pyfunction]
    #[pyo3(signature = (tree, subpath, builder=None, result_dir=None))]
    pub(crate) fn build(
        tree: PyObject,
        subpath: PathBuf,
        builder: Option<&str>,
        result_dir: Option<PathBuf>,
    ) -> PyResult<()> {
        let tree = WorkingTree::from(tree);
        silver_platter::debian::build(&tree, subpath.as_path(), builder, result_dir.as_deref())
    }

    #[pyfunction]
    pub(crate) fn install_built_package(
        local_tree: PyObject,
        subpath: std::path::PathBuf,
        build_target_dir: std::path::PathBuf,
    ) -> PyResult<()> {
        let local_tree = WorkingTree::from(local_tree);
        silver_platter::debian::install_built_package(
            &local_tree,
            subpath.as_path(),
            build_target_dir.as_path(),
        )
        .unwrap();
        Ok(())
    }
}

/// Check whether two branches are conflicted when merged.
///
/// Args:
///   main_branch: Main branch to merge into
///   other_branch: Branch to merge (and use for scratch access, needs write
///                 access)
///   other_revision: Other revision to check
/// Returns:
///   boolean indicating whether the merge would result in conflicts
#[pyfunction]
#[pyo3(signature = (main_branch, other_branch, other_revision=None))]
fn merge_conflicts(
    main_branch: PyObject,
    other_branch: PyObject,
    other_revision: Option<RevisionId>,
) -> PyResult<bool> {
    Ok(silver_platter::utils::merge_conflicts(
        &breezyshim::branch::GenericBranch::new(main_branch),
        &breezyshim::branch::GenericBranch::new(other_branch),
        other_revision.as_ref(),
    )?)
}

fn workspace_error_to_py_err(e: silver_platter::workspace::Error) -> PyErr {
    import_exception!(breezy.errors, UnknownFormat);
    import_exception!(breezy.errors, PermissionDenied);
    match e {
        silver_platter::workspace::Error::BrzError(e) => e.into(),
        silver_platter::workspace::Error::IOError(e) => e.into(),
        silver_platter::workspace::Error::Other(e) => PyRuntimeError::new_err((e,)),
        silver_platter::workspace::Error::PermissionDenied(e) => PermissionDenied::new_err((e,)),
        silver_platter::workspace::Error::UnknownFormat(format) => {
            UnknownFormat::new_err((format,))
        }
    }
}

#[pyclass(subclass)]
struct Workspace(silver_platter::workspace::Workspace);

#[pymethods]
impl Workspace {
    /// Create a workspace from a URL.
    ///
    /// # Arguments
    /// * `url` - The URL to create the workspace from
    #[classmethod]
    fn from_url(_cls: &Bound<PyType>, url: &str) -> PyResult<Self> {
        Ok(Self(
            silver_platter::workspace::Workspace::from_url(
                &url.parse()
                    .map_err(|e| PyValueError::new_err(format!("Invalid URL: {}", e)))?,
            )
            .map_err(workspace_error_to_py_err)?,
        ))
    }

    #[getter]
    fn path(&self) -> std::path::PathBuf {
        self.0.path()
    }

    #[getter]
    fn base_revid(&self) -> Option<RevisionId> {
        self.0.base_revid()
    }

    #[new]
    #[pyo3(signature = (main_branch=None, resume_branch=None, cached_branch=None, dir=None, path=None, additional_colocated_branches=None, resume_branch_additional_colocated_branches=None, format=None))]
    fn new(
        py: Python,
        main_branch: Option<PyObject>,
        resume_branch: Option<PyObject>,
        cached_branch: Option<PyObject>,
        dir: Option<PathBuf>,
        path: Option<PathBuf>,
        additional_colocated_branches: Option<PyObject>,
        resume_branch_additional_colocated_branches: Option<PyObject>,
        format: Option<PyObject>,
    ) -> PyResult<Self> {
        let mut builder = silver_platter::workspace::Workspace::builder();

        if let Some(main_branch) = main_branch {
            builder = builder.main_branch(Box::new(breezyshim::branch::GenericBranch::new(
                main_branch,
            )));
        }

        if let Some(resume_branch) = resume_branch {
            builder = builder.resume_branch(Box::new(breezyshim::branch::GenericBranch::new(
                resume_branch,
            )));
        }

        if let Some(cached_branch) = cached_branch {
            builder = builder.cached_branch(Box::new(breezyshim::branch::GenericBranch::new(
                cached_branch,
            )));
        }

        if let Some(additional_colocated_branches) = additional_colocated_branches {
            if let Ok(additional_colocated_branches) =
                additional_colocated_branches.extract::<HashMap<String, String>>(py)
            {
                builder = builder.additional_colocated_branches(additional_colocated_branches);
            } else if let Ok(additional_colocated_branches) =
                additional_colocated_branches.extract::<Vec<String>>(py)
            {
                builder = builder.additional_colocated_branches(
                    additional_colocated_branches
                        .into_iter()
                        .map(|x| (x.clone(), x))
                        .collect(),
                );
            } else {
                return Err(PyTypeError::new_err(
                    "additional_colocated_branches must be a dict or a list of tuples",
                ));
            }
        }

        if let Some(resume_branch_additional_colocated_branches) =
            resume_branch_additional_colocated_branches
        {
            if let Ok(resume_branch_additional_colocated_branches) =
                resume_branch_additional_colocated_branches.extract::<HashMap<String, String>>(py)
            {
                builder = builder.resume_branch_additional_colocated_branches(
                    resume_branch_additional_colocated_branches,
                );
            } else if let Ok(resume_branch_additional_colocated_branches) =
                resume_branch_additional_colocated_branches.extract::<Vec<String>>(py)
            {
                builder = builder.resume_branch_additional_colocated_branches(
                    resume_branch_additional_colocated_branches
                        .into_iter()
                        .map(|x| (x.clone(), x))
                        .collect(),
                );
            } else {
                return Err(PyTypeError::new_err(
                    "resume_branch_additional_colocated_branches must be a dict or a list of tuples",
                ));
            }
        }

        if let Some(path) = path {
            builder = builder.path(path);
        }

        if let Some(dir) = dir {
            builder = builder.dir(dir);
        }

        if let Some(format) = format {
            if let Ok(format) = format.extract::<String>(py) {
                builder = builder.format(format.as_str());
            } else if format.bind(py).hasattr("get_format_description")? {
                builder = builder.format(&breezyshim::controldir::ControlDirFormat::from(format));
            } else {
                return Err(PyTypeError::new_err("format must be a string"));
            }
        }

        Ok(Self(builder.build().map_err(workspace_error_to_py_err)?))
    }

    #[getter]
    fn base_tree(&self, py: Python) -> PyResult<PyObject> {
        Ok(self.0.base_tree()?.to_object(py))
    }

    #[getter]
    fn local_tree(&self, py: Python) -> PyObject {
        self.0.local_tree().to_object(py)
    }

    #[getter]
    fn main_branch(&self, py: Python) -> PyObject {
        self.0.main_branch().to_object(py)
    }

    #[getter]
    fn resume_branch(&self, py: Python) -> Option<PyObject> {
        self.0.resume_branch().map(|b| b.to_object(py))
    }

    fn any_branch_changes(&self) -> bool {
        self.0.any_branch_changes()
    }

    fn changes_since_main(&self) -> bool {
        self.0.changes_since_main()
    }

    fn changes_since_base(&self) -> bool {
        self.0.changes_since_base()
    }

    #[getter]
    fn main_branch_revid(&self) -> RevisionId {
        self.0.main_branch().unwrap().last_revision()
    }

    #[getter]
    fn refreshed(&self) -> bool {
        self.0.refreshed()
    }

    fn result_branches(&self) -> Vec<(String, Option<RevisionId>, Option<RevisionId>)> {
        self.0.changed_branches()
    }

    fn __enter__(slf: Bound<Self>) -> Bound<Self> {
        slf.clone()
    }

    #[pyo3(signature = (_exc_type, _exc_value, _traceback))]
    fn __exit__(
        slf: Bound<Self>,
        _exc_type: Option<PyObject>,
        _exc_value: Option<PyObject>,
        _traceback: Option<PyObject>,
    ) -> PyResult<bool> {
        slf.borrow_mut()
            .0
            .destroy()
            .map_err(workspace_error_to_py_err)?;
        Ok(false)
    }

    #[pyo3(signature = (outf, old_label=None, new_label=None))]
    fn show_diff(
        &self,
        outf: PyObject,
        old_label: Option<&str>,
        new_label: Option<&str>,
    ) -> PyResult<()> {
        let outf = Box::new(pyo3_filelike::PyBinaryFile::from(outf));

        self.0.show_diff(outf, old_label, new_label)?;

        Ok(())
    }
}

#[pyfunction]
#[pyo3(signature = (vcs_type=None))]
fn select_preferred_probers(py: Python, vcs_type: Option<&str>) -> Vec<PyObject> {
    let probers = silver_platter::probers::select_preferred_probers(vcs_type);
    probers.into_iter().map(|p| p.to_object(py)).collect()
}

#[pyfunction]
#[pyo3(signature = (vcs_type=None))]
fn select_probers(py: Python, vcs_type: Option<&str>) -> Vec<PyObject> {
    let probers = silver_platter::probers::select_probers(vcs_type);
    probers.into_iter().map(|p| p.to_object(py)).collect()
}

#[pymodule(name = "silver_platter")]
fn _svp_rs(py: Python, m: &Bound<PyModule>) -> PyResult<()> {
    pyo3_log::init();
    m.add_function(wrap_pyfunction!(derived_branch_name, m)?)?;
    m.add_function(wrap_pyfunction!(script_runner, m)?)?;
    m.add_function(wrap_pyfunction!(select_preferred_probers, m)?)?;
    m.add_function(wrap_pyfunction!(select_probers, m)?)?;
    m.add(
        "ScriptMadeNoChanges",
        py.get_type_bound::<ScriptMadeNoChanges>(),
    )?;
    m.add("ScriptFailed", py.get_type_bound::<ScriptFailed>())?;
    m.add("ScriptNotFound", py.get_type_bound::<ScriptNotFound>())?;
    m.add("DetailedFailure", py.get_type_bound::<DetailedFailure>())?;
    m.add("MissingChangelog", py.get_type_bound::<MissingChangelog>())?;
    m.add(
        "ResultFileFormatError",
        py.get_type_bound::<ResultFileFormatError>(),
    )?;

    m.add_class::<CommandResult>()?;
    m.add_class::<Recipe>()?;
    m.add_function(wrap_pyfunction!(push_derived_changes, m)?)?;
    m.add_class::<Forge>()?;
    m.add_class::<Workspace>()?;
    m.add_class::<CandidateList>()?;
    m.add_class::<Candidate>()?;
    m.add_function(wrap_pyfunction!(push_result, m)?)?;
    m.add_function(wrap_pyfunction!(push_changes, m)?)?;
    m.add_function(wrap_pyfunction!(full_branch_url, m)?)?;
    m.add_function(wrap_pyfunction!(merge_conflicts, m)?)?;
    #[cfg(feature = "debian")]
    {
        m.add_class::<debian::ChangelogBehaviour>()?;
        m.add_function(wrap_pyfunction!(debian::get_maintainer_from_env, m)?)?;
        m.add_function(wrap_pyfunction!(debian::guess_update_changelog, m)?)?;
        m.add_class::<debian::DebianCommandResult>()?;
        m.add_function(wrap_pyfunction!(debian::debian_script_runner, m)?)?;
        m.add_function(wrap_pyfunction!(debian::is_debcargo_package, m)?)?;
        m.add_function(wrap_pyfunction!(debian::control_files_in_root, m)?)?;
        m.add_function(wrap_pyfunction!(debian::install_built_package, m)?)?;
        m.add_function(wrap_pyfunction!(debian::build, m)?)?;
        m.add_function(wrap_pyfunction!(
            debian::pick_additional_colocated_branches,
            m
        )?)?;
    }
    m.add_function(wrap_pyfunction!(find_existing_proposed, m)?)?;
    m.add_function(wrap_pyfunction!(propose_changes, m)?)?;
    m.add_function(wrap_pyfunction!(publish_changes, m)?)?;
    m.add_class::<PublishResult>()?;
    m.add(
        "InsufficientChangesForNewProposal",
        py.get_type_bound::<InsufficientChangesForNewProposal>(),
    )?;
    m.add(
        "UnrelatedBranchExists",
        py.get_type_bound::<UnrelatedBranchExists>(),
    )?;
    m.add_function(wrap_pyfunction!(run_pre_check, m)?)?;
    m.add_function(wrap_pyfunction!(run_post_check, m)?)?;
    m.add_function(wrap_pyfunction!(check_proposal_diff, m)?)?;
    m.add("PostCheckFailed", py.get_type_bound::<PostCheckFailed>())?;
    m.add("PreCheckFailed", py.get_type_bound::<PreCheckFailed>())?;
    m.add(
        "EmptyMergeProposal",
        py.get_type_bound::<EmptyMergeProposal>(),
    )?;

    m.add("MODE_PUSH", "push")?;
    m.add("MODE_ATTEMPT_PUSH", "attempt-push")?;
    m.add("MODE_PROPOSE", "propose")?;
    m.add("MODE_PUSH_DERIVED", "push-derived")?;
    m.add(
        "SUPPORTED_MODES",
        vec!["push", "attempt-push", "propose", "push-derived"],
    )?;
    let items = silver_platter::VERSION.split('.').collect::<Vec<_>>();
    let tuple = items
        .iter()
        .map(|i| i.parse::<i32>().unwrap())
        .collect::<Vec<_>>();
    m.add("__version__", pyo3::types::PyTuple::new_bound(py, tuple))?;

    Ok(())
}
