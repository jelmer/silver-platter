//! # svp-client
//!
//! `svp-client` is a library to interact with the [SVP
//! protocol](https://github.com/jelmer/silver-platter/blob/master/codemod-protocol.md), as supported by
//! the `svp` command-line tool.

use std::collections::HashMap;

#[derive(Debug, serde::Serialize, serde::Deserialize, Clone, PartialEq, Eq)]
/// Behaviour for updating the changelog.
pub struct ChangelogBehaviour {
    #[serde(rename = "update")]
    /// Whether the changelog should be updated.
    pub update_changelog: bool,

    /// Explanation for the decision.
    pub explanation: String,
}

#[derive(Debug, serde::Serialize)]
struct Failure {
    pub result_code: String,
    pub versions: HashMap<String, String>,
    pub description: String,
    pub transient: Option<bool>,
}

impl std::fmt::Display for Failure {
    fn fmt(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
        write!(f, "{}: {}", self.result_code, self.description)
    }
}

impl std::error::Error for Failure {}

impl std::fmt::Display for Success {
    fn fmt(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
        write!(f, "Success")
    }
}

#[derive(Debug, serde::Serialize)]
struct DebianContext {
    pub changelog: Option<ChangelogBehaviour>,
}

#[derive(Debug, serde::Serialize)]
struct Success {
    pub versions: HashMap<String, String>,
    pub value: Option<i32>,
    pub context: Option<serde_json::Value>,
    pub debian: Option<DebianContext>,
    #[serde(rename = "target-branch-url")]
    pub target_branch_url: Option<url::Url>,
    #[serde(rename = "commit-message")]
    pub commit_message: Option<String>,
}

/// Write a success to the SVP API
fn write_svp_success(data: &Success) -> std::io::Result<()> {
    if enabled() {
        let f = std::fs::File::create(std::env::var("SVP_RESULT").unwrap()).unwrap();

        Ok(serde_json::to_writer(f, data)?)
    } else {
        Ok(())
    }
}

/// Write a failure to the SVP API
fn write_svp_failure(data: &Failure) -> std::io::Result<()> {
    if enabled() {
        let f = std::fs::File::create(std::env::var("SVP_RESULT").unwrap()).unwrap();

        Ok(serde_json::to_writer(f, data)?)
    } else {
        Ok(())
    }
}

/// Report success
pub fn report_success<T>(versions: HashMap<String, String>, value: Option<i32>, context: Option<T>)
where
    T: serde::Serialize,
{
    write_svp_success(&Success {
        versions,
        value,
        context: context.map(|x| serde_json::to_value(x).unwrap()),
        debian: None,
        target_branch_url: None,
        commit_message: None,
    })
    .unwrap();
}

/// Report success with Debian-specific context
pub fn report_success_debian<T>(
    versions: HashMap<String, String>,
    value: Option<i32>,
    context: Option<T>,
    changelog: Option<ChangelogBehaviour>,
) where
    T: serde::Serialize,
{
    write_svp_success(&Success {
        versions,
        value,
        context: context.map(|x| serde_json::to_value(x).unwrap()),
        debian: Some(DebianContext { changelog }),
        target_branch_url: None,
        commit_message: None,
    })
    .unwrap();
}

/// Report that there is nothing to do
pub fn report_nothing_to_do(
    versions: HashMap<String, String>,
    description: Option<&str>,
    hint: Option<&str>,
) -> ! {
    let description = description.unwrap_or("Nothing to do");
    write_svp_failure(&Failure {
        result_code: "nothing-to-do".to_string(),
        versions,
        description: description.to_string(),
        transient: None,
    })
    .unwrap();
    log::error!("{}", description);
    if let Some(hint) = hint {
        log::info!("{}", hint);
    }

    std::process::exit(0);
}

/// Report a fatal error
pub fn report_fatal(
    versions: HashMap<String, String>,
    code: &str,
    description: &str,
    hint: Option<&str>,
    transient: Option<bool>,
) -> ! {
    write_svp_failure(&Failure {
        result_code: code.to_string(),
        versions,
        description: description.to_string(),
        transient,
    })
    .unwrap();
    log::error!("{}", description);
    if let Some(hint) = hint {
        log::info!("{}", hint);
    }
    std::process::exit(1);
}

/// Load the resume file if it exists
pub fn load_resume<T: serde::de::DeserializeOwned>() -> Option<T> {
    if enabled() {
        if let Ok(resume_path) = std::env::var("SVP_RESUME") {
            let f = std::fs::File::open(resume_path).unwrap();
            let resume: T = serde_json::from_reader(f).unwrap();
            Some(resume)
        } else {
            None
        }
    } else {
        None
    }
}

/// Check if the SVP API is enabled
pub fn enabled() -> bool {
    std::env::var("SVP_API").ok().as_deref() == Some("1")
}

/// A reporter for the SVP API
pub struct Reporter {
    versions: HashMap<String, String>,
    target_branch_url: Option<url::Url>,
    commit_message: Option<String>,
}

impl Reporter {
    /// Create a new reporter
    pub fn new(versions: HashMap<String, String>) -> Self {
        Self {
            versions,
            target_branch_url: None,
            commit_message: None,
        }
    }

    /// Check if the SVP API is enabled
    pub fn enabled(&self) -> bool {
        enabled()
    }

    /// Load the resume file if it exists
    pub fn load_resume<T: serde::de::DeserializeOwned>(&self) -> Option<T> {
        load_resume()
    }

    /// Set the target branch URL
    pub fn set_target_branch_url(&mut self, url: url::Url) {
        self.target_branch_url = Some(url);
    }

    /// Set the commit message
    pub fn set_commit_message(&mut self, message: String) {
        self.commit_message = Some(message);
    }

    /// Report success
    pub fn report_success<T>(self, value: Option<i32>, context: Option<T>)
    where
        T: serde::Serialize,
    {
        write_svp_success(&Success {
            versions: self.versions,
            value,
            context: context.map(|x| serde_json::to_value(x).unwrap()),
            debian: None,
            target_branch_url: self.target_branch_url,
            commit_message: self.commit_message,
        })
        .unwrap();
    }

    /// Report success with Debian-specific context
    pub fn report_success_debian<T>(
        self,
        value: Option<i32>,
        context: Option<T>,
        changelog: Option<ChangelogBehaviour>,
    ) where
        T: serde::Serialize,
    {
        write_svp_success(&Success {
            versions: self.versions,
            value,
            context: context.map(|x| serde_json::to_value(x).unwrap()),
            debian: Some(DebianContext { changelog }),
            target_branch_url: self.target_branch_url,
            commit_message: self.commit_message,
        })
        .unwrap();
    }

    /// Report that there is nothing to do
    pub fn report_nothing_to_do(self, description: Option<&str>, hint: Option<&str>) -> ! {
        report_nothing_to_do(self.versions, description, hint);
    }

    /// Report a fatal error
    pub fn report_fatal(
        self,
        code: &str,
        description: &str,
        hint: Option<&str>,
        transient: Option<bool>,
    ) -> ! {
        report_fatal(self.versions, code, description, hint, transient);
    }
}
