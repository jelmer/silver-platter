//! Codemod
use breezyshim::error::Error as BrzError;
use breezyshim::RevisionId;
use breezyshim::WorkingTree;
use std::collections::HashMap;
use url::Url;

#[derive(Debug, Clone, serde::Deserialize, serde::Serialize)]
/// Command result
pub struct CommandResult {
    /// Value
    pub value: Option<u32>,

    /// Context
    pub context: Option<serde_json::Value>,

    /// Description
    pub description: Option<String>,

    /// Serialized context
    pub serialized_context: Option<String>,

    /// Commit message
    pub commit_message: Option<String>,

    /// Title
    pub title: Option<String>,

    /// Tags
    pub tags: Vec<(String, Option<RevisionId>)>,

    /// Target branch URL
    pub target_branch_url: Option<Url>,

    /// Old revision
    pub old_revision: RevisionId,

    /// New revision
    pub new_revision: RevisionId,
}

impl Default for CommandResult {
    fn default() -> Self {
        Self {
            value: None,
            context: None,
            description: None,
            serialized_context: None,
            commit_message: None,
            title: None,
            tags: Vec::new(),
            target_branch_url: None,
            old_revision: RevisionId::null(),
            new_revision: RevisionId::null(),
        }
    }
}

impl crate::CodemodResult for CommandResult {
    fn context(&self) -> serde_json::Value {
        self.context.clone().unwrap_or_default()
    }

    fn value(&self) -> Option<u32> {
        self.value
    }

    fn target_branch_url(&self) -> Option<Url> {
        self.target_branch_url.clone()
    }

    fn description(&self) -> Option<String> {
        self.description.clone()
    }

    fn tags(&self) -> Vec<(String, Option<RevisionId>)> {
        self.tags.clone()
    }
}

impl From<CommandResult> for DetailedSuccess {
    fn from(r: CommandResult) -> Self {
        DetailedSuccess {
            value: r.value,
            context: r.context,
            description: r.description,
            commit_message: r.commit_message,
            title: r.title,
            serialized_context: r.serialized_context,
            tags: Some(
                r.tags
                    .into_iter()
                    .map(|(k, v)| (k, v.map(|v| v.to_string())))
                    .collect(),
            ),
            target_branch_url: r.target_branch_url,
        }
    }
}

impl From<&CommandResult> for DetailedSuccess {
    fn from(r: &CommandResult) -> Self {
        DetailedSuccess {
            value: r.value,
            context: r.context.clone(),
            description: r.description.clone(),
            commit_message: r.commit_message.clone(),
            title: r.title.clone(),
            serialized_context: r.serialized_context.clone(),
            tags: Some(
                r.tags
                    .iter()
                    .map(|(k, v)| (k.clone(), v.as_ref().map(|v| v.to_string())))
                    .collect(),
            ),
            target_branch_url: r.target_branch_url.clone(),
        }
    }
}

#[derive(Debug, serde::Deserialize, serde::Serialize, Default)]
struct DetailedSuccess {
    value: Option<u32>,
    context: Option<serde_json::Value>,
    description: Option<String>,
    serialized_context: Option<String>,
    #[serde(rename = "commit-message")]
    commit_message: Option<String>,
    title: Option<String>,
    tags: Option<Vec<(String, Option<String>)>>,
    #[serde(rename = "target-branch-url")]
    target_branch_url: Option<Url>,
}

#[derive(Debug)]
/// Error while running codemod
pub enum Error {
    /// Script made no changes
    ScriptMadeNoChanges,

    /// Script was not found
    ScriptNotFound,

    /// The script failed with a specific exit code
    ExitCode(i32),

    /// Detailed failure
    Detailed(DetailedFailure),

    /// I/O error
    Io(std::io::Error),

    /// JSON error
    Json(serde_json::Error),

    /// UTF-8 error
    Utf8(std::string::FromUtf8Error),

    /// Other error
    Other(String),
}

impl From<std::io::Error> for Error {
    fn from(e: std::io::Error) -> Self {
        Error::Io(e)
    }
}

impl From<serde_json::Error> for Error {
    fn from(e: serde_json::Error) -> Self {
        Error::Json(e)
    }
}

impl From<std::string::FromUtf8Error> for Error {
    fn from(e: std::string::FromUtf8Error) -> Self {
        Error::Utf8(e)
    }
}

impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
        match self {
            Error::ScriptMadeNoChanges => write!(f, "Script made no changes"),
            Error::ScriptNotFound => write!(f, "Script not found"),
            Error::ExitCode(code) => write!(f, "Script exited with code {}", code),
            Error::Detailed(d) => write!(f, "Script failed: {:?}", d),
            Error::Io(e) => write!(f, "Command failed: {}", e),
            Error::Json(e) => write!(f, "JSON error: {}", e),
            Error::Utf8(e) => write!(f, "UTF-8 error: {}", e),
            Error::Other(s) => write!(f, "{}", s),
        }
    }
}

impl std::error::Error for Error {}

#[derive(Debug, serde::Deserialize, serde::Serialize, Clone, PartialEq, Eq)]
/// Detailed failure information
pub struct DetailedFailure {
    /// Result code
    pub result_code: String,

    /// Description of the failure
    pub description: Option<String>,

    /// Stage at which the failure occurred
    pub stage: Option<Vec<String>>,

    /// Additional details
    pub details: Option<serde_json::Value>,
}

impl std::fmt::Display for DetailedFailure {
    fn fmt(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
        write!(f, "Script failed: {}", self.result_code)?;
        if let Some(description) = &self.description {
            write!(f, ": {}", description)?;
        }
        if let Some(stage) = &self.stage {
            write!(f, " (stage: {})", stage.join(" "))?;
        }
        if let Some(details) = &self.details {
            write!(f, ": {:?}", details)?;
        }
        Ok(())
    }
}

/// Run a script in a tree and commit the result.
///
/// This ignores newly added files.
///
/// # Arguments
///
/// - `local_tree`: Local tree to run script in
/// - `subpath`: Subpath to run script in
/// - `script`: Script to run
/// - `commit_pending`: Whether to commit pending changes
pub fn script_runner(
    local_tree: &dyn WorkingTree,
    script: &[&str],
    subpath: &std::path::Path,
    commit_pending: crate::CommitPending,
    resume_metadata: Option<&serde_json::Value>,
    committer: Option<&str>,
    extra_env: Option<HashMap<String, String>>,
    stderr: std::process::Stdio,
) -> Result<CommandResult, Error> {
    let mut env = std::env::vars().collect::<HashMap<_, _>>();

    if let Some(extra_env) = extra_env {
        for (k, v) in extra_env {
            env.insert(k, v);
        }
    }

    env.insert("SVP_API".to_string(), "1".to_string());

    let last_revision = local_tree.last_revision().unwrap();

    let mut orig_tags = local_tree.get_tag_dict().unwrap();

    let td = tempfile::tempdir()?;

    let result_path = td.path().join("result.json");
    env.insert(
        "SVP_RESULT".to_string(),
        result_path.to_string_lossy().to_string(),
    );
    if let Some(resume_metadata) = resume_metadata {
        let resume_path = td.path().join("resume.json");
        env.insert(
            "SVP_RESUME".to_string(),
            resume_path.to_string_lossy().to_string(),
        );
        let w = std::fs::File::create(&resume_path)?;
        serde_json::to_writer(w, &resume_metadata)?;
    }

    let mut command = std::process::Command::new(script[0]);
    command.args(&script[1..]);
    command.envs(env);
    command.stdin(std::process::Stdio::null());
    command.stdout(std::process::Stdio::piped());
    command.stderr(stderr);
    command.current_dir(local_tree.abspath(subpath).unwrap());

    let ret = match command.output() {
        Ok(ret) => ret,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            return Err(Error::ScriptNotFound);
        }
        Err(e) => {
            return Err(Error::Io(e));
        }
    };

    if !ret.status.success() {
        return Err(match std::fs::read_to_string(&result_path) {
            Ok(result) => {
                let result: DetailedFailure = serde_json::from_str(&result)?;
                Error::Detailed(result)
            }
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                Error::ExitCode(ret.status.code().unwrap_or(1))
            }
            Err(_) => Error::ExitCode(ret.status.code().unwrap_or(1)),
        });
    }

    // Open result_path, read metadata
    let mut result: DetailedSuccess = match std::fs::read_to_string(&result_path) {
        Ok(result) => serde_json::from_str(&result)?,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => DetailedSuccess::default(),
        Err(e) => return Err(e.into()),
    };

    if result.description.is_none() {
        result.description = Some(String::from_utf8(ret.stdout)?);
    }

    let mut new_revision = local_tree.last_revision().unwrap();
    let tags: Vec<(String, Option<RevisionId>)> = if let Some(tags) = result.tags {
        tags.into_iter()
            .map(|(n, v)| (n, v.map(|v| RevisionId::from(v.as_bytes().to_vec()))))
            .collect()
    } else {
        let mut tags = local_tree
            .get_tag_dict()
            .unwrap()
            .into_iter()
            .filter_map(|(n, v)| {
                if orig_tags.remove(n.as_str()).as_ref() != Some(&v) {
                    Some((n, Some(v)))
                } else {
                    None
                }
            })
            .collect::<Vec<_>>();
        tags.extend(orig_tags.into_keys().map(|n| (n, None)));
        tags
    };

    let commit_pending = match commit_pending {
        crate::CommitPending::Auto => {
            // Automatically commit pending changes if the script did not
            // touch the branch
            last_revision == new_revision
        }
        crate::CommitPending::Yes => true,
        crate::CommitPending::No => false,
    };

    if commit_pending {
        local_tree
            .smart_add(&[local_tree.abspath(subpath).unwrap().as_path()])
            .unwrap();
        let mut builder = local_tree
            .build_commit()
            .message(result.description.as_ref().unwrap())
            .allow_pointless(false);
        if let Some(committer) = committer {
            builder = builder.committer(committer);
        }
        match builder.commit() {
            Ok(rev) => {
                new_revision = rev;
            }
            Err(BrzError::PointlessCommit) => {
                // No changes - keep new_revision as last_revision
            }
            Err(e) => return Err(Error::Other(format!("Failed to commit changes: {}", e))),
        };
    }

    if new_revision == last_revision {
        return Err(Error::ScriptMadeNoChanges);
    }

    let old_revision = last_revision;
    let new_revision = local_tree.last_revision().unwrap();

    Ok(CommandResult {
        old_revision,
        new_revision,
        tags,
        description: result.description,
        value: result.value,
        context: result.context,
        commit_message: result.commit_message,
        title: result.title,
        serialized_context: result.serialized_context,
        target_branch_url: result.target_branch_url,
    })
}

#[cfg(test)]
mod command_result_tests {
    use super::*;
    use crate::CodemodResult;

    #[test]
    fn test_command_result_context_with_value() {
        let result = CommandResult {
            context: Some(serde_json::json!({"key": "value"})),
            ..Default::default()
        };

        assert_eq!(result.context(), serde_json::json!({"key": "value"}));
    }

    #[test]
    fn test_command_result_context_none() {
        let result = CommandResult {
            context: None,
            ..Default::default()
        };

        // Should return default (null) when context is None
        assert_eq!(result.context(), serde_json::Value::Null);
    }

    #[test]
    fn test_command_result_value() {
        let result = CommandResult {
            value: Some(42),
            ..Default::default()
        };

        assert_eq!(result.value(), Some(42));
    }

    #[test]
    fn test_command_result_description() {
        let result = CommandResult {
            description: Some("Test description".to_string()),
            ..Default::default()
        };

        assert_eq!(result.description(), Some("Test description".to_string()));
    }

    #[test]
    fn test_command_result_description_none() {
        let result = CommandResult {
            description: None,
            ..Default::default()
        };

        assert_eq!(result.description(), None);
    }

    #[test]
    fn test_command_result_target_branch_url() {
        let url = url::Url::parse("https://github.com/test/repo").unwrap();
        let result = CommandResult {
            target_branch_url: Some(url.clone()),
            ..Default::default()
        };

        assert_eq!(result.target_branch_url(), Some(url));
    }

    #[test]
    fn test_command_result_tags() {
        let tags = vec![
            ("v1.0".to_string(), Some(RevisionId::from(b"rev1".to_vec()))),
            ("v2.0".to_string(), None),
        ];
        let result = CommandResult {
            tags: tags.clone(),
            ..Default::default()
        };

        assert_eq!(result.tags(), tags);
    }

    #[test]
    fn test_command_result_default() {
        let result = CommandResult::default();

        assert_eq!(result.context(), serde_json::Value::Null);
        assert_eq!(result.value(), None);
        assert_eq!(result.target_branch_url(), None);
        assert_eq!(result.description(), None);
        assert!(result.tags().is_empty());
    }
}

#[cfg(test)]
mod script_runner_tests {
    use breezyshim::controldir::create_standalone_workingtree;
    use breezyshim::testing::TestEnv;
    use breezyshim::tree::MutableTree;
    use breezyshim::WorkingTree;
    use serial_test::serial;

    fn make_executable(script_path: &std::path::Path) {
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            // Make script.sh executable
            let mut perm = std::fs::metadata(script_path).unwrap().permissions();
            perm.set_mode(0o755);
            std::fs::set_permissions(script_path, perm).unwrap();
        }
    }

    #[test]
    #[serial]
    fn test_no_api() {
        let _test_env = TestEnv::new();
        let td = tempfile::tempdir().unwrap();
        let d = td.path().join("t");
        let tree = create_standalone_workingtree(&d, "bzr").unwrap();
        let script_path = td.path().join("script.sh");
        std::fs::write(
            &script_path,
            r#"#!/bin/sh
echo foo > bar
echo Did a thing
"#,
        )
        .unwrap();

        make_executable(&script_path);

        std::fs::write(d.join("bar"), "bar").unwrap();

        tree.add(&[std::path::Path::new("bar")]).unwrap();
        let old_revid = tree.build_commit().message("initial").commit().unwrap();
        let script_path_str = script_path.to_str().unwrap();
        let result = super::script_runner(
            &tree,
            &[script_path_str],
            std::path::Path::new(""),
            crate::CommitPending::Auto,
            None,
            Some("Joe Example <joe@example.com>"),
            None,
            std::process::Stdio::null(),
        )
        .unwrap();

        assert!(!tree.has_changes().unwrap());
        assert_eq!(result.old_revision, old_revid);
        assert_eq!(result.new_revision, tree.last_revision().unwrap());
        assert_eq!(result.description.as_deref().unwrap(), "Did a thing\n");

        std::mem::drop(td);
    }

    #[test]
    #[serial]
    fn test_api() {
        let _test_env = TestEnv::new();
        let td = tempfile::tempdir().unwrap();
        let d = td.path().join("t");
        let tree = create_standalone_workingtree(&d, "bzr").unwrap();
        let script_path = td.path().join("script.sh");
        std::fs::write(
            &script_path,
            r#"#!/bin/sh
echo foo > bar
echo '{"description": "Did a thing", "code": "success"}' > $SVP_RESULT
"#,
        )
        .unwrap();

        make_executable(&script_path);

        std::fs::write(d.join("bar"), "bar").unwrap();

        tree.add(&[std::path::Path::new("bar")]).unwrap();
        let old_revid = tree.build_commit().message("initial").commit().unwrap();
        let script_path_str = script_path.to_str().unwrap();
        let result = super::script_runner(
            &tree,
            &[script_path_str],
            std::path::Path::new(""),
            crate::CommitPending::Auto,
            None,
            Some("Joe Example <joe@example.com>"),
            None,
            std::process::Stdio::null(),
        )
        .unwrap();

        assert!(!tree.has_changes().unwrap());
        assert_eq!(result.old_revision, old_revid);
        assert_eq!(result.new_revision, tree.last_revision().unwrap());
        assert_eq!(result.description.as_deref().unwrap(), "Did a thing");

        std::mem::drop(td);
    }

    #[test]
    #[serial]
    fn test_new_file() {
        let _test_env = TestEnv::new();
        let td = tempfile::tempdir().unwrap();
        let d = td.path().join("t");
        let tree = create_standalone_workingtree(&d, "bzr").unwrap();
        let script_path = d.join("script.sh");
        std::fs::write(
            &script_path,
            r#"#!/bin/sh
echo foo > bar
echo Did a thing
"#,
        )
        .unwrap();

        make_executable(&script_path);

        std::fs::write(d.join("bar"), "initial").unwrap();

        tree.add(&[std::path::Path::new("bar")]).unwrap();
        let old_revid = tree.build_commit().message("initial").commit().unwrap();

        let script_path_str = script_path.to_str().unwrap();
        let result = super::script_runner(
            &tree,
            &[script_path_str],
            std::path::Path::new(""),
            crate::CommitPending::Auto,
            None,
            Some("Joe Example <joe@example.com>"),
            None,
            std::process::Stdio::null(),
        )
        .unwrap();

        assert!(!tree.has_changes().unwrap());
        assert_eq!(result.old_revision, old_revid);
        assert_eq!(result.new_revision, tree.last_revision().unwrap());
        assert_eq!(result.description.as_deref().unwrap(), "Did a thing\n");

        std::mem::drop(td);
    }

    #[test]
    #[serial]
    fn test_no_changes() {
        let _test_env = TestEnv::new();
        let td = tempfile::tempdir().unwrap();
        let d = td.path().join("t");
        let tree =
            create_standalone_workingtree(&d, &breezyshim::controldir::ControlDirFormat::default())
                .unwrap();
        let script_path = td.path().join("script.sh");
        std::fs::write(
            &script_path,
            r#"#!/bin/sh
echo Did a thing
"#,
        )
        .unwrap();

        make_executable(&script_path);

        tree.build_commit()
            .message("initial")
            .allow_pointless(true)
            .commit()
            .unwrap();
        let script_path_str = script_path.to_str().unwrap();
        let err = super::script_runner(
            &tree,
            &[script_path_str],
            std::path::Path::new(""),
            crate::CommitPending::Yes,
            None,
            Some("Joe Example <joe@example.com>"),
            None,
            std::process::Stdio::null(),
        )
        .unwrap_err();

        assert!(!tree.has_changes().unwrap());
        assert!(matches!(err, super::Error::ScriptMadeNoChanges));

        std::mem::drop(td);
    }
}
