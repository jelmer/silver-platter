use pyo3::create_exception;
use pyo3::exceptions::{PyRuntimeError, PyTypeError};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyType};
use silver_platter::codemod::Error as CodemodError;
use silver_platter::{RevisionId, WorkingTree};

create_exception!(
    silver_platter.apply,
    ScriptMadeNoChanges,
    pyo3::exceptions::PyException
);
create_exception!(
    silver_platter.apply,
    ScriptFailed,
    pyo3::exceptions::PyException
);
create_exception!(
    silver_platter.apply,
    ScriptNotFound,
    pyo3::exceptions::PyException
);
create_exception!(
    silver_platter.apply,
    DetailedFailure,
    pyo3::exceptions::PyException
);
create_exception!(
    silver_platter.apply,
    ResultFileFormatError,
    pyo3::exceptions::PyException
);

#[pyclass]
struct Recipe(silver_platter::recipe::Recipe);

fn json_to_py(py: Python, value: &serde_json::Value) -> PyObject {
    match value {
        serde_json::Value::Null => py.None(),
        serde_json::Value::Bool(b) => pyo3::types::PyBool::new(py, *b).into(),
        serde_json::Value::Number(n) => {
            if let Some(n) = n.as_u64() {
                n.into_py(py)
            } else if let Some(n) = n.as_i64() {
                n.into_py(py)
            } else if let Some(n) = n.as_f64() {
                n.into_py(py)
            } else {
                unreachable!()
            }
        }
        serde_json::Value::String(s) => pyo3::types::PyString::new(py, s.as_str()).into(),
        serde_json::Value::Array(a) => {
            let list = pyo3::types::PyList::empty(py);
            for v in a {
                list.append(json_to_py(py, v)).unwrap();
            }
            list.into_py(py)
        }
        serde_json::Value::Object(o) => {
            let dict = pyo3::types::PyDict::new(py);
            for (k, v) in o {
                dict.set_item(k, json_to_py(py, v)).unwrap();
            }
            dict.into_py(py)
        }
    }
}

#[pymethods]
impl Recipe {
    #[classmethod]
    fn from_path(_type: &PyType, path: std::path::PathBuf) -> PyResult<Self> {
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
        self.0.commit_pending
    }

    #[getter]
    fn command(&self) -> Option<Vec<&str>> {
        self.0
            .command
            .as_ref()
            .map(|v| v.iter().map(|s| s.as_str()).collect())
    }

    #[getter]
    fn mode(&self) -> Option<String> {
        self.0.mode.as_ref().map(|m| m.to_string())
    }

    fn render_merge_request_title(&self, context: &PyAny) -> PyResult<Option<String>> {
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

    fn render_merge_request_commit_message(&self, context: &PyAny) -> PyResult<Option<String>> {
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
        context: &PyAny,
    ) -> PyResult<Option<String>> {
        let merge_request = if let Some(mp) = self.0.merge_request.as_ref() {
            mp
        } else {
            return Ok(None);
        };
        let context = py_dict_to_tera_context(context)?;
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

fn py_dict_to_tera_context(py_dict: &PyAny) -> PyResult<tera::Context> {
    let mut context = tera::Context::new();
    if py_dict.is_none() {
        return Ok(context);
    }
    let py_dict = py_dict.extract::<&PyDict>()?;
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
    fn context(&self, py: Python) -> Option<PyObject> {
        self.0.context.as_ref().map(|c| json_to_py(py, c))
    }
}

#[pyfunction]
fn script_runner(
    py: Python,
    local_tree: PyObject,
    script: PyObject,
    subpath: Option<&str>,
    commit_pending: Option<bool>,
    resume_metadata: Option<&CommandResult>,
    committer: Option<&str>,
    extra_env: Option<std::collections::HashMap<String, String>>,
) -> PyResult<PyObject> {
    let script = if let Ok(script) = script.extract::<Vec<&str>>(py) {
        script
    } else {
        vec!["sh", "-c", script.extract::<&str>(py)?]
    };

    silver_platter::codemod::script_runner(
        &WorkingTree::new(local_tree).unwrap(),
        script.as_slice(),
        subpath.unwrap_or(""),
        commit_pending,
        resume_metadata.as_ref().map(|obj| &obj.0),
        committer,
        extra_env,
        std::process::Stdio::inherit(),
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
struct Branch(silver_platter::Branch);

#[pyclass]
struct Forge(silver_platter::Forge);

#[pyfunction]
fn push_derived_changes(
    local_branch: PyObject,
    main_branch: PyObject,
    forge: PyObject,
    name: &str,
    overwrite_existing: Option<bool>,
    owner: Option<&str>,
    tags: Option<std::collections::HashMap<String, RevisionId>>,
    stop_revision: Option<RevisionId>,
) -> PyResult<(Branch, String)> {
    let (b, u) = silver_platter::publish::push_derived_changes(
        &silver_platter::Branch::new(local_branch),
        &silver_platter::Branch::new(main_branch),
        &silver_platter::Forge::new(forge),
        name,
        overwrite_existing,
        owner,
        tags,
        stop_revision.as_ref(),
    )?;
    Ok((Branch(b), u.to_string()))
}

#[pyclass]
struct CandidateList(silver_platter::candidates::Candidates);

#[pymethods]
impl CandidateList {
    #[classmethod]
    fn from_path(_type: &PyType, path: std::path::PathBuf) -> PyResult<Self> {
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
    fn subpath(&self) -> Option<&str> {
        self.0.subpath.as_deref()
    }

    #[getter]
    fn default_mode(&self) -> Option<String> {
        self.0.default_mode.as_ref().map(|m| m.to_string())
    }
}

#[pyfunction]
fn push_changes(
    local_branch: PyObject,
    main_branch: PyObject,
    forge: Option<PyObject>,
    possible_transports: Option<Vec<PyObject>>,
    additional_colocated_branches: Option<Vec<(String, String)>>,
    dry_run: Option<bool>,
    tags: Option<std::collections::HashMap<String, RevisionId>>,
    stop_revision: Option<RevisionId>,
) -> PyResult<()> {
    let dry_run = dry_run.unwrap_or(false);
    let possible_transports: Option<Vec<silver_platter::Transport>> =
        possible_transports.map(|t| t.into_iter().map(silver_platter::Transport::new).collect());
    silver_platter::publish::push_changes(
        &silver_platter::Branch::new(local_branch),
        &silver_platter::Branch::new(main_branch),
        forge.map(silver_platter::Forge::new).as_ref(),
        possible_transports,
        additional_colocated_branches,
        dry_run,
        tags,
        stop_revision.as_ref(),
    )?;
    Ok(())
}

#[pyfunction]
fn push_result(
    local_branch: PyObject,
    remote_branch: PyObject,
    additional_colocated_branches: Option<Vec<(String, String)>>,
    tags: Option<std::collections::HashMap<String, RevisionId>>,
    stop_revision: Option<RevisionId>,
) -> PyResult<()> {
    silver_platter::publish::push_result(
        &silver_platter::Branch::new(local_branch),
        &silver_platter::Branch::new(remote_branch),
        additional_colocated_branches,
        tags,
        stop_revision.as_ref(),
    )?;
    Ok(())
}

#[pyfunction]
fn full_branch_url(branch: PyObject) -> PyResult<String> {
    Ok(silver_platter::vcs::full_branch_url(&silver_platter::Branch::new(branch)).to_string())
}

#[pymodule]
fn _svp_rs(py: Python, m: &PyModule) -> PyResult<()> {
    pyo3_log::init();
    m.add_function(wrap_pyfunction!(derived_branch_name, m)?)?;
    m.add_function(wrap_pyfunction!(script_runner, m)?)?;
    m.add("ScriptMadeNoChanges", py.get_type::<ScriptMadeNoChanges>())?;
    m.add("ScriptFailed", py.get_type::<ScriptFailed>())?;
    m.add("ScriptNotFound", py.get_type::<ScriptNotFound>())?;
    m.add("DetailedFailure", py.get_type::<DetailedFailure>())?;
    m.add(
        "ResultFileFormatError",
        py.get_type::<ResultFileFormatError>(),
    )?;
    m.add_class::<CommandResult>()?;
    m.add_class::<Recipe>()?;
    m.add_function(wrap_pyfunction!(push_derived_changes, m)?)?;
    m.add_class::<Branch>()?;
    m.add_class::<Forge>()?;
    m.add_class::<CandidateList>()?;
    m.add_class::<Candidate>()?;
    m.add_function(wrap_pyfunction!(push_result, m)?)?;
    m.add_function(wrap_pyfunction!(push_changes, m)?)?;
    m.add_function(wrap_pyfunction!(full_branch_url, m)?)?;
    Ok(())
}
