use breezyshim::tree::{CommitError, WorkingTree};
use breezyshim::RevisionId;
use std::collections::HashMap;
use url::Url;

#[derive(Debug, Clone)]
pub struct CommandResult {
    pub value: Option<u32>,
    pub context: Option<serde_json::Value>,
    pub description: String,
    pub serialized_context: Option<String>,
    pub tags: Vec<(String, Option<RevisionId>)>,
    pub target_branch_url: Option<Url>,
    pub old_revision: RevisionId,
    pub new_revision: RevisionId,
}

impl From<&CommandResult> for DetailedSuccess {
    fn from(r: &CommandResult) -> Self {
        DetailedSuccess {
            value: r.value,
            context: r.context.clone(),
            description: Some(r.description.clone()),
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
    tags: Option<Vec<(String, Option<String>)>>,
    #[serde(rename = "target-branch-url")]
    target_branch_url: Option<Url>,
}

#[derive(Debug)]
pub enum Error {
    ScriptMadeNoChanges,
    ScriptNotFound,
    ExitCode(i32),
    Detailed(DetailedFailure),
    Io(std::io::Error),
    Json(serde_json::Error),
    Utf8(std::string::FromUtf8Error),
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

#[derive(Debug, serde::Deserialize, serde::Serialize, Clone)]
pub struct DetailedFailure {
    pub result_code: String,
    pub description: Option<String>,
    pub stage: Option<Vec<String>>,
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
    local_tree: &WorkingTree,
    script: &[&str],
    subpath: &std::path::Path,
    commit_pending: Option<bool>,
    resume_metadata: Option<&CommandResult>,
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
        let resume_metadata: DetailedSuccess = resume_metadata.into();
        serde_json::to_writer(w, &resume_metadata)?;
    }

    let mut command = std::process::Command::new(script[0]);
    command.args(&script[1..]);
    command.envs(env);
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

    let commit_pending = commit_pending.unwrap_or_else(|| {
        // Automatically commit pending changes if the script did not
        // touch the branch
        last_revision == new_revision
    });

    if commit_pending {
        local_tree
            .smart_add(&[local_tree.abspath(subpath).unwrap().as_path()])
            .unwrap();
        new_revision = match local_tree.commit(
            result.description.as_ref().unwrap(),
            Some(false),
            committer,
            None,
        ) {
            Ok(rev) => rev,
            Err(CommitError::PointlessCommit) => {
                // No changes
                last_revision.clone()
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
        description: result.description.unwrap(),
        value: result.value,
        context: result.context,
        serialized_context: result.serialized_context,
        target_branch_url: result.target_branch_url,
    })
}
