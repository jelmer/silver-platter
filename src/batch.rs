//! Batch management.

use crate::candidates::Candidate;
use crate::codemod::script_runner;
use crate::proposal::DescriptionFormat;
use crate::publish::{Error as PublishError, PublishResult};
use crate::recipe::Recipe;
use crate::vcs::{open_branch, BranchOpenError};
use crate::workspace::Workspace;
use crate::Mode;
use breezyshim::branch::Branch;
use breezyshim::forge::{Error as ForgeError};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use url::Url;

#[derive(Debug, serde::Serialize, serde::Deserialize)]
pub struct Entry {
    #[serde(skip)]
    local_path: PathBuf,
    pub subpath: Option<PathBuf>,
    pub target_branch_url: Option<Url>,
    pub description: String,
    #[serde(rename = "commit-message")]
    pub commit_message: Option<String>,
    pub mode: Mode,
    pub title: Option<String>,
    pub owner: Option<String>,
    pub labels: Option<Vec<String>>,
    pub context: serde_yaml::Value,
    #[serde(rename = "proposal-url")]
    pub proposal_url: Option<Url>,
}

#[derive(Debug, serde::Deserialize, serde::Serialize)]
pub struct Batch {
    pub recipe: Recipe,
    pub name: String,
    pub work: HashMap<String, Entry>,
    #[serde(skip)]
    pub basepath: PathBuf,
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

        let owner = None;

        ws.defer_destroy();

        Ok(Entry {
            local_path: basepath.to_path_buf(),
            subpath: Some(subpath.to_owned()),
            target_branch_url,
            description,
            commit_message,
            mode,
            owner,
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

    pub fn working_tree(
        &self,
    ) -> Result<breezyshim::tree::WorkingTree, breezyshim::tree::WorkingTreeOpenError> {
        breezyshim::tree::WorkingTree::open(&self.local_path)
    }

    pub fn target_branch(&self) -> Result<Box<dyn Branch>, BranchOpenError> {
        open_branch(self.target_branch_url.as_ref().unwrap(), None, None, None)
    }

    pub fn local_branch(&self) -> Result<Box<dyn Branch>, BranchOpenError> {
        open_branch(
            &url::Url::from_directory_path(&self.local_path).unwrap(),
            None,
            None,
            None,
        )
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
    pub fn from_recipe<'a>(
        recipe: &Recipe,
        candidates: impl Iterator<Item = &'a Candidate>,
        directory: &Path,
    ) -> Result<Batch, Error> {
        std::fs::create_dir(directory)?;

        let mut batch = match load_batch_metadata(directory) {
            Some(batch) => batch,
            None => Batch {
                recipe: recipe.clone(),
                name: recipe.name.clone().unwrap(),
                work: HashMap::new(),
                basepath: directory.to_path_buf(),
            },
        };

        for candidate in candidates {
            // TODO(jelmer): Move this logic to candidate
            let basename: String = candidate.name.as_ref().map_or_else(
                || {
                    candidate
                        .url
                        .to_string()
                        .trim_end_matches('/')
                        .rsplit('/')
                        .last()
                        .unwrap()
                        .to_string()
                },
                |name| name.clone(),
            );

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

    pub fn get(&self, name: &str) -> Option<&Entry> {
        self.work.get(name)
    }

    pub fn get_mut(&mut self, name: &str) -> Option<&mut Entry> {
        self.work.get_mut(name)
    }

    pub fn status(&self) -> HashMap<&str, Status> {
        let mut status = HashMap::new();
        for (name, entry) in self.work.iter() {
            status.insert(name.as_str(), entry.status());
        }
        status
    }

    pub fn remove(&mut self, name: &str) -> Result<(), Error> {
        self.work.remove(name);
        std::fs::remove_dir_all(self.basepath.join(name))?;
        Ok(())
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

pub fn publish_one(
    url: &url::Url,
    local_tree: &breezyshim::tree::WorkingTree,
    batch_name: &str,
    mode: Mode,
    existing_proposal_url: Option<&url::Url>,
    labels: Option<Vec<String>>,
    derived_owner: Option<&str>,
    refresh: bool,
    commit_message: Option<&str>,
    title: Option<&str>,
    description: Option<&str>,
    mut overwrite: Option<bool>,
) -> Result<PublishResult, PublishError> {
    let main_branch = match crate::vcs::open_branch(url, None, None, None) {
        Ok(b) => b,
        Err(e) => {
            log::error!("{}: {}", url, e);
            return Err(e.into());
        }
    };

    let (forge, existing_proposal, mut resume_branch) =
        match breezyshim::forge::get_forge(main_branch.as_ref()) {
            Ok(f) => {
                let (existing_proposal, resume_branch) = if let Some(existing_proposal_url) =
                    existing_proposal_url
                {
                    let existing_proposal = f.get_proposal_by_url(existing_proposal_url).unwrap();
                    let resume_branch_url =
                        existing_proposal.get_source_branch_url().unwrap().unwrap();
                    let resume_branch =
                        crate::vcs::open_branch(&resume_branch_url, None, None, None).unwrap();
                    (Some(existing_proposal), Some(resume_branch))
                } else {
                    (None, None)
                };
                (Some(f), existing_proposal, resume_branch)
            }
            Err(ForgeError::UnsupportedForge(e)) => {
                if mode != Mode::Push {
                    return Err(ForgeError::UnsupportedForge(e).into());
                }

                // We can't figure out what branch to resume from when there's no forge
                // that can tell us.
                log::warn!(
                    "Unsupported forge ({}), will attempt to push to {}",
                    e,
                    crate::vcs::full_branch_url(main_branch.as_ref()),
                );
                (None, None, None)
            }
            Err(e) => {
                log::error!("{}: {}", url, e);
                return Err(e.into());
            }
        };
    if refresh {
        if resume_branch.is_some() {
            overwrite = Some(true);
        }
        resume_branch = None;
    }
    if let Some(ref existing_proposal) = existing_proposal {
        log::info!("Updating {}", existing_proposal.url().unwrap());
    }

    let local_branch = local_tree.branch();

    crate::publish::enable_tag_pushing(local_branch.as_ref()).unwrap();

    let publish_result = match crate::publish::publish_changes(
        local_branch.as_ref(),
        main_branch.as_ref(),
        resume_branch.as_ref().map(|b| b.as_ref()),
        mode,
        batch_name,
        |_df, _ep| description.unwrap().to_string(),
        Some(|_ep: Option<&crate::proposal::MergeProposal>| commit_message.map(|s| s.to_string())),
        Some(|_ep: Option<&crate::proposal::MergeProposal>| title.map(|s| s.to_string())),
        forge.as_ref(),
        Some(true),
        None,
        overwrite,
        existing_proposal,
        labels,
        None,
        derived_owner,
        None,
        None,
    ) {
        Ok(r) => r,
        Err(e) => match e {
            PublishError::UnsupportedForge(ref url) => {
                log::error!("No known supported forge for {}. Run 'svp login'?", url);
                return Err(e);
            }
            PublishError::InsufficientChangesForNewProposal => {
                log::info!("Insufficient changes for a new merge proposal");
                return Err(e);
            }
            PublishError::DivergedBranches() => {
                if resume_branch.is_none() {
                    return Err(PublishError::UnrelatedBranchExists);
                }
                log::warn!("Branch exists that has diverged");
                return Err(e);
            }
            PublishError::ForgeLoginRequired => {
                log::error!(
                    "Credentials for hosting site at {} missing. Run 'svp login'?",
                    url
                );
                return Err(e);
            }
            _ => {
                log::error!("Failed to publish: {}", e);
                return Err(e);
            }
        },
    };

    if let Some(ref proposal) = publish_result.proposal {
        if publish_result.is_new == Some(true) {
            log::info!("Merge proposal created.");
        } else {
            log::info!("Merge proposal updated.")
        }
        log::info!("URL: {}", proposal.url().unwrap());
        log::info!(
            "Description: {}",
            proposal.get_description().unwrap().unwrap()
        );
    }
    Ok(publish_result)
}
