pub mod batch;
pub mod candidates;
pub mod checks;
pub mod codemod;
#[cfg(feature = "debian")]
pub mod debian;
pub mod probers;
pub mod proposal;
pub mod publish;
pub mod recipe;
pub mod run;
pub mod utils;
pub mod vcs;
pub mod workspace;
pub use breezyshim::branch::{Branch, RegularBranch};
pub use breezyshim::controldir::{ControlDir, Prober};
pub use breezyshim::forge::{Forge, MergeProposal};
pub use breezyshim::transport::Transport;
pub use breezyshim::tree::WorkingTree;
pub use breezyshim::RevisionId;
use serde::{Deserialize, Deserializer, Serialize, Serializer};
use std::path::Path;

#[derive(Debug, Serialize, Deserialize, Clone, Copy, PartialEq, Eq, Default)]
pub enum Mode {
    #[serde(rename = "push")]
    Push,

    #[serde(rename = "propose")]
    Propose,

    #[serde(rename = "attempt-push")]
    #[default]
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

impl std::str::FromStr for Mode {
    type Err = String;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s {
            "push" => Ok(Mode::Push),
            "propose" => Ok(Mode::Propose),
            "attempt" | "attempt-push" => Ok(Mode::AttemptPush),
            "push-derived" => Ok(Mode::PushDerived),
            "bts" => Ok(Mode::Bts),
            _ => Err(format!("Unknown mode: {}", s)),
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

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CommitPending {
    Auto,
    Yes,
    No,
}

impl std::str::FromStr for CommitPending {
    type Err = String;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s {
            "auto" => Ok(CommitPending::Auto),
            "yes" => Ok(CommitPending::Yes),
            "no" => Ok(CommitPending::No),
            _ => Err(format!("Unknown commit-pending value: {}", s)),
        }
    }
}

impl Serialize for CommitPending {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: Serializer,
    {
        match *self {
            CommitPending::Auto => serializer.serialize_none(),
            CommitPending::Yes => serializer.serialize_bool(true),
            CommitPending::No => serializer.serialize_bool(false),
        }
    }
}

impl<'de> Deserialize<'de> for CommitPending {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        let opt: Option<bool> = Option::deserialize(deserializer)?;
        Ok(match opt {
            None => CommitPending::Auto,
            Some(true) => CommitPending::Yes,
            Some(false) => CommitPending::No,
        })
    }
}

pub trait CodemodResult {
    fn context(&self) -> serde_json::Value;

    fn tera_context(&self) -> tera::Context {
        tera::Context::from_value(self.context()).unwrap()
    }
}
