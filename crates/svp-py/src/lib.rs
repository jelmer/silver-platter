use pyo3::create_exception;
use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
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
        self.0.target_branch_url.as_deref()
    }

    #[getter]
    fn old_revision(&self) -> RevisionId {
        self.0.old_revision.clone()
    }

    #[getter]
    fn new_revision(&self) -> RevisionId {
        self.0.new_revision.clone()
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
    Ok(())
}
