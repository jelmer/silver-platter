//! # Silver-Platter
//!
//! Silver-Platter makes it possible to contribute automatable changes to source
//! code in a version control system
//! ([codemods](https://github.com/jelmer/awesome-codemods)).
//!
//! It automatically creates a local checkout of a remote repository,
//! makes user-specified changes, publishes those changes on the remote hosting
//! site and then creates a pull request.
//!
//! In addition to that, it can also perform basic maintenance on branches
//! that have been proposed for merging - such as restarting them if they
//! have conflicts due to upstream changes.

#![deny(missing_docs)]
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
pub use breezyshim::branch::{Branch, GenericBranch};
pub use breezyshim::controldir::{ControlDir, Prober};
pub use breezyshim::forge::{Forge, MergeProposal};
pub use breezyshim::transport::Transport;
pub use breezyshim::tree::WorkingTree;
pub use breezyshim::RevisionId;
use serde::{Deserialize, Deserializer, Serialize, Serializer};
use std::path::Path;

#[derive(Debug, Serialize, Deserialize, Clone, Copy, PartialEq, Eq, Default)]
/// Publish mode
pub enum Mode {
    #[serde(rename = "push")]
    /// Push to the target branch
    Push,

    #[serde(rename = "propose")]
    /// Propose a merge
    Propose,

    #[serde(rename = "attempt-push")]
    #[default]
    /// Attempt to push to the target branch, falling back to propose if necessary
    AttemptPush,

    #[serde(rename = "push-derived")]
    /// Push to a branch derived from the script name
    PushDerived,

    #[serde(rename = "bts")]
    /// Bug tracking system
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

#[cfg(feature = "pyo3")]
impl pyo3::FromPyObject<'_> for Mode {
    fn extract_bound(ob: &pyo3::Bound<pyo3::PyAny>) -> pyo3::PyResult<Self> {
        use pyo3::prelude::*;
        let s: std::borrow::Cow<str> = ob.extract()?;
        match s.as_ref() {
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

#[cfg(feature = "pyo3")]
impl pyo3::ToPyObject for Mode {
    fn to_object(&self, py: pyo3::Python) -> pyo3::PyObject {
        self.to_string().to_object(py)
    }
}

/// Returns the branch name derived from a script name
pub fn derived_branch_name(script: &str) -> &str {
    let first_word = script.split(' ').next().unwrap_or("");
    let script_name = Path::new(first_word).file_stem().unwrap_or_default();
    script_name.to_str().unwrap_or("")
}

/// Policy on whether to commit pending changes
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum CommitPending {
    /// Automatically determine pending changes
    #[default]
    Auto,

    /// Commit pending changes
    Yes,

    /// Don't commit pending changes
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

/// The result of a codemod
pub trait CodemodResult {
    /// Context
    fn context(&self) -> serde_json::Value;

    /// Returns the value of the result
    fn value(&self) -> Option<u32>;

    /// Returns the URL of the target branch
    fn target_branch_url(&self) -> Option<url::Url>;

    /// Returns the description of the result
    fn description(&self) -> Option<String>;

    /// Returns the tags of the result
    fn tags(&self) -> Vec<(String, Option<RevisionId>)>;

    /// Returns the context as a Tera context
    fn tera_context(&self) -> tera::Context {
        tera::Context::from_value(self.context()).unwrap()
    }
}

/// The version of the library
pub const VERSION: &str = env!("CARGO_PKG_VERSION");
