use clap::{Args, Parser, Subcommand};
use silver_platter::proposal::MergeProposalStatus;

#[derive(Parser)]
#[command(author, version, about, long_about = None)]
#[command(propagate_version = true)]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// List all forges
    Forges {},

    /// Login to a forge
    Login { url: url::Url },

    /// List merge proposals by the current user
    Proposals {
        // Status is one of "open", "merged" or "closed"
        #[arg(short, long, default_value = "open")]
        status: Option<MergeProposalStatus>,
    },

    /// Run a script to make a change, and publish (propose/push/etc) it
    Run {
        url: url::Url,

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
        commit_pending: silver_platter::CommitPending,

        /// Command to verify changes
        #[arg(long)]
        verify_command: Option<String>,

        /// Recipe to use
        #[arg(long)]
        recipe: Option<std::path::PathBuf>,

        /// File with candidate list
        #[arg(long)]
        candidates: Option<std::path::PathBuf>,
    },

    /// Apply a script to make a change in an existing local checkout
    Apply {
        /// Path to script to run
        command: Option<String>,

        /// Show diff of generated changes
        #[arg(long)]
        diff: bool,

        /// Command pending changes after script
        #[arg(long)]
        commit_pending: silver_platter::CommitPending,

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
        codebase: Option<String>,
    },
}

fn main() {
    let cli = Cli::parse();

    match &cli.command {
        Commands::Forges {} => {
            todo!();
        }
        Commands::Login { url } => {
            todo!();
        }
        Commands::Proposals { status } => {
            let statuses = status.as_ref().map(|status| vec![*status]);
            for (_forge, proposal) in silver_platter::proposal::iter_all_mps(statuses) {
                println!("{}", proposal.url().unwrap());
            }
        }
        Commands::Run {
            url,
            command,
            derived_owner,
            refresh,
            label,
            branch,
            diff,
            push,
            commit_pending,
            verify_command,
            recipe,
            candidates,
        } => {
            todo!();
        }
        Commands::Apply {
            command,
            diff,
            commit_pending,
            verify_command,
            recipe,
        } => {
            todo!();
        }
        Commands::Batch(args) => {
            todo!();
        }
    }
}
