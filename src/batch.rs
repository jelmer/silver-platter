//! Batch management.

use crate::candidates::Candidate;
use crate::codemod::script_runner;
use crate::proposal::DescriptionFormat;
use crate::recipe::Recipe;
use crate::vcs::open_branch;
use crate::workspace::Workspace;
use crate::Mode;
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use url::Url;

#[derive(Debug, serde::Serialize, serde::Deserialize)]
pub struct Entry {
    subpath: Option<PathBuf>,
    target_branch_url: Option<Url>,
    description: String,
    #[serde(rename = "commit-message")]
    commit_message: Option<String>,
    mode: Mode,
    title: Option<String>,
    labels: Option<Vec<String>>,
    context: serde_yaml::Value,
    #[serde(rename = "proposal-url")]
    proposal_url: Option<Url>,
}

#[derive(Debug, serde::Deserialize, serde::Serialize)]
pub struct Batch {
    recipe: Recipe,
    name: String,
    work: HashMap<String, Entry>,
}

#[derive(Debug)]
pub enum Error {
    Script(crate::codemod::Error),
    Vcs(crate::vcs::BranchOpenError),
    Io(std::io::Error),
    Yaml(serde_yaml::Error),
    Tera(tera::Error),
    Workspace(crate::workspace::Error),
}

impl From<crate::workspace::Error> for Error {
    fn from(e: crate::workspace::Error) -> Self {
        Error::Workspace(e)
    }
}

impl From<crate::codemod::Error> for Error {
    fn from(e: crate::codemod::Error) -> Self {
        Error::Script(e)
    }
}

impl From<crate::vcs::BranchOpenError> for Error {
    fn from(e: crate::vcs::BranchOpenError) -> Self {
        Error::Vcs(e)
    }
}

impl From<std::io::Error> for Error {
    fn from(e: std::io::Error) -> Self {
        Error::Io(e)
    }
}

impl From<tera::Error> for Error {
    fn from(e: tera::Error) -> Self {
        Error::Tera(e)
    }
}

impl From<serde_yaml::Error> for Error {
    fn from(e: serde_yaml::Error) -> Self {
        Error::Yaml(e)
    }
}

impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
        match self {
            Error::Vcs(e) => write!(f, "VCS error: {}", e),
            Error::Script(e) => write!(f, "Script error: {}", e),
            Error::Io(e) => write!(f, "I/O error: {}", e),
            Error::Yaml(e) => write!(f, "YAML error: {}", e),
            Error::Tera(e) => write!(f, "Tera error: {}", e),
            Error::Workspace(e) => write!(f, "Workspace error: {}", e),
        }
    }
}

impl Entry {
    pub fn from_recipe(
        recipe: &Recipe,
        basepath: &Path,
        url: &Url,
        subpath: &Path,
        default_mode: Option<Mode>,
    ) -> Result<Self, Error> {
        let main_branch = match open_branch(url, None, None, None) {
            Ok(branch) => branch,
            Err(e) => return Err(Error::Vcs(e)),
        };

        let ws = Workspace::new(
            Some(main_branch.as_ref()),
            None,
            None,
            HashMap::new(),
            HashMap::new(),
            None,
            Some(basepath),
            None,
        );

        ws.start()?;

        log::info!("Making changes to {}", main_branch.get_user_url());

        let result = match script_runner(
            &ws.local_tree(),
            recipe
                .command
                .as_ref()
                .unwrap()
                .iter()
                .map(|s| s.as_str())
                .collect::<Vec<_>>()
                .as_slice(),
            subpath,
            recipe.commit_pending,
            None,
            None,
            None,
            std::process::Stdio::inherit(),
        ) {
            Ok(result) => result,
            Err(e) => return Err(Error::Script(e)),
        };

        let tera_context: tera::Context = tera::Context::from_value(
            result
                .context
                .as_ref()
                .unwrap_or(&serde_json::Value::Null)
                .clone(),
        )
        .unwrap();

        let target_branch_url = result.target_branch_url;
        let description = if let Some(description) = result.description {
            description
        } else if let Some(ref mr) = recipe.merge_request {
            mr.render_description(DescriptionFormat::Markdown, &tera_context)?
                .unwrap()
        } else {
            panic!("No description provided");
        };
        let commit_message = if let Some(commit_message) = result.commit_message {
            Some(commit_message)
        } else if let Some(ref mr) = recipe.merge_request {
            mr.render_commit_message(&tera_context)?
        } else {
            None
        };
        let title = if let Some(title) = result.title {
            Some(title)
        } else if let Some(ref mr) = recipe.merge_request {
            mr.render_title(&tera_context)?
        } else {
            None
        };
        let mode = recipe.mode.or(default_mode).unwrap_or_default();
        let labels = recipe.labels.clone();
        let context = result.context;

        ws.defer_destroy();

        Ok(Entry {
            subpath: Some(subpath.to_owned()),
            target_branch_url,
            description,
            commit_message,
            mode,
            title,
            labels,
            proposal_url: None,
            context: serde_yaml::from_str(
                context
                    .unwrap_or(serde_json::Value::Null)
                    .to_string()
                    .as_str(),
            )
            .unwrap(),
        })
    }

    pub fn status(&self) -> Status {
        if let Some(proposal_url) = self.proposal_url.as_ref() {
            let proposal = breezyshim::forge::get_proposal_by_url(proposal_url).unwrap();
            if proposal.is_merged().unwrap() {
                Status::Merged(proposal_url.clone())
            } else if proposal.is_closed().unwrap() {
                Status::Closed(proposal_url.clone())
            } else {
                Status::Open(proposal_url.clone())
            }
        } else {
            Status::NotPublished()
        }
    }
}

pub enum Status {
    Merged(Url),
    Closed(Url),
    Open(Url),
    NotPublished(),
}

impl std::fmt::Display for Status {
    fn fmt(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
        match self {
            Status::Merged(url) => write!(f, "Merged: {}", url),
            Status::Closed(url) => write!(f, "{} was closed without being merged", url),
            Status::Open(url) => write!(f, "{} is still open", url),
            Status::NotPublished() => write!(f, "Not published yet"),
        }
    }
}

impl Batch {
    pub fn from_recipe(
        recipe: &Recipe,
        candidates: impl Iterator<Item = Candidate>,
        directory: &Path,
    ) -> Result<Batch, Error> {
        std::fs::create_dir(directory)?;

        let mut batch = match load_batch_metadata(directory) {
            Some(batch) => batch,
            None => Batch {
                recipe: recipe.clone(),
                name: recipe.name.clone().unwrap(),
                work: HashMap::new(),
            },
        };

        for candidate in candidates {
            // TODO(jelmer): Move this logic to candidate
            let basename: String = candidate.name.unwrap_or_else(|| {
                candidate
                    .url
                    .to_string()
                    .trim_end_matches('/')
                    .rsplit('/')
                    .last()
                    .unwrap()
                    .to_string()
            });

            let mut name = basename.clone();

            // TODO(jelmer): Search by URL rather than by name?
            if let Some(entry) = batch.work.get(name.as_str()) {
                if entry.target_branch_url.as_ref() == Some(&candidate.url) {
                    log::info!(
                        "Skipping {} ({}) (already in batch)",
                        name,
                        candidate.url.to_string()
                    );
                    continue;
                }
            }

            let mut work_path = directory.join(&name);
            let mut i = 0;
            while std::fs::metadata(&work_path).is_ok() {
                i += 1;
                name = format!("{}.{}", basename, i);
                work_path = directory.join(&name);
            }

            match Entry::from_recipe(
                recipe,
                work_path.as_ref(),
                &candidate.url,
                candidate
                    .subpath
                    .as_deref()
                    .unwrap_or_else(|| Path::new("")),
                candidate.default_mode,
            ) {
                Ok(entry) => {
                    batch.work.insert(name, entry);
                    save_batch_metadata(directory, &batch)?;
                }
                Err(e) => {
                    log::error!("Failed to generate batch entry for {}: {}", name, e);
                    // Recursively remove work_path
                    std::fs::remove_dir_all(work_path)?;
                    continue;
                }
            }
        }
        save_batch_metadata(directory, &batch)?;
        Ok(batch)
    }

    pub fn status(&self) -> HashMap<&str, Status> {
        let mut status = HashMap::new();
        for (name, entry) in self.work.iter() {
            status.insert(name.as_str(), entry.status());
        }
        status
    }
}

pub fn drop_batch_entry(directory: &Path, name: &str) -> Result<(), Error> {
    let mut batch = load_batch_metadata(directory).unwrap();
    batch.work.remove(name);
    std::fs::remove_dir_all(directory.join(name))?;
    save_batch_metadata(directory, &batch)?;
    Ok(())
}

pub fn save_batch_metadata(directory: &Path, batch: &Batch) -> Result<(), Error> {
    let mut file = std::fs::File::create(directory.join("batch.yaml"))?;
    serde_yaml::to_writer(&mut file, &batch)?;
    Ok(())
}

pub fn load_batch_metadata(directory: &Path) -> Option<Batch> {
    let file = std::fs::File::open(directory.join("batch.yaml")).ok()?;
    serde_yaml::from_reader(file).ok()
}
