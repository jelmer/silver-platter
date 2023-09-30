use clap::{Args, Parser, Subcommand};

#[derive(Parser)]
#[command(author, version, about, long_about = None)]
#[command(propagate_version = true)]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    Forges {},
    Login { url: url::Url },
    Proposals {},
    Run {},
    Apply {},
    Batch {},
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
        Commands::Proposals {} => {
            todo!();
        }
        Commands::Run {} => {
            todo!();
        }
        Commands::Apply {} => {
            todo!();
        }
        Commands::Batch {} => {
            todo!();
        }
    }
}
