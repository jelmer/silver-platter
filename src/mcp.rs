//! MCP (Model Context Protocol) server for silver-platter.
//!
//! Exposes silver-platter functionality as MCP tools over stdio.

use breezyshim::tree::WorkingTree as _;
use rmcp::handler::server::router::tool::ToolRouter;
use rmcp::model::{ServerCapabilities, ServerInfo};
use rmcp::schemars;
use rmcp::tool;
use rmcp::{tool_handler, tool_router};

/// MCP server exposing silver-platter tools.
#[derive(Debug, Clone)]
pub struct SvpMcpServer {
    tool_router: ToolRouter<Self>,
}

#[derive(Debug, serde::Deserialize, schemars::JsonSchema)]
struct ListProposalsRequest {
    /// Status filter: "open", "merged", or "closed"
    #[schemars(description = "Filter proposals by status: open, merged, or closed")]
    status: Option<String>,
}

#[derive(Debug, serde::Deserialize, schemars::JsonSchema)]
struct GetProposalRequest {
    /// URL of the merge proposal
    #[schemars(description = "URL of the merge proposal to inspect")]
    url: String,
}

#[derive(Debug, serde::Deserialize, schemars::JsonSchema)]
struct BatchStatusRequest {
    /// Path to the batch directory
    #[schemars(description = "Path to the batch directory containing batch.yaml")]
    directory: String,

    /// Specific codebase entry to check
    #[schemars(description = "Name of a specific codebase entry to check status for")]
    codebase: Option<String>,
}

#[derive(Debug, serde::Deserialize, schemars::JsonSchema)]
struct ApplyRequest {
    /// Shell command to run in the checkout
    #[schemars(description = "Shell command to run in the working directory")]
    command: String,

    /// Path to the working directory (defaults to current directory)
    #[schemars(description = "Path to the working directory to apply changes in")]
    directory: Option<String>,

    /// Whether to commit pending changes: auto, yes, or no
    #[schemars(description = "Whether to commit pending changes: auto, yes, or no")]
    commit_pending: Option<String>,
}

#[derive(Debug, serde::Deserialize, schemars::JsonSchema)]
struct RunRequest {
    /// URL of the repository to modify
    #[schemars(description = "URL of the repository to modify")]
    url: String,

    /// Shell command to run in the checkout
    #[schemars(description = "Shell command to run in the repository checkout")]
    command: String,

    /// Branch name for the change
    #[schemars(description = "Branch name for the proposed change")]
    branch: Option<String>,

    /// Publish mode: push, propose, attempt-push, push-derived
    #[schemars(description = "Publish mode: push, propose, attempt-push, or push-derived")]
    mode: Option<String>,

    /// Whether to commit pending changes: auto, yes, or no
    #[schemars(description = "Whether to commit pending changes: auto, yes, or no")]
    commit_pending: Option<String>,
}

impl Default for SvpMcpServer {
    fn default() -> Self {
        Self::new()
    }
}

#[tool_router]
impl SvpMcpServer {
    /// Create a new MCP server instance.
    pub fn new() -> Self {
        Self {
            tool_router: Self::tool_router(),
        }
    }

    #[tool(
        description = "List all configured forges (code hosting platforms like GitHub, GitLab, Launchpad)"
    )]
    fn list_forges(&self) -> String {
        breezyshim::init();
        breezyshim::plugin::load_plugins();

        let mut result = String::new();
        for instance in breezyshim::forge::iter_forge_instances() {
            result.push_str(&format!(
                "{} ({})\n",
                instance.base_url(),
                instance.forge_kind()
            ));
        }
        if result.is_empty() {
            result.push_str("No forges configured.\n");
        }
        result
    }

    #[tool(description = "List merge proposals by the current user, optionally filtered by status")]
    fn list_proposals(
        &self,
        rmcp::handler::server::wrapper::Parameters(req): rmcp::handler::server::wrapper::Parameters<
            ListProposalsRequest,
        >,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        breezyshim::init();
        breezyshim::plugin::load_plugins();

        let statuses = if let Some(status_str) = &req.status {
            let status: crate::proposal::MergeProposalStatus =
                status_str.parse().map_err(|e: String| {
                    rmcp::ErrorData::new(rmcp::model::ErrorCode::INVALID_PARAMS, e, None)
                })?;
            Some(vec![status])
        } else {
            None
        };

        let mut lines = Vec::new();
        for (_forge, proposal) in crate::proposal::iter_all_mps(statuses) {
            if let Ok(url) = proposal.url() {
                lines.push(url.to_string());
            }
        }

        let text = if lines.is_empty() {
            "No merge proposals found.".to_string()
        } else {
            lines.join("\n")
        };

        Ok(rmcp::model::CallToolResult::success(vec![
            rmcp::model::Content::text(text),
        ]))
    }

    #[tool(
        description = "Run a codemod script on a repository and publish the changes as a merge proposal or push"
    )]
    fn run(
        &self,
        rmcp::handler::server::wrapper::Parameters(req): rmcp::handler::server::wrapper::Parameters<
            RunRequest,
        >,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        breezyshim::init();
        breezyshim::plugin::load_plugins();

        let url: url::Url = req.url.parse().map_err(|e: url::ParseError| {
            rmcp::ErrorData::new(rmcp::model::ErrorCode::INVALID_PARAMS, e.to_string(), None)
        })?;

        let command = shlex::split(&req.command).ok_or_else(|| {
            rmcp::ErrorData::new(
                rmcp::model::ErrorCode::INVALID_PARAMS,
                "Invalid shell command".to_string(),
                None,
            )
        })?;

        let branch = req.branch.unwrap_or_else(|| {
            crate::derived_branch_name(command.first().map(|s| s.as_str()).unwrap_or("change"))
                .to_string()
        });

        let mode: crate::Mode = if let Some(mode_str) = &req.mode {
            mode_str.parse().map_err(|e: String| {
                rmcp::ErrorData::new(rmcp::model::ErrorCode::INVALID_PARAMS, e, None)
            })?
        } else {
            crate::Mode::Propose
        };

        let commit_pending: crate::CommitPending = if let Some(cp_str) = &req.commit_pending {
            cp_str.parse().map_err(|e: String| {
                rmcp::ErrorData::new(rmcp::model::ErrorCode::INVALID_PARAMS, e, None)
            })?
        } else {
            crate::CommitPending::Auto
        };

        let get_description = |result: &crate::codemod::CommandResult,
                               _description_format,
                               _existing_proposal: Option<&breezyshim::forge::MergeProposal>|
         -> String {
            result
                .description
                .clone()
                .unwrap_or_else(|| "Automated change by silver-platter".to_string())
        };

        let retcode = crate::run::apply_and_publish(
            &url,
            &branch,
            &command.iter().map(|s| s.as_str()).collect::<Vec<_>>(),
            mode,
            commit_pending,
            None,
            false,
            None,
            None,
            false,
            None::<fn(&crate::codemod::CommandResult) -> bool>,
            None::<
                fn(
                    &crate::codemod::CommandResult,
                    Option<&breezyshim::forge::MergeProposal>,
                ) -> Option<String>,
            >,
            None::<
                fn(
                    &crate::codemod::CommandResult,
                    Option<&breezyshim::forge::MergeProposal>,
                ) -> Option<String>,
            >,
            get_description,
            None,
            false,
            None,
        );

        if retcode == 0 {
            Ok(rmcp::model::CallToolResult::success(vec![
                rmcp::model::Content::text("Changes applied and published successfully."),
            ]))
        } else {
            Ok(rmcp::model::CallToolResult::error(vec![
                rmcp::model::Content::text(format!("Command failed with exit code {}", retcode)),
            ]))
        }
    }

    #[tool(description = "Get details about a specific merge proposal by its URL")]
    fn get_proposal(
        &self,
        rmcp::handler::server::wrapper::Parameters(req): rmcp::handler::server::wrapper::Parameters<
            GetProposalRequest,
        >,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        breezyshim::init();
        breezyshim::plugin::load_plugins();

        let url: url::Url = req.url.parse().map_err(|e: url::ParseError| {
            rmcp::ErrorData::new(rmcp::model::ErrorCode::INVALID_PARAMS, e.to_string(), None)
        })?;

        let proposal = breezyshim::forge::get_proposal_by_url(&url).map_err(|e| {
            rmcp::ErrorData::new(
                rmcp::model::ErrorCode::INTERNAL_ERROR,
                format!("Failed to get proposal: {}", e),
                None,
            )
        })?;

        let mut info = Vec::new();
        info.push(format!("URL: {}", proposal.url().unwrap()));

        if let Ok(Some(title)) = proposal.get_title() {
            info.push(format!("Title: {}", title));
        }

        if let Ok(Some(description)) = proposal.get_description() {
            info.push(format!("Description: {}", description));
        }

        if let Ok(merged) = proposal.is_merged() {
            if merged {
                info.push("Status: Merged".to_string());
            } else if let Ok(closed) = proposal.is_closed() {
                if closed {
                    info.push("Status: Closed".to_string());
                } else {
                    info.push("Status: Open".to_string());
                }
            }
        }

        if let Ok(Some(source_url)) = proposal.get_source_branch_url() {
            info.push(format!("Source branch: {}", source_url));
        }

        if let Ok(Some(target_url)) = proposal.get_target_branch_url() {
            info.push(format!("Target branch: {}", target_url));
        }

        if let Ok(mergeable) = proposal.can_be_merged() {
            info.push(format!("Can be merged: {}", mergeable));
        }

        Ok(rmcp::model::CallToolResult::success(vec![
            rmcp::model::Content::text(info.join("\n")),
        ]))
    }

    #[tool(description = "Show status of a batch directory or specific entry within it")]
    fn batch_status(
        &self,
        rmcp::handler::server::wrapper::Parameters(req): rmcp::handler::server::wrapper::Parameters<
            BatchStatusRequest,
        >,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        breezyshim::init();
        breezyshim::plugin::load_plugins();

        let directory = std::path::Path::new(&req.directory)
            .canonicalize()
            .map_err(|e| {
                rmcp::ErrorData::new(
                    rmcp::model::ErrorCode::INVALID_PARAMS,
                    format!("Invalid directory: {}", e),
                    None,
                )
            })?;

        let batch = crate::batch::load_batch_metadata(&directory)
            .map_err(|e| {
                rmcp::ErrorData::new(
                    rmcp::model::ErrorCode::INTERNAL_ERROR,
                    format!("Failed to load batch metadata: {}", e),
                    None,
                )
            })?
            .ok_or_else(|| {
                rmcp::ErrorData::new(
                    rmcp::model::ErrorCode::INVALID_PARAMS,
                    format!("No batch.yaml found in {}", directory.display()),
                    None,
                )
            })?;

        let mut lines = Vec::new();
        lines.push(format!("Batch: {}", batch.name));
        lines.push(format!("Entries: {}", batch.work.len()));
        lines.push(String::new());

        if let Some(codebase) = &req.codebase {
            let entry = batch.work.get(codebase.as_str()).ok_or_else(|| {
                rmcp::ErrorData::new(
                    rmcp::model::ErrorCode::INVALID_PARAMS,
                    format!("Entry '{}' not found in batch", codebase),
                    None,
                )
            })?;
            lines.push(format!("{}: {}", codebase, entry.status()));
        } else {
            for (name, entry) in batch.work.iter() {
                lines.push(format!("{}: {}", name, entry.status()));
            }
        }

        Ok(rmcp::model::CallToolResult::success(vec![
            rmcp::model::Content::text(lines.join("\n")),
        ]))
    }

    #[tool(description = "Apply a codemod script to an existing local checkout without publishing")]
    fn apply(
        &self,
        rmcp::handler::server::wrapper::Parameters(req): rmcp::handler::server::wrapper::Parameters<
            ApplyRequest,
        >,
    ) -> Result<rmcp::model::CallToolResult, rmcp::ErrorData> {
        breezyshim::init();
        breezyshim::plugin::load_plugins();

        let directory = req
            .directory
            .as_deref()
            .map(std::path::Path::new)
            .unwrap_or_else(|| std::path::Path::new("."));

        let command = shlex::split(&req.command).ok_or_else(|| {
            rmcp::ErrorData::new(
                rmcp::model::ErrorCode::INVALID_PARAMS,
                "Invalid shell command".to_string(),
                None,
            )
        })?;

        let commit_pending: crate::CommitPending = if let Some(cp_str) = &req.commit_pending {
            cp_str.parse().map_err(|e: String| {
                rmcp::ErrorData::new(rmcp::model::ErrorCode::INVALID_PARAMS, e, None)
            })?
        } else {
            crate::CommitPending::Auto
        };

        let (local_tree, subpath) =
            breezyshim::workingtree::open_containing(directory).map_err(|e| {
                rmcp::ErrorData::new(
                    rmcp::model::ErrorCode::INTERNAL_ERROR,
                    format!("Failed to open working tree: {}", e),
                    None,
                )
            })?;

        breezyshim::workspace::check_clean_tree(
            &local_tree,
            &local_tree.basis_tree().unwrap(),
            subpath.as_path(),
        )
        .map_err(|e| {
            rmcp::ErrorData::new(
                rmcp::model::ErrorCode::INTERNAL_ERROR,
                format!("Working tree is not clean: {}", e),
                None,
            )
        })?;

        let result = crate::codemod::script_runner(
            &local_tree,
            &command.iter().map(|s| s.as_str()).collect::<Vec<_>>(),
            subpath.as_path(),
            commit_pending,
            None,
            None,
            None,
            std::process::Stdio::inherit(),
        )
        .map_err(|e| {
            breezyshim::workspace::reset_tree(&local_tree, None, Some(subpath.as_path())).ok();
            rmcp::ErrorData::new(
                rmcp::model::ErrorCode::INTERNAL_ERROR,
                format!("Script failed: {}", e),
                None,
            )
        })?;

        let mut info = Vec::new();
        if let Some(description) = &result.description {
            info.push(format!("Description: {}", description));
        }
        info.push(format!("Old revision: {}", result.old_revision));
        info.push(format!("New revision: {}", result.new_revision));

        Ok(rmcp::model::CallToolResult::success(vec![
            rmcp::model::Content::text(info.join("\n")),
        ]))
    }
}

#[tool_handler]
impl rmcp::handler::server::ServerHandler for SvpMcpServer {
    fn get_info(&self) -> ServerInfo {
        ServerInfo::new(
            ServerCapabilities::builder().enable_tools().build(),
        )
        .with_instructions("Silver-Platter: large-scale VCS change management. Automate contributing changes to source code repositories.".to_string())
    }
}

/// Run the MCP server over stdio.
pub async fn serve_stdio() -> Result<(), Box<dyn std::error::Error>> {
    use rmcp::ServiceExt;

    let server = SvpMcpServer::new();
    let service = server.serve(rmcp::transport::stdio()).await?;
    service.waiting().await?;
    Ok(())
}
