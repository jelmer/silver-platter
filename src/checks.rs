use breezyshim::tree::WorkingTree;
use breezyshim::RevisionId;
use std::collections::HashMap;
use std::error::Error;
use std::fmt;
use std::process::Command;

#[derive(Debug)]
pub struct PreCheckFailed;

impl fmt::Display for PreCheckFailed {
    fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result {
        write!(f, "Pre-check failed")
    }
}

impl Error for PreCheckFailed {}

pub fn run_pre_check(tree: WorkingTree, script: &str) -> Result<(), PreCheckFailed> {
    let path = tree.abspath(std::path::Path::new("")).unwrap();
    let status = Command::new("sh")
        .arg("-c")
        .arg(script)
        .current_dir(path)
        .status();

    match status {
        Ok(status) => {
            if status.code().unwrap() != 0 {
                Err(PreCheckFailed)
            } else {
                Ok(())
            }
        }
        Err(_) => Err(PreCheckFailed),
    }
}

#[derive(Debug)]
pub struct PostCheckFailed;

impl fmt::Display for PostCheckFailed {
    fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result {
        write!(f, "Post-check failed")
    }
}

impl Error for PostCheckFailed {}

pub fn run_post_check(
    tree: WorkingTree,
    script: &str,
    since_revid: &RevisionId,
) -> Result<(), PostCheckFailed> {
    let mut env_vars = HashMap::new();
    env_vars.insert("SINCE_REVID", since_revid.to_string());
    let path = tree.abspath(std::path::Path::new("")).unwrap();

    let status = Command::new("sh")
        .arg("-c")
        .arg(script)
        .current_dir(path)
        .envs(&env_vars)
        .status();

    match status {
        Ok(status) => {
            if status.code().unwrap() != 0 {
                Err(PostCheckFailed)
            } else {
                Ok(())
            }
        }
        Err(_) => Err(PostCheckFailed),
    }
}
