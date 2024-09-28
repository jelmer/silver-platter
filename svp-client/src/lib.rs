//! # svp-client
//!
//! `svp-client` is a library to interact with the [SVP
//! protocol](https://github.com/jelmer/silver-platter/blob/master/codemod-protocol.md), as supported by
//! the `svp` command-line tool.
use std::collections::HashMap;

pub mod debian;

#[derive(Debug, serde::Serialize)]
struct Failure {
    /// The result code, e.g. "header-missing".
    result_code: String,

    /// The versions of the packages involved.
    versions: HashMap<String, String>,

    /// A human-readable description of the failure.
    description: String,

    /// Whether the failure is transient.
    transient: Option<bool>,
}

#[derive(Debug, serde::Serialize)]
struct Success {
    versions: HashMap<String, String>,
    value: Option<i32>,
    context: Option<serde_json::Value>,
    debian: Option<debian::Context>,
}

pub fn enabled() -> bool {
    std::env::var("SVP_API").ok().as_deref() == Some("1")
}

/// Report a success to the SVP server.
///
/// # Arguments
/// * `versions` - A map of package names to versions.
pub fn report_success(
    versions: HashMap<String, String>,
    value: Option<i32>,
    context: Option<serde_json::Value>,
) {
    if enabled() {
        let f = std::fs::File::create(std::env::var("SVP_RESULT").unwrap()).unwrap();

        serde_json::to_writer(
            f,
            &Success {
                versions,
                value,
                context,
                debian: None,
            },
        )
        .unwrap();
    }
}

pub fn report_nothing_to_do(versions: HashMap<String, String>, description: Option<&str>) -> ! {
    let description = description.unwrap_or("Nothing to do");
    if std::env::var("SVP_API").ok().as_deref() == Some("1") {
        let f = std::fs::File::create(std::env::var("SVP_RESULT").unwrap()).unwrap();

        serde_json::to_writer(
            f,
            &Failure {
                result_code: "nothing-to-do".to_string(),
                versions,
                description: description.to_string(),
                transient: None,
            },
        )
        .unwrap();
    }
    log::error!("{}", description);
    std::process::exit(0);
}

pub fn report_fatal(
    versions: HashMap<String, String>,
    code: &str,
    description: &str,
    hint: Option<&str>,
    transient: Option<bool>,
) -> ! {
    if enabled() {
        let f = std::fs::File::create(std::env::var("SVP_RESULT").unwrap()).unwrap();

        serde_json::to_writer(
            f,
            &Failure {
                result_code: code.to_string(),
                versions,
                description: description.to_string(),
                transient,
            },
        )
        .unwrap();
    }
    log::error!("{}", description);
    if let Some(hint) = hint {
        log::info!("{}", hint);
    }
    std::process::exit(1);
}

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
