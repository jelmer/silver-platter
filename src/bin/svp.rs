use breezyshim::tree::WorkingTree;
use breezyshim::workspace::{check_clean_tree, reset_tree};
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
    Publish {
        /// Directory to run in
        directory: std::path::PathBuf,

        /// Specific entry to publish
        name: Option<String>,
    },
    Status {
        /// Directory to run in
        directory: std::path::PathBuf,

        /// Specific entry to publish
        codebase: Option<String>,
    },
    Diff {
        /// Directory to run in
        directory: std::path::PathBuf,

        /// Specific entry to publish
        codebase: String,
    },
}

fn run(args: &RunArgs) -> i32 {
    let recipe = args
        .recipe
        .as_ref()
        .map(|recipe| silver_platter::recipe::Recipe::from_path(recipe.as_path()).unwrap());

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
        recipe.command.clone().unwrap()
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
        );
        retcode = std::cmp::max(retcode, result)
    }

    retcode
}

pub fn publish_entry(
    batch: &mut silver_platter::batch::Batch,
    name: &str,
    refresh: bool,
    overwrite: Option<bool>,
) -> bool {
    let batch_name = batch.name.clone();
    let entry = batch.get_mut(name).unwrap();
    let tree = entry.working_tree().unwrap();
    let publish_result = match silver_platter::batch::publish_one(
        entry.target_branch_url.as_ref().unwrap(),
        &tree,
        batch_name.as_str(),
        entry.mode,
        entry.proposal_url.as_ref(),
        entry.labels.clone(),
        entry.owner.as_deref(),
        refresh,
        entry.commit_message.as_deref(),
        entry.title.as_deref(),
        Some(entry.description.as_str()),
        overwrite,
    ) {
        Ok(publish_result) => publish_result,
        Err(PublishError::EmptyMergeProposal) => {
            info!("No changes left");
            batch.remove(name).unwrap();
            return true;
        }
        Err(PublishError::UnrelatedBranchExists) => {
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

pub fn batch_publish(
    directory: &Path,
    codebase: Option<&str>,
    refresh: bool,
    overwrite: Option<bool>,
) -> i32 {
    let mut batch = silver_platter::batch::load_batch_metadata(directory).unwrap();

    let mut errors = 0;
    if let Some(codebase) = codebase {
        if publish_entry(&mut batch, codebase, refresh, overwrite) {
            silver_platter::batch::save_batch_metadata(directory, &batch).unwrap();
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
        silver_platter::batch::save_batch_metadata(directory, &batch).unwrap();
    }
    if batch.work.is_empty() {
        info!(
            "No work left in batch.yaml; you can now remove {}",
            directory.display()
        );
    }
    if errors > 0 {
        1
    } else {
        0
    }
}

fn main() {
    pyo3::prepare_freethreaded_python();
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

    breezyshim::init().unwrap();

    pyo3::Python::with_gil(|py| -> pyo3::PyResult<()> {
        let m = py.import("breezy.plugin").unwrap();
        let load_plugins = m.getattr("load_plugins").unwrap();
        load_plugins.call0().unwrap();
        Ok(())
    })
    .unwrap();

    std::process::exit(match &cli.command {
        Commands::Forges {} => {
            for instance in breezyshim::forge::iter_forge_instances() {
                println!("{} ({})", instance.base_url(), instance.forge_kind());
            }
            0
        }
        Commands::Login { url: _ } => {
            todo!();
        }
        Commands::Proposals { status } => {
            let statuses = status.as_ref().map(|status| vec![*status]);
            for (_forge, proposal) in silver_platter::proposal::iter_all_mps(statuses) {
                println!("{}", proposal.url().unwrap());
            }
            0
        }
        Commands::Run(args) => run(args),
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
                recipe.command.clone().unwrap()
            } else {
                error!("No command specified");
                std::process::exit(1);
            };

            let (local_tree, subpath) = WorkingTree::open_containing(Path::new(".")).unwrap();

            check_clean_tree(
                &local_tree,
                local_tree.basis_tree().as_ref(),
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
                    reset_tree(&local_tree, None, Some(subpath.as_path()), None).unwrap();
                    std::process::exit(1);
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
                        reset_tree(&local_tree, None, Some(subpath.as_path()), None).unwrap();
                        std::process::exit(1);
                    }
                    Err(err) => {
                        error!("Verify command failed: {}", err);
                        reset_tree(&local_tree, None, Some(subpath.as_path()), None).unwrap();
                        std::process::exit(1);
                    }
                }
            }

            if *diff {
                let old_tree = local_tree.revision_tree(&result.old_revision);
                let new_tree = local_tree.revision_tree(&result.new_revision);
                breezyshim::diff::show_diff_trees(
                    old_tree.as_ref(),
                    new_tree.as_ref(),
                    Box::new(std::io::stdout()),
                    Some("old/"),
                    Some("new/"),
                )
                .unwrap();
            }
            0
        }
        Commands::Batch(args) => match args {
            BatchArgs::Generate {
                recipe,
                candidates,
                directory,
            } => {
                let recipe = if let Some(recipe) = recipe {
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

                silver_platter::batch::Batch::from_recipe(
                    &recipe,
                    candidates.iter(),
                    directory.as_path(),
                )
                .unwrap();
                info!("Now, review the patches under {}, edit {}/batch.yaml as appropriate and then run \"svp batch publish {}\"", directory.display(), directory.display(), directory.display());
                0
            }
            BatchArgs::Publish { directory, name } => {
                let ret = batch_publish(directory.as_path(), name.as_deref(), false, None);

                info!(
                    "To see the status of open merge requests, run: \"svn batch status {}\"",
                    directory.display()
                );
                ret
            }
            BatchArgs::Status {
                directory,
                codebase,
            } => {
                let batch = silver_platter::batch::load_batch_metadata(directory).unwrap();
                if let Some(codebase) = codebase {
                    let entry = batch.work.get(codebase).unwrap();
                    info!("{}: {}", codebase, entry.status());
                } else {
                    for (name, entry) in batch.work.iter() {
                        info!("{}: {}", name, entry.status());
                    }
                }
                0
            }
            BatchArgs::Diff {
                directory,
                codebase,
            } => {
                let batch = silver_platter::batch::load_batch_metadata(directory).unwrap();
                let entry = batch.work.get(codebase.as_str()).unwrap();
                let main_branch = match entry.target_branch() {
                    Ok(branch) => branch,
                    Err(e) => {
                        error!("Failed to open branch: {}", e);
                        std::process::exit(1);
                    }
                };

                let local_branch = match entry.local_branch() {
                    Ok(branch) => branch,
                    Err(e) => {
                        error!("Failed to open branch: {}", e);
                        std::process::exit(1);
                    }
                };

                breezyshim::diff::show_diff_trees(
                    &local_branch.basis_tree().unwrap(),
                    &main_branch.basis_tree().unwrap(),
                    Box::new(std::io::stdout()),
                    Some("old/"),
                    Some("new/"),
                )
                .unwrap();
                1
            }
        },
    })
}
