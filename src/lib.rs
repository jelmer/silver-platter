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
// Allow unknown cfgs for now, since import_exception_bound
// expects a gil-refs feature that is not defined
#![allow(unexpected_cfgs)]
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

impl std::fmt::Display for Mode {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let s = match self {
            Mode::Push => "push",
            Mode::Propose => "propose",
            Mode::AttemptPush => "attempt-push",
            Mode::PushDerived => "push-derived",
            Mode::Bts => "bts",
        };
        write!(f, "{}", s)
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

impl CommitPending {
    /// Returns whether the policy is to commit pending changes
    pub fn is_default(&self) -> bool {
        *self == CommitPending::Auto
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

#[cfg(test)]
mod tests {
    use super::*;
    use std::str::FromStr;

    #[test]
    fn test_derived_branch_name() {
        assert_eq!(derived_branch_name("script.py"), "script");
        assert_eq!(derived_branch_name("path/to/script.py"), "script");
        assert_eq!(derived_branch_name("/absolute/path/to/script.py"), "script");
        assert_eq!(derived_branch_name("script.py arg1 arg2"), "script");
        assert_eq!(derived_branch_name(""), "");
        assert_eq!(derived_branch_name("script"), "script");
        assert_eq!(derived_branch_name("no-extension."), "no-extension");
    }

    #[test]
    fn test_commit_pending_from_str() {
        assert_eq!(
            CommitPending::from_str("auto").unwrap(),
            CommitPending::Auto
        );
        assert_eq!(CommitPending::from_str("yes").unwrap(), CommitPending::Yes);
        assert_eq!(CommitPending::from_str("no").unwrap(), CommitPending::No);

        let err = CommitPending::from_str("invalid").unwrap_err();
        assert_eq!(err, "Unknown commit-pending value: invalid");
    }

    #[test]
    fn test_commit_pending_serialization() {
        // Test serialization
        let auto_json = serde_json::to_string(&CommitPending::Auto).unwrap();
        let yes_json = serde_json::to_string(&CommitPending::Yes).unwrap();
        let no_json = serde_json::to_string(&CommitPending::No).unwrap();

        assert_eq!(auto_json, "null");
        assert_eq!(yes_json, "true");
        assert_eq!(no_json, "false");

        // Test deserialization
        let auto: CommitPending = serde_json::from_str("null").unwrap();
        let yes: CommitPending = serde_json::from_str("true").unwrap();
        let no: CommitPending = serde_json::from_str("false").unwrap();

        assert_eq!(auto, CommitPending::Auto);
        assert_eq!(yes, CommitPending::Yes);
        assert_eq!(no, CommitPending::No);
    }

    #[test]
    fn test_commit_pending_is_default() {
        assert!(CommitPending::Auto.is_default());
        assert!(!CommitPending::Yes.is_default());
        assert!(!CommitPending::No.is_default());
    }

    #[test]
    fn test_mode_from_str() {
        assert_eq!(Mode::from_str("push").unwrap(), Mode::Push);
        assert_eq!(Mode::from_str("propose").unwrap(), Mode::Propose);
        assert_eq!(Mode::from_str("attempt").unwrap(), Mode::AttemptPush);
        assert_eq!(Mode::from_str("attempt-push").unwrap(), Mode::AttemptPush);
        assert_eq!(Mode::from_str("push-derived").unwrap(), Mode::PushDerived);
        assert_eq!(Mode::from_str("bts").unwrap(), Mode::Bts);

        let err = Mode::from_str("invalid").unwrap_err();
        assert_eq!(err, "Unknown mode: invalid");
    }

    #[test]
    fn test_mode_to_string() {
        assert_eq!(Mode::Push.to_string(), "push");
        assert_eq!(Mode::Propose.to_string(), "propose");
        assert_eq!(Mode::AttemptPush.to_string(), "attempt-push");
        assert_eq!(Mode::PushDerived.to_string(), "push-derived");
        assert_eq!(Mode::Bts.to_string(), "bts");
    }
}
