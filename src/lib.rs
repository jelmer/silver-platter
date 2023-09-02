pub mod candidates;
pub mod codemod;
pub mod probers;
pub mod publish;
pub mod recipe;
pub mod utils;
pub mod vcs;
pub use breezyshim::branch::Branch;
pub use breezyshim::controldir::{ControlDir, Prober};
pub use breezyshim::forge::{Forge, MergeProposal};
pub use breezyshim::transport::Transport;
pub use breezyshim::tree::WorkingTree;
pub use breezyshim::RevisionId;
use serde::{Deserialize, Serialize};
use std::path::Path;

#[derive(Debug, Serialize, Deserialize, Clone, Copy, PartialEq, Eq)]
pub enum Mode {
    #[serde(rename = "push")]
    Push,

    #[serde(rename = "propose")]
    Propose,

    #[serde(rename = "attempt-push")]
    AttemptPush,

    #[serde(rename = "push-derived")]
    PushDerived,

    #[serde(rename = "bts")]
    Bts,
}

impl ToString for Mode {
    fn to_string(&self) -> String {
        match self {
            Mode::Push => "push".to_string(),
            Mode::Propose => "propose".to_string(),
            Mode::AttemptPush => "attempt-push".to_string(),
            Mode::PushDerived => "push-derived".to_string(),
            Mode::Bts => "bts".to_string(),
        }
    }
}

impl pyo3::FromPyObject<'_> for Mode {
    fn extract(ob: &pyo3::PyAny) -> pyo3::PyResult<Self> {
        let s: &str = ob.extract()?;
        match s {
            "push" => Ok(Mode::Push),
            "propose" => Ok(Mode::Propose),
            "attempt-push" => Ok(Mode::AttemptPush),
            "push-derived" => Ok(Mode::PushDerived),
            "bts" => Ok(Mode::Bts),
            _ => Err(pyo3::exceptions::PyValueError::new_err((format!(
                "Unknown mode: {}",
                s
            ),))),
        }
    }
}

impl pyo3::ToPyObject for Mode {
    fn to_object(&self, py: pyo3::Python) -> pyo3::PyObject {
        self.to_string().to_object(py)
    }
}

pub fn derived_branch_name(script: &str) -> &str {
    let first_word = script.split(' ').next().unwrap_or("");
    let script_name = Path::new(first_word).file_stem().unwrap_or_default();
    script_name.to_str().unwrap_or("")
}
