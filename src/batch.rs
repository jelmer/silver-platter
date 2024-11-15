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
use breezyshim::error::Error as BrzError;
use serde::Deserialize;
use std::collections::HashMap;
use std::io::Write;
use std::path::{Path, PathBuf};
use url::Url;

/// Current version of the batch format.
pub const CURRENT_VERSION: u8 = 1;

#[derive(Debug, serde::Serialize, serde::Deserialize)]
/// Batch entry
pub struct Entry {
    #[serde(skip)]
    local_path: PathBuf,

    /// Subpath within the local path to work on.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub subpath: Option<PathBuf>,

    /// URL of the target branch.
    #[serde(rename = "url")]
    pub target_branch_url: Option<Url>,

    /// Description of the work to be done.
    pub description: String,

    #[serde(
        rename = "commit-message",
        default,
        skip_serializing_if = "Option::is_none"
    )]
    /// Commit message for the work.
    pub commit_message: Option<String>,

    #[serde(
        rename = "auto-merge",
        default,
        skip_serializing_if = "Option::is_none"
    )]
    /// Whether to automatically merge the proposal.
    pub auto_merge: Option<bool>,

    /// Mode for the work.
    pub mode: Mode,

    /// Title of the work.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub title: Option<String>,

    /// Owner of the work.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub owner: Option<String>,

    /// Labels for the work.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub labels: Option<Vec<String>>,

    /// Context for the work.
    #[serde(default, skip_serializing_if = "serde_yaml::Value::is_null")]
    pub context: serde_yaml::Value,

    #[serde(
        rename = "proposal-url",
        default,
        skip_serializing_if = "Option::is_none"
    )]
    /// URL of the proposal for this work.
    pub proposal_url: Option<Url>,
}

#[derive(Debug, serde::Deserialize, serde::Serialize)]
/// Batch
pub struct Batch {
    /// Format version
    #[serde(default)]
    pub version: u8,

    /// Recipe
    #[serde(deserialize_with = "deserialize_recipe")]
    pub recipe: Recipe,

    /// Batch name
    pub name: String,

    /// Work to be done in this batch.
    pub work: HashMap<String, Entry>,

    #[serde(skip)]
    /// Basepath for the batch
    pub basepath: PathBuf,
}

fn deserialize_recipe<'de, D>(deserializer: D) -> Result<Recipe, D::Error>
where
    D: serde::Deserializer<'de>,
{
    // Recipe can either be a PathBuf or a Recipe
    #[derive(serde::Deserialize)]
    #[serde(untagged)]
    enum RecipeOrPathBuf {
        Recipe(Recipe),
        PathBuf(PathBuf),
    }

    let value = RecipeOrPathBuf::deserialize(deserializer)?;

    match value {
        RecipeOrPathBuf::Recipe(recipe) => Ok(recipe),
        RecipeOrPathBuf::PathBuf(path) => {
            let file = std::fs::File::open(&path).map_err(serde::de::Error::custom)?;
            let recipe: Recipe = serde_yaml::from_reader(file).map_err(serde::de::Error::custom)?;
            Ok(recipe)
        }
    }
}

#[derive(Debug)]
/// Batch error
pub enum Error {
    /// Error running a script
    Script(crate::codemod::Error),

    /// Error opening a branch
    Vcs(crate::vcs::BranchOpenError),

    /// I/O error
    Io(std::io::Error),

    /// Error parsing YAML
    Yaml(serde_yaml::Error),

    /// Error with Tera
    Tera(tera::Error),

    /// Error with workspace
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
    /// Create a new batch entry from a recipe.
    pub fn from_recipe(
        recipe: &Recipe,
        basepath: &Path,
        url: &Url,
        subpath: &Path,
        default_mode: Option<Mode>,
        extra_env: Option<HashMap<String, String>>,
    ) -> Result<Self, Error> {
        if !basepath.exists() {
            std::fs::create_dir_all(basepath)?;
        }
        let basepath = basepath.canonicalize().unwrap();
        let main_branch = match open_branch(url, None, None, None) {
            Ok(branch) => branch,
            Err(e) => return Err(Error::Vcs(e)),
        };

        let ws = Workspace::builder()
            .main_branch(main_branch)
            .path(basepath.to_path_buf())
            .build()?;

        log::info!(
            "Making changes to {}",
            ws.main_branch().unwrap().get_user_url()
        );

        let result = match script_runner(
            ws.local_tree(),
            recipe
                .command
                .as_ref()
                .unwrap()
                .argv()
                .iter()
                .map(|s| s.as_str())
                .collect::<Vec<_>>()
                .as_slice(),
            subpath,
            recipe.commit_pending,
            None,
            None,
            extra_env,
            std::process::Stdio::inherit(),
        ) {
            Ok(result) => result,
            Err(e) => return Err(Error::Script(e)),
        };

        let tera_context: tera::Context = tera::Context::from_value(
            result
                .context
                .clone()
                .unwrap_or_else(|| serde_json::json!({})),
        )
        .unwrap();

        let target_branch_url = match result.target_branch_url {
            Some(url) => Some(url),
            None => Some(url.clone()),
        };
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

        let auto_merge = recipe.merge_request.as_ref().and_then(|mr| mr.auto_merge);

        let owner = None;

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
            auto_merge,
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

    /// Return the status of this entry
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

    /// Get the local working tree for this entry.
    pub fn working_tree(&self) -> Result<breezyshim::tree::WorkingTree, BrzError> {
        breezyshim::workingtree::open(&self.local_path)
    }

    /// Get the target branch for this entry.
    pub fn target_branch(&self) -> Result<Box<dyn Branch>, BranchOpenError> {
        open_branch(self.target_branch_url.as_ref().unwrap(), None, None, None)
    }

    /// Get the local branch for this entry.
    pub fn local_branch(&self) -> Result<Box<dyn Branch>, BranchOpenError> {
        let url = match url::Url::from_directory_path(&self.local_path) {
            Ok(url) => url,
            Err(_) => {
                return Err(BranchOpenError::Other(format!(
                    "Invalid URL: {}",
                    self.local_path.display()
                )));
            }
        };
        open_branch(&url, None, None, None)
    }

    /// Refresh the changes for this entry.
    pub fn refresh(
        &mut self,
        recipe: &Recipe,
        extra_env: Option<HashMap<String, String>>,
    ) -> Result<(), Error> {
        let url = self.target_branch_url.as_ref().unwrap();
        let main_branch = match open_branch(url, None, None, None) {
            Ok(branch) => branch,
            Err(e) => return Err(Error::Vcs(e)),
        };

        let ws = Workspace::builder()
            .main_branch(main_branch)
            .path(self.local_path.clone())
            .build()?;

        log::info!(
            "Making changes to {}",
            ws.main_branch().unwrap().get_user_url()
        );

        assert_eq!(
            ws.main_branch().unwrap().last_revision(),
            ws.local_tree().last_revision().unwrap()
        );

        let result = match script_runner(
            ws.local_tree(),
            recipe
                .command
                .as_ref()
                .unwrap()
                .argv()
                .iter()
                .map(|s| s.as_str())
                .collect::<Vec<_>>()
                .as_slice(),
            self.subpath.as_deref().unwrap_or_else(|| Path::new("")),
            recipe.commit_pending,
            None,
            None,
            extra_env,
            std::process::Stdio::inherit(),
        ) {
            Ok(result) => result,
            Err(e) => return Err(Error::Script(e)),
        };

        let tera_context: tera::Context = tera::Context::from_value(
            result
                .context
                .clone()
                .unwrap_or_else(|| serde_json::json!({})),
        )
        .unwrap();

        let target_branch_url = match result.target_branch_url {
            Some(url) => Some(url),
            None => Some(url.clone()),
        };
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
        let mode = recipe.mode.unwrap_or_default();
        let labels = recipe.labels.clone();
        let context = result.context;

        let auto_merge = recipe.merge_request.as_ref().and_then(|mr| mr.auto_merge);

        let owner = None;

        self.target_branch_url = target_branch_url;
        self.description = description;
        self.commit_message = commit_message;
        self.mode = mode;
        self.owner = owner;
        self.title = title;
        self.labels = labels;
        self.auto_merge = auto_merge;
        self.context = serde_yaml::from_str(
            context
                .unwrap_or(serde_json::Value::Null)
                .to_string()
                .as_str(),
        )
        .unwrap();

        Ok(())
    }

    /// Publish this entry
    pub fn publish(
        &self,
        batch_name: &str,
        refresh: bool,
        overwrite: Option<bool>,
    ) -> Result<PublishResult, PublishError> {
        let target_branch_url = match self.target_branch_url.as_ref() {
            Some(url) => url,
            None => {
                return Err(PublishError::NoTargetBranch);
            }
        };

        publish_one(
            target_branch_url,
            &self.working_tree().unwrap(),
            batch_name,
            self.mode,
            self.proposal_url.as_ref(),
            self.labels.clone(),
            self.owner.as_deref(),
            refresh,
            self.commit_message.as_deref(),
            self.title.as_deref(),
            Some(self.description.as_str()),
            overwrite,
            self.auto_merge,
        )
    }
}

/// Status of a batch entry.
pub enum Status {
    /// Merged - URL of the merge proposal.
    Merged(Url),

    /// Closed - URL of the merge proposal.
    Closed(Url),

    /// Open - URL of the merge proposal.
    Open(Url),

    /// Not published yet.
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
    /// Create a batch from a recipe and a set of candidates.
    pub fn from_recipe<'a>(
        recipe: &Recipe,
        candidates: impl Iterator<Item = &'a Candidate>,
        directory: &Path,
        extra_env: Option<HashMap<String, String>>,
    ) -> Result<Batch, Error> {
        // The directory should either be empty or not exist
        if directory.exists() {
            if !directory.is_dir() {
                return Err(Error::Io(std::io::Error::new(
                    std::io::ErrorKind::AlreadyExists,
                    "Not a directory",
                )));
            }
            if let Ok(entries) = std::fs::read_dir(&directory) {
                if entries.count() > 0 {
                    return Err(Error::Io(std::io::Error::new(
                        std::io::ErrorKind::AlreadyExists,
                        "Directory not empty",
                    )));
                }
            }
        } else {
            std::fs::create_dir_all(&directory)?;
        }

        // make sure directory is an absolute path
        let directory = directory.canonicalize().unwrap();

        let mut batch = match load_batch_metadata(&directory) {
            Ok(Some(batch)) => batch,
            Ok(None) => Batch {
                version: CURRENT_VERSION,
                recipe: recipe.clone(),
                name: recipe.name.clone().unwrap(),
                work: HashMap::new(),
                basepath: directory.to_path_buf(),
            },
            Err(e) => return Err(e),
        };

        for candidate in candidates {
            let basename: String = candidate.shortname();

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
                extra_env.clone(),
            ) {
                Ok(entry) => {
                    batch.work.insert(name, entry);
                    save_batch_metadata(&directory, &batch)?;
                }
                Err(e) => {
                    log::error!("Failed to generate batch entry for {}: {}", name, e);
                    // Recursively remove work_path
                    std::fs::remove_dir_all(work_path)?;
                    continue;
                }
            }
        }
        save_batch_metadata(&directory, &batch)?;
        Ok(batch)
    }

    /// Get reference to a batch entry.
    pub fn get(&self, name: &str) -> Option<&Entry> {
        self.work.get(name)
    }

    /// Get a mutable reference to a batch entry.
    pub fn get_mut(&mut self, name: &str) -> Option<&mut Entry> {
        self.work.get_mut(name)
    }

    /// Returen the status of all work in the batch.
    pub fn status(&self) -> HashMap<&str, Status> {
        let mut status = HashMap::new();
        for (name, entry) in self.work.iter() {
            status.insert(name.as_str(), entry.status());
        }
        status
    }

    /// Remove work from the batch.
    pub fn remove(&mut self, name: &str) -> Result<(), Error> {
        self.work.remove(name);
        let path = self.basepath.join(name);
        match std::fs::remove_dir_all(&path) {
            Ok(()) => Ok(()),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                log::warn!("{} ({}): already removed - {}", name, path.display(), e);
                Ok(())
            }
            Err(e) => Err(Error::Io(e)),
        }
    }
}

/// Drop a batch entry from the given directory.
pub fn drop_batch_entry(directory: &Path, name: &str) -> Result<(), Error> {
    let mut batch = match load_batch_metadata(directory)? {
        Some(batch) => batch,
        None => return Ok(()),
    };
    batch.work.remove(name);
    match std::fs::remove_dir_all(directory.join(name)) {
        Ok(()) => {}
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            log::warn!(
                "{} ({}): already removed - {}",
                name,
                directory.join(name).display(),
                e
            );
        }
        Err(e) => {
            return Err(Error::Io(e));
        }
    }
    save_batch_metadata(directory, &batch)?;
    Ok(())
}

/// Save batch metadata to the metadata file in the given directory.
pub fn save_batch_metadata(directory: &Path, batch: &Batch) -> Result<(), Error> {
    let mut file = std::fs::File::create(directory.join("batch.yaml"))?;
    serde_yaml::to_writer(&mut file, &batch)?;
    file.flush()?;
    Ok(())
}

/// Load a batch metadata from the metadata file in the given directory.
pub fn load_batch_metadata(directory: &Path) -> Result<Option<Batch>, Error> {
    assert!(directory.is_absolute());
    let file = match std::fs::File::open(directory.join("batch.yaml")) {
        Ok(f) => f,
        Err(e) => {
            if e.kind() == std::io::ErrorKind::NotFound {
                return Ok(None);
            }
            return Err(Error::Io(e));
        }
    };

    let mut batch: Batch = serde_yaml::from_reader(file)?;

    batch.basepath = directory.to_path_buf();

    // Set local path for entries
    for (key, entry) in batch.work.iter_mut() {
        entry.local_path = directory.join(key);
    }

    Ok(Some(batch))
}

/// Publish a single batch entry.
fn publish_one(
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
    auto_merge: Option<bool>,
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
                    let (resume_branch_url, params) =
                        breezyshim::urlutils::split_segment_parameters(&resume_branch_url);
                    let resume_branch_name = params.get("branch");
                    let resume_branch = crate::vcs::open_branch(
                        &resume_branch_url,
                        None,
                        None,
                        resume_branch_name.map(|x| x.as_str()),
                    )
                    .unwrap();
                    (Some(existing_proposal), Some(resume_branch))
                } else {
                    (None, None)
                };
                (Some(f), existing_proposal, resume_branch)
            }
            Err(BrzError::UnsupportedForge(e)) => {
                if mode != Mode::Push {
                    return Err(BrzError::UnsupportedForge(e).into());
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
        auto_merge,
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

#[cfg(test)]
mod tests {
    #[test]
    fn test_entry_from_recipe() {
        let td = tempfile::tempdir().unwrap();
        let remote = tempfile::tempdir().unwrap();
        breezyshim::controldir::create_branch_convenience(
            &url::Url::from_directory_path(remote.path()).unwrap(),
            None,
            &breezyshim::controldir::ControlDirFormat::default(),
        )
        .unwrap();
        let recipe = crate::recipe::RecipeBuilder::new();
        let recipe = recipe
            .shell("echo hello > hello.txt; echo hello".to_owned())
            .build();
        let entry = crate::batch::Entry::from_recipe(
            &recipe,
            td.path(),
            &url::Url::from_directory_path(&remote.path()).unwrap(),
            &std::path::Path::new(""),
            None,
            None,
        )
        .unwrap();
        assert_eq!(entry.description, "hello\n");
    }

    #[test]
    fn test_batch_from_recipe() {
        let td = tempfile::tempdir().unwrap();
        let remote = tempfile::tempdir().unwrap();
        breezyshim::controldir::create_branch_convenience(
            &url::Url::from_directory_path(remote.path()).unwrap(),
            None,
            &breezyshim::controldir::ControlDirFormat::default(),
        )
        .unwrap();
        let recipe = crate::recipe::RecipeBuilder::new();
        let recipe = recipe
            .name("hello".to_owned())
            .shell("echo hello > hello.txt; echo hello".to_owned())
            .build();
        let candidate = crate::candidates::Candidate {
            url: url::Url::from_directory_path(&remote.path()).unwrap(),
            subpath: None,
            default_mode: None,
            branch: None,
            name: Some("foo".to_owned()),
        };
        let batch =
            crate::batch::Batch::from_recipe(&recipe, std::iter::once(&candidate), td.path(), None)
                .unwrap();
        assert_eq!(batch.work.len(), 1);
        let entry = batch.work.get("foo").unwrap();
        assert_eq!(entry.description, "hello\n");
    }
}
