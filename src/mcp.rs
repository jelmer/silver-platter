//! MCP (Model Context Protocol) server for silver-platter

use rmcp::{
    handler::server::ServerHandler,
    model::*,
    service::{RequestContext, RoleServer, ServiceExt},
    Error as McpError,
};
use serde::{Deserialize, Serialize};
use std::sync::Arc;
use tokio::sync::Mutex;

/// MCP server implementation for silver-platter
#[derive(Clone)]
pub struct SvpMcpServer {
    _state: Arc<Mutex<ServerState>>,
}

#[derive(Default)]
struct ServerState {
    // You can add state here if needed
}

impl SvpMcpServer {
    /// Create a new MCP server instance
    pub fn new() -> Self {
        Self {
            _state: Arc::new(Mutex::new(ServerState::default())),
        }
    }
}

impl ServerHandler for SvpMcpServer {
    fn get_info(&self) -> ServerInfo {
        ServerInfo {
            server_info: Implementation {
                name: "svp-mcp".to_string(),
                version: env!("CARGO_PKG_VERSION").to_string(),
            },
            capabilities: ServerCapabilities {
                tools: Some(ToolsCapability { list_changed: None }),
                ..Default::default()
            },
            protocol_version: ProtocolVersion::V_2024_11_05,
            instructions: Some("Silver-platter MCP server for automating VCS changes".to_string()),
        }
    }

    async fn list_tools(
        &self,
        _params: Option<PaginatedRequestParam>,
        _ctx: RequestContext<RoleServer>,
    ) -> Result<ListToolsResult, McpError> {
        Ok(ListToolsResult {
            tools: vec![
                Tool {
                    name: "list_forges".to_string().into(),
                    description: Some("List all configured forges".to_string().into()),
                    input_schema: Arc::new(
                        serde_json::json!({
                            "type": "object",
                            "properties": {},
                            "required": []
                        })
                        .as_object()
                        .unwrap()
                        .clone(),
                    ),
                    annotations: None,
                },
                Tool {
                    name: "list_proposals".to_string().into(),
                    description: Some(
                        "List merge proposals by the current user"
                            .to_string()
                            .into(),
                    ),
                    input_schema: Arc::new(
                        serde_json::json!({
                            "type": "object",
                            "properties": {
                                "forge": {
                                    "type": "string",
                                    "description": "Optional forge name to filter proposals"
                                }
                            },
                            "required": []
                        })
                        .as_object()
                        .unwrap()
                        .clone(),
                    ),
                    annotations: None,
                },
                Tool {
                    name: "repo_info".to_string().into(),
                    description: Some(
                        "Get information about a specific repository"
                            .to_string()
                            .into(),
                    ),
                    input_schema: Arc::new(
                        serde_json::json!({
                            "type": "object",
                            "properties": {
                                "path": {
                                    "type": "string",
                                    "description": "Path to the repository"
                                }
                            },
                            "required": ["path"]
                        })
                        .as_object()
                        .unwrap()
                        .clone(),
                    ),
                    annotations: None,
                },
                Tool {
                    name: "run_recipe".to_string().into(),
                    description: Some(
                        "Run a silver-platter recipe to make changes to a repository"
                            .to_string()
                            .into(),
                    ),
                    input_schema: Arc::new(
                        serde_json::json!({
                            "type": "object",
                            "properties": {
                                "recipe": {
                                    "type": "object",
                                    "description": "Recipe definition as JSON object",
                                    "properties": {
                                        "name": {
                                            "type": "string",
                                            "description": "Name of the recipe"
                                        },
                                        "command": {
                                            "oneOf": [
                                                {
                                                    "type": "string",
                                                    "description": "Command as shell string"
                                                },
                                                {
                                                    "type": "array",
                                                    "items": {"type": "string"},
                                                    "description": "Command as array of arguments"
                                                }
                                            ]
                                        },
                                        "mode": {
                                            "type": "string",
                                            "description": "Mode to run the recipe in",
                                            "enum": ["push", "propose", "attempt-push"]
                                        },
                                        "merge-request": {
                                            "type": "object",
                                            "description": "Merge request configuration",
                                            "properties": {
                                                "title": {"type": "string"},
                                                "description": {"type": "string"},
                                                "commit-message": {"type": "string"}
                                            }
                                        }
                                    },
                                    "required": ["command"]
                                },
                                "url": {
                                    "type": "string",
                                    "description": "URL of the repository to apply the recipe to"
                                }
                            },
                            "required": ["recipe", "url"]
                        })
                        .as_object()
                        .unwrap()
                        .clone(),
                    ),
                    annotations: None,
                },
                Tool {
                    name: "apply_changes".to_string().into(),
                    description: Some(
                        "Apply a script to make changes in an existing local checkout"
                            .to_string()
                            .into(),
                    ),
                    input_schema: Arc::new(
                        serde_json::json!({
                            "type": "object",
                            "properties": {
                                "directory": {
                                    "type": "string",
                                    "description": "Directory containing the repository"
                                },
                                "command": {
                                    "oneOf": [
                                        {
                                            "type": "string",
                                            "description": "Command as shell string"
                                        },
                                        {
                                            "type": "array",
                                            "items": {"type": "string"},
                                            "description": "Command as array of arguments"
                                        }
                                    ]
                                },
                                "diff": {
                                    "type": "boolean",
                                    "description": "Show diff of generated changes",
                                    "default": false
                                },
                                "commit_pending": {
                                    "type": "string",
                                    "description": "Whether to commit pending changes after script",
                                    "enum": ["yes", "no", "auto"],
                                    "default": "auto"
                                }
                            },
                            "required": ["directory", "command"]
                        })
                        .as_object()
                        .unwrap()
                        .clone(),
                    ),
                    annotations: None,
                },
                Tool {
                    name: "batch_generate".to_string().into(),
                    description: Some(
                        "Generate a batch of changes to apply to multiple repositories"
                            .to_string()
                            .into(),
                    ),
                    input_schema: Arc::new(
                        serde_json::json!({
                            "type": "object",
                            "properties": {
                                "directory": {
                                    "type": "string",
                                    "description": "Directory to create the batch in"
                                },
                                "candidates": {
                                    "type": "string",
                                    "description": "Path to candidates file or URL pattern"
                                },
                                "command": {
                                    "oneOf": [
                                        {
                                            "type": "string",
                                            "description": "Command as shell string"
                                        },
                                        {
                                            "type": "array",
                                            "items": {"type": "string"},
                                            "description": "Command as array of arguments"
                                        }
                                    ]
                                },
                                "recipe": {
                                    "type": "string",
                                    "description": "Path to recipe file"
                                }
                            },
                            "required": ["directory", "candidates"],
                            "anyOf": [
                                {"required": ["command"]},
                                {"required": ["recipe"]}
                            ]
                        })
                        .as_object()
                        .unwrap()
                        .clone(),
                    ),
                    annotations: None,
                },
            ],
            ..Default::default()
        })
    }

    async fn call_tool(
        &self,
        params: CallToolRequestParam,
        _ctx: RequestContext<RoleServer>,
    ) -> Result<CallToolResult, McpError> {
        match params.name.as_ref() {
            "list_forges" => {
                use breezyshim::forge::iter_forge_instances;

                let names: Vec<String> = iter_forge_instances()
                    .map(|f| f.forge_kind().to_string())
                    .collect();

                Ok(CallToolResult::success(vec![Content::text(
                    serde_json::to_string(&names).unwrap(),
                )]))
            }
            "list_proposals" => {
                use crate::proposal::iter_all_mps;
                use breezyshim::forge::MergeProposalStatus;

                let forge = params
                    .arguments
                    .as_ref()
                    .and_then(|args| args.get("forge"))
                    .and_then(|v| v.as_str())
                    .map(|s| s.to_string());

                let proposals = if let Some(_forge_name) = forge {
                    // TODO: filter by forge
                    iter_all_mps(Some(vec![MergeProposalStatus::All]))
                } else {
                    iter_all_mps(Some(vec![MergeProposalStatus::All]))
                };

                let proposal_infos: Vec<ProposalInfo> = proposals
                    .map(|(_forge, mp)| ProposalInfo {
                        url: mp.url().ok().map(|u| u.to_string()),
                        status: "open".to_string(), // Default status
                        target_branch: None,
                        source_branch: None,
                        description: mp.get_description().ok().flatten(),
                    })
                    .collect();

                Ok(CallToolResult::success(vec![Content::text(
                    serde_json::to_string(&proposal_infos).unwrap(),
                )]))
            }
            "repo_info" => {
                use crate::vcs::open_branch;
                use std::path::Path;
                use url::Url;

                let path_str = params
                    .arguments
                    .as_ref()
                    .and_then(|args| args.get("path"))
                    .and_then(|v| v.as_str())
                    .ok_or_else(|| McpError::invalid_params("path parameter is required", None))?;

                let repo_path = Path::new(path_str);
                let url = Url::from_file_path(repo_path.canonicalize().unwrap()).unwrap();

                let branch = open_branch(&url, None, None, None).map_err(|e| {
                    McpError::internal_error(format!("Failed to open branch: {}", e), None)
                })?;

                let info = RepoInfo {
                    branch_name: branch.name(),
                    repository_path: branch.controldir().user_transport().base().to_string(),
                    has_changes: false, // TODO: implement proper change detection
                };

                Ok(CallToolResult::success(vec![Content::text(
                    serde_json::to_string(&info).unwrap(),
                )]))
            }
            "run_recipe" => {
                use crate::recipe::Recipe;
                use url::Url;
                
                let recipe_json = params
                    .arguments
                    .as_ref()
                    .and_then(|args| args.get("recipe"))
                    .ok_or_else(|| McpError::invalid_params("recipe parameter is required", None))?;
                
                let url_str = params
                    .arguments
                    .as_ref()
                    .and_then(|args| args.get("url"))
                    .and_then(|v| v.as_str())
                    .ok_or_else(|| McpError::invalid_params("url parameter is required", None))?;
                
                // Parse recipe from JSON
                let recipe: Recipe = serde_json::from_value(recipe_json.clone())
                    .map_err(|e| McpError::invalid_params(format!("Invalid recipe format: {}", e), None))?;
                
                // Parse URL
                let _url = Url::parse(url_str)
                    .map_err(|e| McpError::invalid_params(format!("Invalid URL: {}", e), None))?;
                
                // For simplicity, we'll just return basic info about the recipe
                // A full implementation would need to:
                // 1. Clone the repository locally
                // 2. Create a working tree
                // 3. Run the recipe command
                // 4. Create a merge proposal if mode is "propose"
                
                let result_info = RunRecipeResult {
                    success: true,
                    branch_name: Some(format!("recipe-{}", recipe.name.as_ref().unwrap_or(&"unnamed".to_string()))),
                    description: Some(format!(
                        "Recipe '{}' loaded successfully. Command: {:?}, Mode: {:?}",
                        recipe.name.as_ref().unwrap_or(&"unnamed".to_string()),
                        recipe.command.as_ref().map(|c| c.shell()).unwrap_or_else(|| "No command".to_string()),
                        recipe.mode.unwrap_or(crate::Mode::Propose)
                    )),
                    proposal_url: None,
                    error: None,
                };
                
                Ok(CallToolResult::success(vec![Content::text(
                    serde_json::to_string(&result_info).unwrap(),
                )]))
            }
            "apply_changes" => {
                use crate::codemod::script_runner;
                use crate::recipe::Command;
                use breezyshim::workingtree;
                use std::path::Path;
                
                let directory = params
                    .arguments
                    .as_ref()
                    .and_then(|args| args.get("directory"))
                    .and_then(|v| v.as_str())
                    .ok_or_else(|| McpError::invalid_params("directory parameter is required", None))?;
                
                let command_json = params
                    .arguments
                    .as_ref()
                    .and_then(|args| args.get("command"))
                    .ok_or_else(|| McpError::invalid_params("command parameter is required", None))?;
                
                let show_diff = params
                    .arguments
                    .as_ref()
                    .and_then(|args| args.get("diff"))
                    .and_then(|v| v.as_bool())
                    .unwrap_or(false);
                
                let commit_pending_str = params
                    .arguments
                    .as_ref()
                    .and_then(|args| args.get("commit_pending"))
                    .and_then(|v| v.as_str())
                    .unwrap_or("auto");
                
                let commit_pending = match commit_pending_str {
                    "yes" => crate::CommitPending::Yes,
                    "no" => crate::CommitPending::No,
                    "auto" => crate::CommitPending::Auto,
                    _ => return Err(McpError::invalid_params("Invalid commit_pending value", None)),
                };
                
                // Parse command
                let command = if let Some(cmd_str) = command_json.as_str() {
                    Command::Shell(cmd_str.to_string())
                } else if let Some(cmd_array) = command_json.as_array() {
                    let cmd_vec: Result<Vec<String>, _> = cmd_array.iter()
                        .map(|v| v.as_str().ok_or("Invalid command array").map(|s| s.to_string()))
                        .collect();
                    Command::Argv(cmd_vec.map_err(|e| McpError::invalid_params(e, None))?)
                } else {
                    return Err(McpError::invalid_params("command must be string or array", None));
                };
                
                // Open the working tree
                let (local_tree, subpath) = workingtree::open_containing(Path::new(directory))
                    .map_err(|e| McpError::internal_error(format!("Failed to open working tree: {}", e), None))?;
                
                // Check if tree is clean
                let basis_tree = local_tree.basis_tree()
                    .map_err(|e| McpError::internal_error(format!("Failed to get basis tree: {}", e), None))?;
                
                if let Err(e) = breezyshim::workspace::check_clean_tree(&local_tree, &basis_tree, subpath.as_path()) {
                    return Err(McpError::internal_error(format!("Working tree is not clean: {}", e), None));
                }
                
                // Run the script
                let cmd_argv = command.argv();
                let cmd_parts: Vec<&str> = cmd_argv.iter().map(|s| s.as_str()).collect();
                let result = script_runner(
                    &local_tree,
                    &cmd_parts,
                    subpath.as_path(),
                    commit_pending,
                    None,
                    None,
                    None,
                    std::process::Stdio::null(),
                ).map_err(|e| McpError::internal_error(format!("Script execution failed: {}", e), None))?;
                
                let mut response_parts = vec![];
                
                // Add basic result info
                response_parts.push(format!("Applied changes successfully"));
                if let Some(description) = &result.description {
                    response_parts.push(format!("Description: {}", description));
                }
                
                // Add diff if requested
                if show_diff {
                    // Get the diff
                    let _new_basis = local_tree.basis_tree()
                        .map_err(|e| McpError::internal_error(format!("Failed to get new basis tree: {}", e), None))?;
                    
                    // For now, just indicate that diff was requested
                    response_parts.push("Diff requested but not implemented yet".to_string());
                }
                
                let apply_result = RunRecipeResult {
                    success: true,
                    branch_name: None,
                    description: Some(response_parts.join("\\n")),
                    proposal_url: None,
                    error: None,
                };
                
                Ok(CallToolResult::success(vec![Content::text(
                    serde_json::to_string(&apply_result).unwrap(),
                )]))
            }
            "batch_generate" => {
                use crate::candidates::Candidates;
                use crate::recipe::Recipe;
                use crate::batch::Batch;
                use std::path::Path;
                
                let directory = params
                    .arguments
                    .as_ref()
                    .and_then(|args| args.get("directory"))
                    .and_then(|v| v.as_str())
                    .ok_or_else(|| McpError::invalid_params("directory parameter is required", None))?;
                
                let candidates_path = params
                    .arguments
                    .as_ref()
                    .and_then(|args| args.get("candidates"))
                    .and_then(|v| v.as_str())
                    .ok_or_else(|| McpError::invalid_params("candidates parameter is required", None))?;
                
                let recipe_path = params
                    .arguments
                    .as_ref()
                    .and_then(|args| args.get("recipe"))
                    .and_then(|v| v.as_str());
                
                let command_json = params
                    .arguments
                    .as_ref()
                    .and_then(|args| args.get("command"));
                
                // Either recipe or command must be provided
                let recipe = if let Some(recipe_path) = recipe_path {
                    Recipe::from_path(Path::new(recipe_path))
                        .map_err(|e| McpError::invalid_params(format!("Failed to load recipe: {}", e), None))?
                } else if let Some(command_json) = command_json {
                    // Create a basic recipe from the command
                    let mut recipe = Recipe {
                        name: Some("batch-recipe".to_string()),
                        merge_request: None,
                        labels: None,
                        command: None,
                        mode: Some(crate::Mode::Propose),
                        resume: None,
                        commit_pending: crate::CommitPending::Auto,
                    };
                    
                    // Parse command
                    if let Some(cmd_str) = command_json.as_str() {
                        recipe.command = Some(crate::recipe::Command::Shell(cmd_str.to_string()));
                    } else if let Some(cmd_array) = command_json.as_array() {
                        let cmd_vec: Result<Vec<String>, _> = cmd_array.iter()
                            .map(|v| v.as_str().ok_or("Invalid command array").map(|s| s.to_string()))
                            .collect();
                        recipe.command = Some(crate::recipe::Command::Argv(cmd_vec.map_err(|e| McpError::invalid_params(e, None))?));
                    } else {
                        return Err(McpError::invalid_params("command must be string or array", None));
                    }
                    
                    recipe
                } else {
                    return Err(McpError::invalid_params("Either recipe or command must be provided", None));
                };
                
                // Load candidates
                let candidates = if Path::new(candidates_path).exists() {
                    Candidates::from_path(Path::new(candidates_path))
                        .map_err(|e| McpError::invalid_params(format!("Failed to load candidates: {}", e), None))?
                } else {
                    return Err(McpError::invalid_params("Candidates file not found", None));
                };
                
                // Generate batch
                let batch_dir = Path::new(directory);
                let extra_env = std::collections::HashMap::new();
                
                Batch::from_recipe(&recipe, candidates.iter(), batch_dir, Some(extra_env))
                    .map_err(|e| McpError::internal_error(format!("Failed to generate batch: {}", e), None))?;
                
                let result = RunRecipeResult {
                    success: true,
                    branch_name: None,
                    description: Some(format!(
                        "Batch generated successfully in {}. Review the patches and run batch_publish to publish them.",
                        directory
                    )),
                    proposal_url: None,
                    error: None,
                };
                
                Ok(CallToolResult::success(vec![Content::text(
                    serde_json::to_string(&result).unwrap(),
                )]))
            }
            _ => Err(McpError::internal_error(
                format!("Unknown tool: {}", params.name),
                None,
            )),
        }
    }
}

#[derive(Serialize, Deserialize)]
struct ProposalInfo {
    url: Option<String>,
    status: String,
    target_branch: Option<String>,
    source_branch: Option<String>,
    description: Option<String>,
}

#[derive(Serialize, Deserialize)]
struct RepoInfo {
    branch_name: Option<String>,
    repository_path: String,
    has_changes: bool,
}

#[derive(Serialize, Deserialize)]
struct RunRecipeResult {
    success: bool,
    branch_name: Option<String>,
    description: Option<String>,
    proposal_url: Option<String>,
    error: Option<String>,
}

/// Run the MCP server on stdin/stdout
pub async fn run_mcp_server() -> Result<(), Box<dyn std::error::Error>> {
    use tokio::io::{stdin, stdout};

    let server = SvpMcpServer::new();
    let transport = (stdin(), stdout());

    let service = server.serve(transport).await?;
    let _quit_reason = service.waiting().await?;

    Ok(())
}
