use breezyshim::workingtree;
use breezyshim::workspace::{check_clean_tree, reset_tree};
use clap::{Args, Parser, Subcommand};
use log::{error, info};
use silver_platter::candidates::Candidates;
use silver_platter::debian::codemod::{script_runner, CommandResult};
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

        /// Build package to verify it
        #[arg(long)]
        build_verify: bool,

        /// Build command to use when verifying build
        #[arg(long, default_value(silver_platter::debian::DEFAULT_BUILDER))]
        builder: String,

        /// Store built Debian files in specific directory (with --build-verify)
        #[arg(long)]
        build_target_dir: Option<std::path::PathBuf>,

        /// Install built packages (implies --build-verify)
        #[arg(long)]
        install: bool,

        /// Report context on success
        #[arg(long)]
        dump_context: bool,

        /// Recipe to use
        #[arg(long)]
        recipe: Option<std::path::PathBuf>,

        /// Don't update changelog
        #[arg(long)]
        no_update_changelog: bool,

        /// Do update changelog
        #[arg(long)]
        update_changelog: bool,
    },

    #[clap(subcommand)]
    Batch(BatchArgs),

    UploadPending {
        /// List of acceptable GPG keys
        #[arg(long)]
        acceptable_keys: Option<Vec<String>>,

        /// Verify GPG signatures on commit
        #[arg(long)]
        gpg_verification: bool,

        /// Minimum age of the last commit, in days
        #[arg(long)]
        min_commit_age: Option<i64>,

        /// Show diff
        #[arg(long)]
        diff: bool,

        /// Build command
        #[arg(long, default_value_t = format!("{} --source --source-only-changes --debbuildopt=-v$(LAST_VERSION)", silver_platter::debian::DEFAULT_BUILDER))]
        builder: String,

        /// Select all packages maintained by specified maintainer.
        #[arg(long, conflicts_with = "packages")]
        maintainer: Option<Vec<String>>,

        /// Use vcswatch to determine what packages need uploading.
        #[arg(long)]
        vcswatch: bool,

        /// Ignore source package
        #[arg(long)]
        exclude: Option<Vec<String>>,

        /// Only process packages with autopkgtest
        #[arg(long)]
        autopkgtest_only: bool,

        /// Require that all new commits are from specified committers
        #[arg(long)]
        allowed_committer: Option<Vec<String>>,

        /// Randomize order packages are processed in.
        #[arg(long)]
        shuffle: bool,

        /// Command to verify whether upload is necessary. Should return 1 to decline, 0 to upload.
        #[arg(long)]
        verify_command: Option<String>,

        /// APT repository to use. Defaults to locally configured.
        #[arg(long, env = "APT_REPOSITORY")]
        apt_repository: Option<String>,

        /// APT repository key to use for validation, if --apt-repository is set.
        #[arg(long, env = "APT_REPOSITORY_KEY")]
        apt_repository_key: Option<std::path::PathBuf>,

        /// Packages to upload
        packages: Vec<String>,
    },
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

    /// Don't update changelog
    #[arg(long)]
    no_update_changelog: bool,

    /// Do update changelog
    #[arg(long)]
    update_changelog: bool,

    /// Build package to verify it
    #[arg(long)]
    build_verify: bool,

    /// Build command to use when verifying build
    #[arg(long, default_value(silver_platter::debian::DEFAULT_BUILDER))]
    builder: String,

    /// Store built Debian files in specific directory (with --build-verify)
    #[arg(long)]
    build_target_dir: Option<std::path::PathBuf>,

    /// Install built packages (implies --build-verify)
    #[arg(long)]
    install: bool,
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

    let update_changelog = if args.update_changelog {
        Some(true)
    } else if args.no_update_changelog {
        Some(false)
    } else {
        None
    };

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
                           _existing_proposal: Option<&MergeProposal>|
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
        return result.description.clone();
    };

    let mut retcode = 0;

    let labels_ref = args
        .label
        .as_ref()
        .map(|labels| labels.iter().map(|s| s.as_str()).collect::<Vec<_>>());

    for url in urls {
        let result = silver_platter::debian::run::apply_and_publish(
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
            args.derived_owner.as_deref(),
            refresh,
            Some(allow_create_proposal),
            Some(get_commit_message),
            Some(get_title),
            get_description,
            update_changelog,
            args.build_verify,
            args.build_target_dir.clone(),
            Some(args.builder.clone()),
            args.install,
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

fn login(url: &url::Url) -> i32 {
    let lp_uris = breezyshim::launchpad::uris().unwrap();

    let forge = if url.host_str() == Some("github.com") {
        "github"
    } else if lp_uris.iter().any(|(_key, root)| {
        url.host_str() == Some(root) || url.host_str() == Some(root.trim_end_matches('/'))
    }) {
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

fn main() {
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

    std::process::exit(match &cli.command {
        Commands::Forges {} => {
            for instance in breezyshim::forge::iter_forge_instances() {
                println!("{} ({})", instance.base_url(), instance.forge_kind());
            }
            0
        }
        Commands::Login { url } => login(url),
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
            install,
            mut build_verify,
            ref build_target_dir,
            builder,
            dump_context,
            no_update_changelog,
            update_changelog,
            recipe,
        } => {
            if *install {
                build_verify = true;
            }

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

            let (local_tree, subpath) = workingtree::open_containing(Path::new(".")).unwrap();

            check_clean_tree(
                &local_tree,
                &local_tree.basis_tree().unwrap(),
                subpath.as_path(),
            )
            .unwrap();

            let update_changelog = if *update_changelog {
                Some(true)
            } else if *no_update_changelog {
                Some(false)
            } else {
                None
            };

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
                update_changelog,
            ) {
                Ok(result) => result,
                Err(err) => {
                    error!("Failed: {}", err);
                    reset_tree(&local_tree, None, Some(subpath.as_path())).unwrap();
                    std::process::exit(1);
                }
            };

            let mut td = None;

            let mut build_target_dir = build_target_dir.clone();

            if build_verify {
                if build_target_dir.is_none() {
                    td = Some(tempfile::tempdir().unwrap());
                    build_target_dir = td.as_ref().map(|td| td.path().to_owned());
                }

                silver_platter::debian::build(
                    &local_tree,
                    &subpath,
                    Some(builder),
                    build_target_dir.as_deref(),
                )
                .unwrap();
            }

            info!("Succeeded: {} ", result.description);

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

            if *install {
                silver_platter::debian::install_built_package(
                    &local_tree,
                    subpath.as_path(),
                    build_target_dir.as_ref().unwrap(),
                )
                .unwrap();
            }

            if let Some(td) = td.take() {
                td.close().unwrap();
            }

            if *dump_context {
                let context = result.context.unwrap();
                println!("{}", serde_json::to_string_pretty(&context).unwrap());
            }
            0
        }
        Commands::UploadPending {
            acceptable_keys,
            gpg_verification,
            min_commit_age,
            diff,
            maintainer,
            builder,
            autopkgtest_only,
            vcswatch,
            shuffle,
            exclude,
            verify_command,
            allowed_committer,
            apt_repository,
            apt_repository_key,
            packages,
        } => silver_platter::debian::uploader::main(
            packages.clone(),
            acceptable_keys.clone(),
            *gpg_verification,
            *min_commit_age,
            *diff,
            builder.clone(),
            maintainer.clone(),
            *vcswatch,
            exclude.clone(),
            *autopkgtest_only,
            allowed_committer.clone(),
            cli.debug,
            *shuffle,
            verify_command.clone(),
            apt_repository.clone(),
            apt_repository_key.clone(),
        ),
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
