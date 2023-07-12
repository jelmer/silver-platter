mod breezyshim;
pub mod candidates;
pub mod codemod;
pub mod probers;
pub mod publish;
pub mod recipe;
pub mod vcs;
pub use breezyshim::{Branch, Forge, RevisionId, Transport, WorkingTree};
use serde::{Deserialize, Serialize};
use std::path::Path;

#[derive(Debug, Serialize, Deserialize, Clone, Copy)]
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

pub fn derived_branch_name(script: &str) -> &str {
    let first_word = script.split(' ').next().unwrap_or("");
    let script_name = Path::new(first_word).file_stem().unwrap_or_default();
    script_name.to_str().unwrap_or("")
}
