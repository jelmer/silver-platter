use breezyshim::prelude::Repository;
use breezyshim::workingtree;
use breezyshim::workspace::{check_clean_tree, reset_tree};
use breezyshim::{Branch, WorkingTree};
use clap::{Args, Parser, Subcommand};
use log::{error, info};
use silver_platter::candidates::Candidates;
use silver_platter::codemod::{script_runner, CommandResult};
use silver_platter::proposal::{MergeProposal, MergeProposalStatus};
use silver_platter::publish::Error as PublishError;
use silver_platter::CodemodResult;

use silver_platter::Mode;
use std::io::Write;
use std::path::Path;

#[derive(Parser)]
#[command(author, version, about, long_about = None)]
#[command(propagate_version = true)]
struct Cli {
    #[command(subcommand)]
    command: Commands,

    #[arg(short, long)]
    debug: bool,
}

#[derive(Subcommand)]
enum Commands {
    /// List all forges
    Forges {},

    /// Login to a forge
    Login {
        url: url::Url,
    },

    /// List merge proposals by the current user
    Proposals {
        // Status is one of "open", "merged" or "closed"
        #[arg(short, long, default_value = "open")]
        status: Option<MergeProposalStatus>,
    },

    Run(RunArgs),

    /// Apply a script to make a change in an existing local checkout
    Apply {
        /// Path to script to run
        command: Option<String>,

        /// Show diff of generated changes
        #[arg(long)]
        diff: bool,

        /// Command pending changes after script
        #[arg(long)]
        commit_pending: Option<silver_platter::CommitPending>,

        /// Command to verify changes
        #[arg(long)]
        verify_command: Option<String>,

        /// Recipe to use
        #[arg(long)]
        recipe: Option<std::path::PathBuf>,
    },

    #[clap(subcommand)]
    Batch(BatchArgs),
}

/// Run a script to make a change, and publish (propose/push/etc) it
#[derive(Args)]
struct RunArgs {
    url: Option<url::Url>,

    /// Path to script to run
    #[arg(long)]
    command: Option<String>,

    /// Owner for derived branches
    #[arg(long)]
    derived_owner: Option<String>,

    /// Refresh changes if branch already exists
    #[arg(long)]
    refresh: bool,

    /// Label to attach
    #[arg(long)]
    label: Option<Vec<String>>,

    /// Proposed branch name
    #[arg(long)]
    branch: Option<String>,

    /// Show diff of generated changes
    #[arg(long)]
    diff: bool,

    /// Mode for pushing
    #[arg(long)]
    push: Option<silver_platter::Mode>,

    /// Commit pending changes after script
    /// One of: ["yes", "no", "auto"]
    #[arg(long, default_value = "auto")]
    commit_pending: Option<silver_platter::CommitPending>,

    /// Command to verify changes
    #[arg(long)]
    verify_command: Option<String>,

    /// Recipe to use
    #[arg(long)]
    recipe: Option<std::path::PathBuf>,

    /// File with candidate list
    #[arg(long)]
    candidates: Option<std::path::PathBuf>,

    /// Mode for publishing
    #[arg(long)]
    mode: Option<silver_platter::Mode>,
}

/// Operate on multiple repositories at once
#[derive(Subcommand)]
enum BatchArgs {
    /// Generate a batch
    Generate {
        /// Recipe to use
        #[arg(long)]
        recipe: Option<std::path::PathBuf>,

        /// File with candidate list
        #[arg(long)]
        candidates: Option<std::path::PathBuf>,

        /// Directory to run in
        directory: Option<std::path::PathBuf>,
    },
    /// Publish a batch or specific entry
    Publish {
        /// Directory to run in
        directory: std::path::PathBuf,

        /// Specific entry to publish
        name: Option<String>,

        /// Whether to overwrite existing branches
        #[arg(long)]
        overwrite: bool,

        /// Refresh changes
        #[arg(long)]
        refresh: bool,
    },
    /// Show status of a batch or specific entry
    Status {
        /// Directory to run in
        directory: std::path::PathBuf,

        /// Specific entry to publish
        codebase: Option<String>,
    },
    /// Show diff of a specific entry in a batch
    Diff {
        /// Directory to run in
        directory: std::path::PathBuf,

        /// Specific entry to publish
        codebase: String,
    },
    /// Refresh changes
    Refresh {
        /// Directory to run in
        directory: std::path::PathBuf,

        /// Specific entry to publish
        codebase: Option<String>,
    },
}

fn run(args: &RunArgs) -> i32 {
    let mut extra_env = std::collections::HashMap::new();
    let recipe = args
        .recipe
        .as_ref()
        .map(|recipe| silver_platter::recipe::Recipe::from_path(recipe.as_path()).unwrap());

    if let Some(recipe) = &args.recipe {
        extra_env.insert(
            "RECIPEDIR".to_string(),
            recipe.parent().unwrap().to_str().unwrap().to_string(),
        );
    }

    let mut urls = vec![];

    if let Some(url) = args.url.as_ref() {
        urls.push(url.clone());
    }

    if let Some(candidates) = args.candidates.as_ref() {
        let candidates = Candidates::from_path(candidates.as_path()).unwrap();
        urls.extend(candidates.iter().map(|c| c.url.clone()));
    }

    let commit_pending = if let Some(commit_pending) = args.commit_pending {
        commit_pending
    } else if let Some(recipe) = &recipe {
        recipe.commit_pending
    } else {
        silver_platter::CommitPending::Auto
    };

    let command = if let Some(command) = args.command.as_ref() {
        shlex::split(command.as_str()).unwrap()
    } else if let Some(recipe) = &recipe {
        recipe.command.as_ref().unwrap().argv()
    } else {
        error!("No command specified");
        return 1;
    };

    let branch = if let Some(branch) = args.branch.as_ref() {
        branch.clone()
    } else if let Some(recipe) = recipe.as_ref() {
        recipe.name.clone().unwrap()
    } else {
        silver_platter::derived_branch_name(command.first().unwrap()).to_string()
    };

    let mode = if let Some(mode) = args.mode {
        mode
    } else if let Some(recipe) = &recipe {
        recipe.mode.unwrap()
    } else {
        silver_platter::Mode::Propose
    };

    let mut refresh = args.refresh;

    if let Some(ref recipe) = recipe {
        if recipe.resume.is_some() {
            refresh = true;
        }
    }

    let recipe_ref = recipe.as_ref();
    let allow_create_proposal = |result: &CommandResult| -> bool {
        if let Some(value) = result.value.as_ref() {
            if let Some(recipe) = recipe_ref {
                if let Some(merge_request) = recipe.merge_request.as_ref() {
                    if let Some(propose_threshold) = merge_request.propose_threshold {
                        return *value >= propose_threshold;
                    }
                }
            }
        }
        true
    };

    let recipe_ref = recipe.as_ref();
    let get_commit_message = |result: &CommandResult, existing_proposal: Option<&MergeProposal>| {
        if let Some(recipe) = recipe_ref {
            if let Some(merge_request) = recipe.merge_request.as_ref() {
                return merge_request
                    .render_commit_message(&result.tera_context())
                    .unwrap();
            }
        }
        if let Some(existing_proposal) = existing_proposal.as_ref() {
            return existing_proposal.get_commit_message().unwrap();
        }
        None
    };

    let recipe_ref = recipe.as_ref();
    let get_title = |result: &CommandResult, existing_proposal: Option<&MergeProposal>| {
        if let Some(recipe) = recipe_ref {
            if let Some(merge_request) = recipe.merge_request.as_ref() {
                return merge_request.render_title(&result.tera_context()).unwrap();
            }
        }
        if let Some(existing_proposal) = existing_proposal {
            return existing_proposal.get_title().unwrap();
        }
        None
    };

    let get_description = |result: &CommandResult,
                           description_format,
                           existing_proposal: Option<&MergeProposal>|
     -> String {
        if let Some(recipe) = recipe.as_ref() {
            if let Some(merge_request) = recipe.merge_request.as_ref() {
                let description = merge_request
                    .render_description(description_format, &result.tera_context())
                    .unwrap();
                if let Some(description) = description {
                    return description;
                }
            }
        }
        if let Some(description) = result.description.as_ref() {
            return description.clone();
        }
        if let Some(existing_proposal) = existing_proposal {
            return existing_proposal.get_description().unwrap().unwrap();
        }
        panic!("No description available");
    };

    let mut retcode = 0;

    let labels_ref = args
        .label
        .as_ref()
        .map(|labels| labels.iter().map(|s| s.as_str()).collect::<Vec<_>>());

    for url in urls {
        let result = silver_platter::run::apply_and_publish(
            &url,
            branch.as_str(),
            command
                .iter()
                .map(|s| s.as_str())
                .collect::<Vec<_>>()
                .as_slice(),
            mode,
            commit_pending,
            labels_ref.as_deref(),
            args.diff,
            args.verify_command.as_deref(),
            args.derived_owner.as_deref(),
            refresh,
            Some(allow_create_proposal),
            Some(get_commit_message),
            Some(get_title),
            get_description,
            Some(extra_env.clone()),
        );
        retcode = std::cmp::max(retcode, result)
    }

    retcode
}

fn publish_entry(
    batch: &mut silver_platter::batch::Batch,
    name: &str,
    refresh: bool,
    overwrite: bool,
) -> bool {
    let batch_name = batch.name.clone();
    let entry = batch.get_mut(name).unwrap();
    let overwrite = if overwrite { Some(true) } else { None };
    let publish_result = match entry.publish(&batch_name, refresh, overwrite) {
        Ok(publish_result) => publish_result,
        Err(PublishError::EmptyMergeProposal) => {
            info!("No changes left");
            match batch.remove(name) {
                Ok(_) => {}
                Err(e) => {
                    error!("Failed to remove {}: {}", name, e);
                }
            }
            return true;
        }
        Err(PublishError::UnrelatedBranchExists) => {
            error!("An unrelated branch exists. Remove it or use --overwrite.");
            return false;
        }
        Err(e) => {
            error!("Failed to publish {}: {}", name, e);
            return false;
        }
    };

    match publish_result.mode {
        Mode::Push => {
            batch.remove(name).unwrap();
        }
        Mode::Propose => {
            entry.proposal_url = Some(publish_result.proposal.unwrap().url().unwrap());
        }
        Mode::PushDerived => {
            batch.remove(name).unwrap();
        }
        _ => {
            unreachable!();
        }
    }
    true
}

pub fn batch_refresh(directory: &Path, codebase: Option<&str>) -> Result<(), i32> {
    let directory = directory.canonicalize().unwrap();

    let mut batch = match silver_platter::batch::load_batch_metadata(&directory) {
        Ok(Some(batch)) => batch,
        Ok(None) => {
            info!("No batch.yaml found in {}", directory.display());
            return Err(1);
        }
        Err(e) => {
            error!(
                "Failed to load batch metadata from {}: {}",
                directory.display(),
                e
            );
            return Err(1);
        }
    };

    let mut errors = 0;

    if let Some(codebase) = codebase {
        let entry = batch.work.get_mut(codebase).unwrap();
        if entry.refresh(&batch.recipe, None).is_err() {
            errors += 1;
        }
    } else {
        let names = batch.work.keys().cloned().collect::<Vec<_>>();
        for name in names {
            let entry = batch.work.get_mut(name.as_str()).unwrap();
            if entry.refresh(&batch.recipe, None).is_err() {
                errors += 1;
            }
        }
    }
    match silver_platter::batch::save_batch_metadata(&directory, &batch) {
        Ok(_) => {}
        Err(e) => {
            error!(
                "Failed to save batch metadata to {}: {}",
                directory.display(),
                e
            );
            return Err(1);
        }
    }
    if batch.work.is_empty() {
        info!(
            "No work left in batch.yaml; you can now remove {}",
            directory.display()
        );
    }
    if errors > 0 {
        Err(1)
    } else {
        Ok(())
    }
}

pub fn batch_publish(
    directory: &Path,
    codebase: Option<&str>,
    refresh: bool,
    overwrite: bool,
) -> Result<(), i32> {
    let directory = directory.canonicalize().unwrap();
    let mut batch = match silver_platter::batch::load_batch_metadata(&directory) {
        Ok(Some(batch)) => batch,
        Ok(None) => {
            info!("No batch.yaml found in {}", directory.display());
            return Err(1);
        }
        Err(e) => {
            error!(
                "Failed to load batch metadata from {}: {}",
                directory.display(),
                e
            );
            return Err(1);
        }
    };

    let mut errors = 0;
    if let Some(codebase) = codebase {
        if publish_entry(&mut batch, codebase, refresh, overwrite) {
            silver_platter::batch::save_batch_metadata(&directory, &batch).unwrap();
        } else {
            error!("Failed to publish {}", codebase);
            errors = 1;
        }
    } else {
        let names = batch.work.keys().cloned().collect::<Vec<_>>();
        for name in names {
            if !publish_entry(&mut batch, name.as_str(), refresh, overwrite) {
                errors += 1;
            }
        }
        silver_platter::batch::save_batch_metadata(&directory, &batch).unwrap();
    }
    if batch.work.is_empty() {
        info!(
            "No work left in batch.yaml; you can now remove {}",
            directory.display()
        );
    }
    if errors > 0 {
        Err(1)
    } else {
        Ok(())
    }
}

fn is_launchpad_url(url: &url::Url) -> bool {
    #[cfg(feature = "launchpad")]
    {
        launchpadlib::uris::is_launchpad_url(url)
    }
    #[cfg(not(feature = "launchpad"))]
    {
        url.host_str() == Some("launchpad.net")
            || url
                .host_str()
                .map_or(false, |h| h.ends_with(".launchpad.net"))
    }
}

fn login(url: &url::Url) -> i32 {
    let forge = if url.host_str() == Some("github.com") {
        "github"
    } else if is_launchpad_url(url) {
        "launchpad"
    } else {
        "gitlab"
    };

    match forge {
        "gitlab" => {
            breezyshim::gitlab::login(url).unwrap();
        }
        "github" => {
            breezyshim::github::login().unwrap();
        }
        "launchpad" => {
            breezyshim::launchpad::login(url);
        }
        _ => {
            panic!("Unknown forge {}", forge);
        }
    }

    0
}

fn main() -> Result<(), i32> {
    let cli = Cli::parse();

    env_logger::builder()
        .format(|buf, record| writeln!(buf, "{}", record.args()))
        .filter(
            None,
            if cli.debug {
                log::LevelFilter::Debug
            } else {
                log::LevelFilter::Info
            },
        )
        .init();

    breezyshim::init();
    breezyshim::plugin::load_plugins();

    match &cli.command {
        Commands::Forges {} => {
            for instance in breezyshim::forge::iter_forge_instances() {
                println!("{} ({})", instance.base_url(), instance.forge_kind());
            }
            Ok(())
        }
        Commands::Login { url } => match login(url) {
            0 => Ok(()),
            e => Err(e),
        },
        Commands::Proposals { status } => {
            let statuses = status.as_ref().map(|status| vec![*status]);
            for (_forge, proposal) in silver_platter::proposal::iter_all_mps(statuses) {
                println!("{}", proposal.url().unwrap());
            }
            Ok(())
        }
        Commands::Run(args) => match run(args) {
            0 => Ok(()),
            e => Err(e),
        },
        Commands::Apply {
            command,
            diff,
            commit_pending,
            verify_command,
            recipe,
        } => {
            let recipe = recipe
                .as_ref()
                .map(|recipe| silver_platter::recipe::Recipe::from_path(recipe).unwrap());

            let commit_pending = if let Some(commit_pending) = commit_pending {
                *commit_pending
            } else if let Some(recipe) = &recipe {
                recipe.commit_pending
            } else {
                silver_platter::CommitPending::Auto
            };

            let command = if let Some(command) = command.as_ref() {
                shlex::split(command.as_str()).unwrap()
            } else if let Some(recipe) = &recipe {
                recipe.command.clone().unwrap().argv()
            } else {
                error!("No command specified");
                return Err(1);
            };

            let (local_tree, subpath) = workingtree::open_containing(Path::new(".")).unwrap();

            check_clean_tree(
                &local_tree,
                &local_tree.basis_tree().unwrap(),
                subpath.as_path(),
            )
            .unwrap();

            let result = match script_runner(
                &local_tree,
                command
                    .iter()
                    .map(|s| s.as_str())
                    .collect::<Vec<_>>()
                    .as_slice(),
                subpath.as_path(),
                commit_pending,
                None,
                None,
                None,
                std::process::Stdio::inherit(),
            ) {
                Ok(result) => result,
                Err(err) => {
                    error!("Failed: {}", err);
                    reset_tree(&local_tree, None, Some(subpath.as_path())).unwrap();
                    return Err(1);
                }
            };

            if let Some(description) = result.description {
                info!("Succeeded: {} ", description);
            }

            if let Some(verify_command) = verify_command {
                match std::process::Command::new(verify_command)
                    .current_dir(local_tree.abspath(subpath.as_path()).unwrap())
                    .status()
                {
                    Ok(status) if status.success() => {}
                    Ok(status) => {
                        error!("Verify command failed: {}", status);
                        reset_tree(&local_tree, None, Some(subpath.as_path())).unwrap();
                        return Err(1);
                    }
                    Err(err) => {
                        error!("Verify command failed: {}", err);
                        reset_tree(&local_tree, None, Some(subpath.as_path())).unwrap();
                        return Err(1);
                    }
                }
            }

            if *diff {
                let old_tree = local_tree.revision_tree(&result.old_revision).unwrap();
                let new_tree = local_tree.revision_tree(&result.new_revision).unwrap();
                breezyshim::diff::show_diff_trees(
                    old_tree.as_ref(),
                    new_tree.as_ref(),
                    Box::new(std::io::stdout()),
                    Some("old/"),
                    Some("new/"),
                )
                .unwrap();
            }
            Ok(())
        }
        Commands::Batch(args) => match args {
            BatchArgs::Generate {
                recipe,
                candidates,
                directory,
            } => {
                let mut extra_env = std::collections::HashMap::new();

                let recipe = if let Some(recipe) = recipe {
                    extra_env.insert(
                        "RECIPEDIR".to_string(),
                        recipe
                            .as_path()
                            .parent()
                            .unwrap()
                            .to_str()
                            .unwrap()
                            .to_string(),
                    );
                    silver_platter::recipe::Recipe::from_path(recipe.as_path()).unwrap()
                } else {
                    panic!("No recipe specified");
                };

                let candidates = if let Some(candidate_list) = candidates {
                    Candidates::from_path(candidate_list.as_path()).unwrap()
                } else {
                    Candidates::new()
                };

                let directory = if let Some(directory) = directory.as_ref() {
                    directory.clone()
                } else {
                    info!("Using output directory: {}", recipe.name.as_ref().unwrap());
                    std::path::PathBuf::from(recipe.name.clone().unwrap())
                };

                match silver_platter::batch::Batch::from_recipe(
                    &recipe,
                    candidates.iter(),
                    directory.as_path(),
                    Some(extra_env),
                ) {
                    Ok(_batch) => {}
                    Err(e) => {
                        error!("Failed to generate batch: {}", e);
                        return Err(1);
                    }
                }
                info!("Now, review the patches under {}, edit {} as appropriate and then run \"svp batch publish {}\"", directory.display(), directory.join("batch.yaml").display(), directory.display());
                info!(
                    "You can run \"svp batch status {}\" to see the status of the patches",
                    directory.display()
                );
                info!(
                    "To refresh the patches, run \"svp batch refresh {}\"",
                    directory.display()
                );
                Ok(())
            }
            BatchArgs::Publish {
                directory,
                name,
                overwrite,
                refresh,
            } => {
                let ret = batch_publish(directory.as_path(), name.as_deref(), *refresh, *overwrite);

                info!(
                    "To see the status of open merge requests, run: \"svp batch status {}\"",
                    directory.display()
                );
                ret
            }
            BatchArgs::Status {
                directory,
                codebase,
            } => {
                let directory = directory.canonicalize().unwrap();
                let batch = match silver_platter::batch::load_batch_metadata(&directory) {
                    Ok(Some(batch)) => batch,
                    Ok(None) => {
                        info!("No batch.yaml found in {}", directory.display());
                        return Err(1);
                    }
                    Err(e) => {
                        error!(
                            "Failed to load batch metadata from {}: {}",
                            directory.display(),
                            e
                        );
                        return Err(1);
                    }
                };
                if let Some(codebase) = codebase {
                    let entry = batch.work.get(codebase).unwrap();
                    info!("{}: {}", codebase, entry.status());
                } else {
                    for (name, entry) in batch.work.iter() {
                        info!("{}: {}", name, entry.status());
                    }
                }
                Ok(())
            }
            BatchArgs::Diff {
                directory,
                codebase,
            } => {
                let directory = directory.canonicalize().unwrap();
                let batch = match silver_platter::batch::load_batch_metadata(&directory) {
                    Ok(Some(batch)) => batch,
                    Ok(None) => {
                        info!("No batch.yaml found in {}", directory.display());
                        return Err(1);
                    }
                    Err(e) => {
                        error!(
                            "Failed to load batch metadata from {}: {}",
                            directory.display(),
                            e
                        );
                        return Err(1);
                    }
                };
                let entry = batch.work.get(codebase.as_str()).unwrap();
                let main_branch = match entry.target_branch() {
                    Ok(branch) => branch,
                    Err(e) => {
                        error!("Failed to open branch: {}", e);
                        return Err(1);
                    }
                };

                let local_branch = match entry.local_branch() {
                    Ok(branch) => branch,
                    Err(e) => {
                        error!("Failed to open branch: {}", e);
                        return Err(1);
                    }
                };

                let repository = local_branch.repository();

                let main_revision = main_branch.last_revision();

                repository
                    .fetch(&main_branch.repository(), Some(&main_revision))
                    .unwrap();

                let main_tree = repository.revision_tree(&main_revision).unwrap();

                breezyshim::diff::show_diff_trees(
                    &main_tree,
                    &local_branch.basis_tree().unwrap(),
                    Box::new(std::io::stdout()),
                    Some("old/"),
                    Some("new/"),
                )
                .unwrap();
                Err(1)
            }
            BatchArgs::Refresh {
                directory,
                codebase,
            } => batch_refresh(directory.as_path(), codebase.as_deref()),
        },
    }
}
