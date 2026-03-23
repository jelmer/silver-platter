//! MCP (Model Context Protocol) server for silver-platter.
//!
//! Exposes silver-platter functionality as MCP tools over stdio.

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
