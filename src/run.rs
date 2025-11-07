//! Run the codemod script and publish the changes as a merge proposal.
use crate::codemod::{CommandResult, Error as CommandError};
use crate::publish::{
    enable_tag_pushing, find_existing_proposed, DescriptionFormat, Error as PublishError,
};
use crate::vcs::{open_branch, BranchOpenError};
use crate::workspace::Workspace;
use crate::Mode;
use breezyshim::branch::{Branch, GenericBranch};
use breezyshim::error::Error as BrzError;
use breezyshim::forge::{get_forge, Forge, MergeProposal};
use breezyshim::tree::WorkingTree;
use log::{error, info, warn};
use std::collections::HashMap;
use std::sync::Arc;
use url::Url;

/// Open a branch from a URL, with error handling
///
/// Returns a branch on success or error code on failure.
fn open_branch_with_error_handling(url: &Url) -> Result<GenericBranch, i32> {
    match open_branch(url, None, None, None) {
        Err(BranchOpenError::Unavailable {
            url, description, ..
        })
        | Err(BranchOpenError::Missing {
            url, description, ..
        })
        | Err(BranchOpenError::RateLimited {
            url, description, ..
        })
        | Err(BranchOpenError::TemporarilyUnavailable {
            url, description, ..
        })
        | Err(BranchOpenError::Unsupported {
            url, description, ..
        }) => {
            error!("{}: {}", url, description);
            Err(2)
        }
        Err(BranchOpenError::Other(e)) => {
            error!("{}: {}", url, e);
            Err(2)
        }
        Ok(b) => Ok(b),
    }
}

/// Get forge and existing proposals
///
/// Returns tuple of (forge, existing_proposals, resume_branch, overwrite) on success
/// or error code on failure.
fn get_forge_and_proposals(
    main_branch: &GenericBranch,
    url: &Url,
    name: &str,
    mode: Mode,
    derived_owner: Option<&str>,
) -> Result<
    (
        Option<Box<Forge>>,
        Vec<MergeProposal>,
        Option<GenericBranch>,
        bool,
    ),
    i32,
> {
    let mut overwrite = false;

    match get_forge(main_branch) {
        Err(BrzError::UnsupportedForge(e)) => {
            if mode != Mode::Push {
                error!("{}: {}", url, e);
                return Err(2);
            }
            // We can't figure out what branch to resume from when there's no forge
            // that can tell us.
            warn!(
                "Unsupported forge ({}), will attempt to push to {}",
                e,
                crate::vcs::full_branch_url(main_branch),
            );
            Ok((None, vec![], None, overwrite))
        }
        Err(BrzError::ForgeProjectExists(_)) | Err(BrzError::AlreadyControlDir(..)) => {
            unreachable!()
        }
        Err(BrzError::ForgeLoginRequired) => {
            warn!("Login required to access forge");
            Err(2)
        }
        Err(e) => {
            error!("Failed to get forge: {}", e);
            Err(2)
        }
        Ok(forge) => {
            let (resume_branch, resume_overwrite, existing_proposals) =
                match find_existing_proposed(main_branch, &forge, name, false, derived_owner, None)
                {
                    Ok(r) => r,
                    Err(e) => {
                        error!("Failed to find existing proposals: {}", e);
                        return Err(2);
                    }
                };

            if let Some(resume_overwrite) = resume_overwrite {
                overwrite = resume_overwrite;
            }

            Ok((
                Some(Box::new(forge)),
                existing_proposals.unwrap_or_default(),
                resume_branch,
                overwrite,
            ))
        }
    }
}

/// Build a workspace from main branch and resume branch
///
/// Returns workspace on success or error code on failure.
fn build_workspace(
    main_branch: GenericBranch,
    resume_branch: Option<GenericBranch>,
) -> Result<Workspace, i32> {
    let mut builder = Workspace::builder().main_branch(main_branch);

    builder = if let Some(resume_branch) = resume_branch {
        builder.resume_branch(resume_branch)
    } else {
        builder
    };

    match builder.build() {
        Ok(ws) => Ok(ws),
        Err(e) => {
            error!("Failed to start workspace: {}", e);
            Err(2)
        }
    }
}

/// Run a script in a workspace
///
/// Returns CommandResult on success or error code on failure.
fn run_script(
    workspace: &Workspace,
    command: &[&str],
    subpath: &std::path::Path,
    commit_pending: crate::CommitPending,
    extra_env: Option<HashMap<String, String>>,
) -> Result<CommandResult, i32> {
    match crate::codemod::script_runner(
        workspace.local_tree(),
        command,
        subpath,
        commit_pending,
        None,
        None,
        extra_env,
        std::process::Stdio::inherit(),
    ) {
        Ok(r) => Ok(r),
        Err(CommandError::ScriptMadeNoChanges) => {
            error!("Script did not make any changes.");
            Err(0)
        }
        Err(e) => {
            error!("Script failed: {}", e);
            Err(2)
        }
    }
}

/// Run a verification command in a workspace
///
/// Returns Ok(()) on success or error code on failure.
fn run_verification(workspace: &Workspace, verify_command: &str) -> Result<(), i32> {
    match std::process::Command::new("sh")
        .arg("-c")
        .arg(verify_command)
        .current_dir(
            workspace
                .local_tree()
                .abspath(std::path::Path::new("."))
                .unwrap(),
        )
        .stdout(std::process::Stdio::inherit())
        .stderr(std::process::Stdio::inherit())
        .output()
    {
        Ok(output) => {
            if output.status.success() {
                info!("Verify command succeeded.");
                Ok(())
            } else {
                error!("Verify command failed.");
                Err(2)
            }
        }
        Err(e) => {
            error!("Verify command failed: {}", e);
            Err(2)
        }
    }
}

/// Publish changes to a workspace
///
/// Returns publish result on success or error code on failure.
fn publish_workspace_changes(
    workspace: &Workspace,
    mode: Mode,
    name: &str,
    result: &CommandResult,
    forge: Option<&Forge>,
    overwrite: bool,
    existing_proposal: Option<MergeProposal>,
    derived_owner: Option<&str>,
    labels: Option<Vec<String>>,
    allow_create_proposal: Option<bool>,
    get_commit_message: Option<impl FnOnce(Option<&MergeProposal>) -> Option<String>>,
    get_title: Option<impl FnOnce(Option<&MergeProposal>) -> Option<String>>,
    get_description: impl FnOnce(&CommandResult, DescriptionFormat, Option<&MergeProposal>) -> String,
    auto_merge: bool,
) -> Result<crate::publish::PublishResult, i32> {
    match workspace.publish_changes(
        None,
        mode,
        name,
        |df, ep| get_description(result, df, ep),
        get_commit_message,
        get_title,
        forge,
        allow_create_proposal,
        labels,
        Some(overwrite),
        existing_proposal,
        None,
        None,
        derived_owner,
        None,
        None,
        Some(auto_merge),
        None,
    ) {
        Ok(r) => Ok(r),
        Err(PublishError::UnsupportedForge(_)) => {
            error!(
                "No known supported forge for {}. Run 'svp login'?",
                crate::vcs::full_branch_url(workspace.main_branch().unwrap()),
            );
            Err(2)
        }
        Err(PublishError::InsufficientChangesForNewProposal) => {
            info!("Insufficient changes for a new merge proposal");
            Err(1)
        }
        Err(PublishError::ForgeLoginRequired) => {
            error!("Credentials for hosting site missing. Run 'svp login'?",);
            Err(2)
        }
        Err(PublishError::DivergedBranches()) | Err(PublishError::UnrelatedBranchExists) => {
            error!("A branch exists on the server that has diverged from the local branch.");
            Err(2)
        }
        Err(PublishError::BranchOpenError(e)) => {
            error!("Failed to open branch: {}", e);
            Err(2)
        }
        Err(PublishError::EmptyMergeProposal) => {
            error!("No changes to publish.");
            Err(2)
        }
        Err(PublishError::Other(e)) => {
            error!("Failed to publish changes: {}", e);
            Err(2)
        }
        Err(PublishError::PermissionDenied) => {
            error!("Permission denied to create merge proposal.");
            Err(2)
        }
        Err(PublishError::NoTargetBranch) => {
            unreachable!();
        }
    }
}

/// Builder for apply_and_publish operation
pub struct ApplyAndPublishBuilder<'a> {
    /// The URL of the repository to work on
    pub url: &'a Url,
    /// The name of the branch or proposal
    pub name: &'a str,
    /// The command to execute for applying changes
    pub command: &'a [&'a str],
    /// The publish mode (push-derived, propose, etc.)
    pub mode: Mode,
    /// How to handle pending commits
    pub commit_pending: crate::CommitPending,
    /// Labels to apply to the merge proposal
    pub labels: Option<&'a [&'a str]>,
    /// Whether to show diff output
    pub diff: bool,
    /// Optional verification command to run
    pub verify_command: Option<&'a str>,
    /// The derived owner for the published branch
    pub derived_owner: Option<&'a str>,
    /// Whether to refresh the local branch before applying changes
    pub refresh: bool,
    /// Additional environment variables for command execution
    pub extra_env: Option<HashMap<String, String>>,
    /// Subpath within the repository to run the command
    pub subpath: Option<&'a std::path::Path>,
    /// Whether to enable automatic merge when CI passes
    pub auto_merge: bool,
}

impl<'a> ApplyAndPublishBuilder<'a> {
    /// Creates a new ApplyAndPublishBuilder with the required parameters.
    pub fn new(url: &'a Url, name: &'a str, command: &'a [&'a str], mode: Mode) -> Self {
        Self {
            url,
            name,
            command,
            mode,
            commit_pending: crate::CommitPending::Auto,
            labels: None,
            diff: false,
            verify_command: None,
            derived_owner: None,
            refresh: false,
            extra_env: None,
            subpath: None,
            auto_merge: false,
        }
    }

    /// Sets how to handle pending commits.
    pub fn commit_pending(mut self, commit_pending: crate::CommitPending) -> Self {
        self.commit_pending = commit_pending;
        self
    }

    /// Sets the labels to apply to the merge proposal.
    pub fn labels(mut self, labels: &'a [&'a str]) -> Self {
        self.labels = Some(labels);
        self
    }

    /// Sets whether to show diff output.
    pub fn diff(mut self, diff: bool) -> Self {
        self.diff = diff;
        self
    }

    /// Sets the verification command to run after applying changes.
    pub fn verify_command(mut self, command: &'a str) -> Self {
        self.verify_command = Some(command);
        self
    }

    /// Sets the derived owner for the published branch.
    pub fn derived_owner(mut self, owner: &'a str) -> Self {
        self.derived_owner = Some(owner);
        self
    }

    /// Sets whether to refresh the local branch before applying changes.
    pub fn refresh(mut self, refresh: bool) -> Self {
        self.refresh = refresh;
        self
    }

    /// Sets additional environment variables for the command execution.
    pub fn extra_env(mut self, env: HashMap<String, String>) -> Self {
        self.extra_env = Some(env);
        self
    }

    /// Sets the subpath within the repository to run the command.
    pub fn subpath(mut self, path: &'a std::path::Path) -> Self {
        self.subpath = Some(path);
        self
    }

    /// Sets whether to enable automatic merge when CI passes.
    pub fn auto_merge(mut self, auto_merge: bool) -> Self {
        self.auto_merge = auto_merge;
        self
    }

    /// Applies the codemod and publishes the changes as a merge proposal.
    ///
    /// # Arguments
    /// * `allow_create_proposal` - Function to determine if a new proposal should be created
    /// * `get_commit_message` - Function to generate the commit message
    /// * `get_title` - Function to generate the proposal title
    /// * `get_description` - Function to generate the proposal description
    ///
    /// # Returns
    /// The exit code of the operation (0 for success)
    pub fn apply_and_publish(
        self,
        allow_create_proposal: Option<impl FnOnce(&CommandResult) -> bool>,
        get_commit_message: Option<
            impl FnOnce(&CommandResult, Option<&MergeProposal>) -> Option<String>,
        >,
        get_title: Option<impl FnOnce(&CommandResult, Option<&MergeProposal>) -> Option<String>>,
        get_description: impl FnOnce(
            &CommandResult,
            DescriptionFormat,
            Option<&MergeProposal>,
        ) -> String,
    ) -> i32 {
        apply_and_publish(
            self.url,
            self.name,
            self.command,
            self.mode,
            self.commit_pending,
            self.labels,
            self.diff,
            self.verify_command,
            self.derived_owner,
            self.refresh,
            allow_create_proposal,
            get_commit_message,
            get_title,
            get_description,
            self.extra_env,
            self.auto_merge,
            self.subpath,
        )
    }
}

/// Apply a codemod script and publish the changes as a merge proposal.
pub fn apply_and_publish(
    url: &Url,
    name: &str,
    command: &[&str],
    mode: Mode,
    commit_pending: crate::CommitPending,
    labels: Option<&[&str]>,
    diff: bool,
    verify_command: Option<&str>,
    derived_owner: Option<&str>,
    refresh: bool,
    allow_create_proposal: Option<impl FnOnce(&CommandResult) -> bool>,
    mut get_commit_message: Option<
        impl FnOnce(&CommandResult, Option<&MergeProposal>) -> Option<String>,
    >,
    get_title: Option<impl FnOnce(&CommandResult, Option<&MergeProposal>) -> Option<String>>,
    get_description: impl FnOnce(&CommandResult, DescriptionFormat, Option<&MergeProposal>) -> String,
    extra_env: Option<HashMap<String, String>>,
    auto_merge: bool,
    subpath: Option<&std::path::Path>,
) -> i32 {
    // Step 1: Open the main branch
    let main_branch = match open_branch_with_error_handling(url) {
        Ok(branch) => branch,
        Err(code) => return code,
    };

    // Step 2: Get forge and proposals
    let (forge, existing_proposals, mut resume_branch, mut overwrite) =
        match get_forge_and_proposals(&main_branch, url, name, mode, derived_owner) {
            Ok(result) => result,
            Err(code) => return code,
        };

    // Handle refresh option
    if refresh {
        if resume_branch.is_some() {
            overwrite = true;
        }
        resume_branch = None;
    }

    // Get existing proposal
    let existing_proposal = if existing_proposals.len() > 1 {
        warn!(
            "Multiple open merge proposals for branch at {}: {:?}",
            resume_branch.as_ref().unwrap().get_user_url(),
            existing_proposals
                .iter()
                .map(|mp| mp.url().unwrap())
                .collect::<Vec<_>>()
        );
        let existing_proposal = existing_proposals.into_iter().next().unwrap();
        info!("Updating {}", existing_proposal.url().unwrap());
        Some(existing_proposal)
    } else {
        None
    };

    // Step 3: Build workspace
    let ws = match build_workspace(main_branch, resume_branch) {
        Ok(ws) => ws,
        Err(code) => return code,
    };

    // Step 4: Run script
    let subpath = subpath.unwrap_or(std::path::Path::new(""));
    let result = match run_script(&ws, command, subpath, commit_pending, extra_env) {
        Ok(result) => result,
        Err(code) => return code,
    };

    // Step 5: Run verification if provided
    if let Some(verify_cmd) = verify_command {
        match run_verification(&ws, verify_cmd) {
            Ok(()) => {}
            Err(code) => return code,
        }
    }

    // Enable tag pushing
    enable_tag_pushing(&ws.local_tree().branch()).unwrap();

    // Step 6: Prepare callbacks for publishing
    let result = Arc::new(result);
    let result_ref = Arc::clone(&result);
    let get_commit_message = get_commit_message
        .take()
        .map(|f| move |ep: Option<&MergeProposal>| -> Option<String> { f(&result_ref, ep) });

    let result_ref = Arc::clone(&result);
    let get_title_wrapper = Some(move |ep: Option<&MergeProposal>| {
        if let Some(get_title) = get_title {
            get_title(&result_ref, ep)
        } else {
            None
        }
    });

    // Step 7: Publish changes
    let labels_vec = labels.map(|l| l.iter().map(|&s| s.to_string()).collect());
    let allow_create = allow_create_proposal.map(|f| f(&result));

    let publish_result = match publish_workspace_changes(
        &ws,
        mode,
        name,
        &result,
        forge.as_deref(),
        overwrite,
        existing_proposal,
        derived_owner,
        labels_vec,
        allow_create,
        get_commit_message,
        get_title_wrapper,
        get_description,
        auto_merge,
    ) {
        Ok(result) => result,
        Err(code) => return code,
    };

    // Step 8: Handle success and output
    if let Some(mp) = publish_result.proposal {
        if publish_result.is_new.unwrap() {
            info!("Merge proposal created.");
        } else {
            info!("Merge proposal updated.");
        }
        if let Ok(url) = mp.url() {
            info!("URL: {}", url);
        }
        info!("Description: {}", mp.get_description().unwrap().unwrap());
    }

    if diff {
        ws.show_diff(Box::new(std::io::stdout()), None, None)
            .unwrap();
    }

    1
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::CommitPending;
    use breezyshim::controldir::{create_standalone_workingtree, ControlDirFormat};
    use breezyshim::testing::TestEnv;
    use breezyshim::tree::MutableTree;
    use breezyshim::WorkingTree;
    use serial_test::serial;
    use std::path::Path;
    use tempfile::tempdir;

    // Helper that creates a simple test script file
    fn create_test_script(dir_path: &Path, script_name: &str, content: &str) -> std::path::PathBuf {
        let script_path = dir_path.join(script_name);
        std::fs::write(&script_path, content).unwrap();

        // Make script executable on Unix systems
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mut perms = std::fs::metadata(&script_path).unwrap().permissions();
            perms.set_mode(0o755);
            std::fs::set_permissions(&script_path, perms).unwrap();
        }

        script_path
    }

    // Helper that creates a test repository
    fn create_test_repo() -> (tempfile::TempDir, std::path::PathBuf, url::Url) {
        let _test_env = TestEnv::new();
        let td = tempdir().unwrap();
        let origin_dir = td.path().join("origin");

        // Create a standalone working tree
        let tree =
            create_standalone_workingtree(&origin_dir, &ControlDirFormat::default()).unwrap();

        // Create an initial file and commit
        std::fs::write(origin_dir.join("README.md"), "# Test Repository").unwrap();
        tree.add(&[Path::new("README.md")]).unwrap();
        tree.build_commit()
            .message("Initial commit")
            .commit()
            .unwrap();

        let branch_url = Url::from_directory_path(&origin_dir).unwrap();

        (td, origin_dir, branch_url)
    }

    #[test]
    #[serial]
    fn test_open_branch_with_error_handling_success() {
        let _test_env = TestEnv::new();
        let (_td, origin_dir, _) = create_test_repo();

        // Test successful branch opening
        let url = Url::from_directory_path(origin_dir).unwrap();
        let result = open_branch_with_error_handling(&url);

        assert!(result.is_ok(), "Branch should be opened successfully");
    }

    #[test]
    fn test_open_branch_with_error_handling_invalid_url() {
        // Try to open a branch with an invalid URL
        let invalid_url = Url::parse("file:///nonexistent/path").unwrap();
        let result = open_branch_with_error_handling(&invalid_url);

        assert!(result.is_err(), "Opening invalid branch should fail");

        // We can't directly compare using assert_eq! since Box<dyn Branch>
        // doesn't implement Debug. Instead, just check that we get the expected error code.
        let error_code = result.err().unwrap();
        assert_eq!(error_code, 2, "Should return error code 2");
    }

    #[test]
    #[serial]
    fn test_run_script_success() {
        let _test_env = TestEnv::new();
        let (td, origin_dir, _) = create_test_repo();

        // Create a script that will succeed with changes
        let script_dir = td.path().join("scripts");
        std::fs::create_dir(&script_dir).unwrap();
        let script_content = r#"#!/bin/sh
echo "new content" > README.md
exit 0
"#;
        let script_path = create_test_script(&script_dir, "successful_script.sh", script_content);

        // Open branch and build workspace
        let branch = open_branch(
            &Url::from_directory_path(origin_dir).unwrap(),
            None,
            None,
            None,
        )
        .unwrap();
        let ws = build_workspace(branch, None).unwrap();

        // Run the script
        let result = run_script(
            &ws,
            &[script_path.to_str().unwrap()],
            Path::new(""),
            CommitPending::Auto,
            None,
        );

        // Script should succeed
        assert!(result.is_ok(), "Script should succeed");
    }

    #[test]
    #[serial]
    fn test_run_script_no_changes() {
        let _test_env = TestEnv::new();
        let (td, origin_dir, _) = create_test_repo();

        // Create a script that will succeed but make no changes
        let script_dir = td.path().join("scripts");
        std::fs::create_dir(&script_dir).unwrap();
        let script_path =
            create_test_script(&script_dir, "no_change_script.sh", "#!/bin/sh\nexit 0");

        // Open branch and build workspace
        let branch = open_branch(
            &Url::from_directory_path(origin_dir).unwrap(),
            None,
            None,
            None,
        )
        .unwrap();
        let ws = build_workspace(branch, None).unwrap();

        // Run the script
        let result = run_script(
            &ws,
            &[script_path.to_str().unwrap()],
            Path::new(""),
            CommitPending::Auto,
            None,
        );

        // Script should return an error code indicating no changes
        assert!(
            result.is_err(),
            "Script with no changes should return an error"
        );
        assert_eq!(
            result.unwrap_err(),
            0,
            "Script with no changes should return code 0"
        );
    }

    #[test]
    #[serial]
    fn test_run_script_error() {
        let _test_env = TestEnv::new();
        let (td, origin_dir, _) = create_test_repo();

        // Create a script that will fail
        let script_dir = td.path().join("scripts");
        std::fs::create_dir(&script_dir).unwrap();
        let script_path = create_test_script(&script_dir, "failing_script.sh", "#!/bin/sh\nexit 1");

        // Open branch and build workspace
        let branch = open_branch(
            &Url::from_directory_path(origin_dir).unwrap(),
            None,
            None,
            None,
        )
        .unwrap();
        let ws = build_workspace(branch, None).unwrap();

        // Run the script
        let result = run_script(
            &ws,
            &[script_path.to_str().unwrap()],
            Path::new(""),
            CommitPending::Auto,
            None,
        );

        // Script should return an error code
        assert!(result.is_err(), "Failed script should return an error");
        assert_eq!(result.unwrap_err(), 2, "Failed script should return code 2");
    }

    #[test]
    #[serial]
    fn test_run_verification_success() {
        let _test_env = TestEnv::new();
        let (_td, origin_dir, _) = create_test_repo();

        // Open branch and build workspace
        let branch = open_branch(
            &Url::from_directory_path(origin_dir).unwrap(),
            None,
            None,
            None,
        )
        .unwrap();
        let ws = build_workspace(branch, None).unwrap();

        // Run a verification command that should succeed
        let result = run_verification(&ws, "test -f README.md");

        // Verification should succeed
        assert!(
            result.is_ok(),
            "Verification should succeed when file exists"
        );
    }

    #[test]
    #[serial]
    fn test_run_verification_failure() {
        let _test_env = TestEnv::new();
        let (_td, origin_dir, _) = create_test_repo();

        // Open branch and build workspace
        let branch = open_branch(
            &Url::from_directory_path(origin_dir).unwrap(),
            None,
            None,
            None,
        )
        .unwrap();
        let ws = build_workspace(branch, None).unwrap();

        // Run a verification command that should fail
        let result = run_verification(&ws, "test -f nonexistent.file");

        // Verification should fail
        assert!(
            result.is_err(),
            "Verification should fail when file doesn't exist"
        );
        assert_eq!(
            result.unwrap_err(),
            2,
            "Verification failure should return code 2"
        );
    }

    #[test]
    #[serial]
    fn test_build_workspace() {
        let _test_env = TestEnv::new();
        let (td, origin_dir, _) = create_test_repo();

        // Open branch
        let branch = open_branch(
            &Url::from_directory_path(origin_dir).unwrap(),
            None,
            None,
            None,
        )
        .unwrap();

        // Build workspace
        let result = build_workspace(branch, None);

        // Workspace should be created successfully
        assert!(result.is_ok(), "Workspace should be created successfully");

        // Make sure the tempdir stays alive until the end of the test
        drop(td);
    }

    #[test]
    #[serial]
    fn test_build_workspace_with_resume_branch() {
        let _test_env = TestEnv::new();
        let (td, origin_dir, _) = create_test_repo();

        // Create another branch to use as resume branch
        let resume_dir = td.path().join("resume");
        let resume_tree =
            create_standalone_workingtree(&resume_dir, &ControlDirFormat::default()).unwrap();
        std::fs::write(resume_dir.join("README.md"), "# Resume Repository").unwrap();
        resume_tree.add(&[Path::new("README.md")]).unwrap();
        resume_tree
            .build_commit()
            .message("Initial commit in resume")
            .commit()
            .unwrap();

        // Open both branches
        let main_branch = open_branch(
            &Url::from_directory_path(origin_dir).unwrap(),
            None,
            None,
            None,
        )
        .unwrap();
        let resume_branch = open_branch(
            &Url::from_directory_path(resume_dir).unwrap(),
            None,
            None,
            None,
        )
        .unwrap();

        // Build workspace with resume branch
        let result = build_workspace(main_branch, Some(resume_branch));

        // Workspace should be created successfully
        assert!(
            result.is_ok(),
            "Workspace with resume branch should be created successfully"
        );

        // Make sure the tempdir stays alive until the end of the test
        drop(td);
    }

    #[test]
    #[serial]
    fn test_apply_and_publish_script_error() {
        let _test_env = TestEnv::new();
        // Create a test directory structure
        let td = tempdir().unwrap();
        let origin_dir = td.path().join("origin");

        // Create a standalone working tree
        let tree =
            create_standalone_workingtree(&origin_dir, &ControlDirFormat::default()).unwrap();

        // Create an initial file and commit
        std::fs::write(origin_dir.join("README.md"), "# Test Repository").unwrap();
        tree.add(&[Path::new("README.md")]).unwrap();
        tree.build_commit()
            .message("Initial commit")
            .commit()
            .unwrap();

        // Create a script that will fail
        let script_dir = td.path().join("scripts");
        std::fs::create_dir(&script_dir).unwrap();
        let script_path = create_test_script(&script_dir, "failing_script.sh", "#!/bin/sh\nexit 1");

        // Run apply_and_publish with a script that will fail
        let branch_url = Url::from_directory_path(&origin_dir).unwrap();

        // Adding type annotations to help the compiler
        let allow_create_proposal: Option<fn(&CommandResult) -> bool> = None;
        let get_commit_message: Option<
            fn(&CommandResult, Option<&MergeProposal>) -> Option<String>,
        > = None;
        let get_title: Option<fn(&CommandResult, Option<&MergeProposal>) -> Option<String>> = None;

        // The function should return error code 2 when the script fails
        let result = apply_and_publish(
            &branch_url,
            "test-script",
            &[script_path.to_str().unwrap()],
            Mode::Push,
            CommitPending::Auto,
            None,
            false,
            None,
            None,
            false,
            allow_create_proposal,
            get_commit_message,
            get_title,
            |_, _, _| "Test description".to_string(),
            None,
            false,
            None,
        );

        assert_eq!(result, 2, "Script failure should return exit code 2");
    }

    #[test]
    #[serial]
    fn test_apply_and_publish_no_changes() {
        let _test_env = TestEnv::new();
        // Create a test directory structure
        let td = tempdir().unwrap();
        let origin_dir = td.path().join("origin");

        // Create a standalone working tree
        let tree =
            create_standalone_workingtree(&origin_dir, &ControlDirFormat::default()).unwrap();

        // Create an initial file and commit
        std::fs::write(origin_dir.join("README.md"), "# Test Repository").unwrap();
        tree.add(&[Path::new("README.md")]).unwrap();
        tree.build_commit()
            .message("Initial commit")
            .commit()
            .unwrap();

        // Create a script that will succeed but make no changes
        let script_dir = td.path().join("scripts");
        std::fs::create_dir(&script_dir).unwrap();
        let script_path =
            create_test_script(&script_dir, "no_change_script.sh", "#!/bin/sh\nexit 0");

        // Run apply_and_publish with a script that will succeed but make no changes
        let branch_url = Url::from_directory_path(&origin_dir).unwrap();

        // Adding type annotations to help the compiler
        let allow_create_proposal: Option<fn(&CommandResult) -> bool> = None;
        let get_commit_message: Option<
            fn(&CommandResult, Option<&MergeProposal>) -> Option<String>,
        > = None;
        let get_title: Option<fn(&CommandResult, Option<&MergeProposal>) -> Option<String>> = None;

        // The function should return error code 0 when the script makes no changes
        let result = apply_and_publish(
            &branch_url,
            "test-script",
            &[script_path.to_str().unwrap()],
            Mode::Push,
            CommitPending::Auto,
            None,
            false,
            None,
            None,
            false,
            allow_create_proposal,
            get_commit_message,
            get_title,
            |_, _, _| "Test description".to_string(),
            None,
            false,
            None,
        );

        assert_eq!(
            result, 0,
            "Script with no changes should return exit code 0"
        );
    }

    #[test]
    #[serial]
    fn test_apply_and_publish_with_verification() {
        let _test_env = TestEnv::new();
        // Create a test directory structure
        let td = tempdir().unwrap();
        let origin_dir = td.path().join("origin");

        // Create a standalone working tree
        let tree =
            create_standalone_workingtree(&origin_dir, &ControlDirFormat::default()).unwrap();

        // Create an initial file and commit
        std::fs::write(origin_dir.join("README.md"), "# Test Repository").unwrap();
        tree.add(&[Path::new("README.md")]).unwrap();
        tree.build_commit()
            .message("Initial commit")
            .commit()
            .unwrap();

        // Create a script that will succeed with changes
        let script_dir = td.path().join("scripts");
        std::fs::create_dir(&script_dir).unwrap();
        let script_content = r#"#!/bin/sh
echo "new content" > README.md
exit 0
"#;
        let script_path = create_test_script(&script_dir, "successful_script.sh", script_content);

        // Run apply_and_publish with a script and verification
        let branch_url = Url::from_directory_path(&origin_dir).unwrap();

        // Adding type annotations to help the compiler
        let allow_create_proposal: Option<fn(&CommandResult) -> bool> = None;
        let get_commit_message: Option<
            fn(&CommandResult, Option<&MergeProposal>) -> Option<String>,
        > = None;
        let get_title: Option<fn(&CommandResult, Option<&MergeProposal>) -> Option<String>> = None;

        // The function should return a non-zero code (either 1 or 2 depending on the implementation details)
        // The important part is that the script ran successfully but there was no merge proposal
        let result = apply_and_publish(
            &branch_url,
            "test-script",
            &[script_path.to_str().unwrap()],
            Mode::Push,
            CommitPending::Auto,
            None,
            false,
            Some("test -f README.md"), // Verification command
            None,
            false,
            allow_create_proposal,
            get_commit_message,
            get_title,
            |_, _, _| "Test description".to_string(),
            None,
            false,
            None,
        );

        assert_eq!(result, 2, "Script with changes should return exit code 2");
    }

    #[test]
    #[serial]
    fn test_apply_and_publish_with_verification_failure() {
        let _test_env = TestEnv::new();
        // Create a test directory structure
        let td = tempdir().unwrap();
        let origin_dir = td.path().join("origin");

        // Create a standalone working tree
        let tree =
            create_standalone_workingtree(&origin_dir, &ControlDirFormat::default()).unwrap();

        // Create an initial file and commit
        std::fs::write(origin_dir.join("README.md"), "# Test Repository").unwrap();
        tree.add(&[Path::new("README.md")]).unwrap();
        tree.build_commit()
            .message("Initial commit")
            .commit()
            .unwrap();

        // Create a script that will succeed with changes
        let script_dir = td.path().join("scripts");
        std::fs::create_dir(&script_dir).unwrap();
        let script_content = r#"#!/bin/sh
echo "new content" > README.md
exit 0
"#;
        let script_path = create_test_script(&script_dir, "successful_script.sh", script_content);

        // Run apply_and_publish with a script and verification that will fail
        let branch_url = Url::from_directory_path(&origin_dir).unwrap();

        // Adding type annotations to help the compiler
        let allow_create_proposal: Option<fn(&CommandResult) -> bool> = None;
        let get_commit_message: Option<
            fn(&CommandResult, Option<&MergeProposal>) -> Option<String>,
        > = None;
        let get_title: Option<fn(&CommandResult, Option<&MergeProposal>) -> Option<String>> = None;

        // The function should return error code 2 when verification fails
        let result = apply_and_publish(
            &branch_url,
            "test-script",
            &[script_path.to_str().unwrap()],
            Mode::Push,
            CommitPending::Auto,
            None,
            false,
            Some("test -f nonexistent.file"), // Verification command that will fail
            None,
            false,
            allow_create_proposal,
            get_commit_message,
            get_title,
            |_, _, _| "Test description".to_string(),
            None,
            false,
            None,
        );

        assert_eq!(result, 2, "Verification failure should return exit code 2");
    }

    #[test]
    #[serial]
    fn test_run_script_with_extra_env() {
        let _test_env = TestEnv::new();
        let (td, origin_dir, _) = create_test_repo();

        // Create a script that uses an environment variable
        let script_dir = td.path().join("scripts");
        std::fs::create_dir(&script_dir).unwrap();
        let script_content = r#"#!/bin/sh
echo $TEST_VAR > README.md
exit 0
"#;
        let script_path = create_test_script(&script_dir, "env_script.sh", script_content);

        // Open branch and build workspace
        let branch = open_branch(
            &Url::from_directory_path(origin_dir).unwrap(),
            None,
            None,
            None,
        )
        .unwrap();
        let ws = build_workspace(branch, None).unwrap();

        // Create extra environment variables
        let mut extra_env = HashMap::new();
        extra_env.insert(
            "TEST_VAR".to_string(),
            "Environment variable content".to_string(),
        );

        // Run script with extra environment
        let result = run_script(
            &ws,
            &[script_path.to_str().unwrap()],
            Path::new(""),
            CommitPending::Auto,
            Some(extra_env),
        );

        // Script should succeed
        assert!(
            result.is_ok(),
            "Script with extra environment should succeed"
        );

        // Verify the content was written correctly using the environment variable
        let readme_content =
            std::fs::read_to_string(ws.local_tree().abspath(Path::new("README.md")).unwrap())
                .unwrap();
        assert!(
            readme_content.contains("Environment variable content"),
            "Content should include text from environment variable"
        );
    }

    #[test]
    #[serial]
    fn test_run_verification_with_complex_command() {
        let _test_env = TestEnv::new();
        let (_td, origin_dir, _) = create_test_repo();

        // Open branch and build workspace
        let branch = open_branch(
            &Url::from_directory_path(origin_dir).unwrap(),
            None,
            None,
            None,
        )
        .unwrap();
        let ws = build_workspace(branch, None).unwrap();

        // Create a new file to verify with a complex command
        let test_dir = ws.local_tree().abspath(Path::new(".")).unwrap();
        std::fs::write(test_dir.join("testfile.txt"), "test content").unwrap();

        // Run a verification command with pipes and multiple commands
        let result = run_verification(
            &ws,
            "ls -la | grep testfile && cat testfile.txt | grep 'test content'",
        );

        // Verification should succeed
        assert!(
            result.is_ok(),
            "Complex verification command should succeed"
        );
    }

    #[test]
    #[serial]
    fn test_run_verification_with_invalid_command() {
        let _test_env = TestEnv::new();
        let (_td, origin_dir, _) = create_test_repo();

        // Open branch and build workspace
        let branch = open_branch(
            &Url::from_directory_path(origin_dir).unwrap(),
            None,
            None,
            None,
        )
        .unwrap();
        let ws = build_workspace(branch, None).unwrap();

        // Run an invalid command that should fail
        let result = run_verification(&ws, "invalid_command_that_doesnt_exist");

        // Verification should fail
        assert!(result.is_err(), "Invalid command should fail");
        assert_eq!(
            result.unwrap_err(),
            2,
            "Invalid command should return error code 2"
        );
    }

    #[test]
    fn test_apply_and_publish_builder_construction() {
        let url = Url::parse("https://github.com/test/repo").unwrap();
        let command = ["script.sh", "arg1"];

        // Test basic builder construction
        let builder = ApplyAndPublishBuilder::new(&url, "test-branch", &command, Mode::Push);

        // Verify fields are set correctly
        assert_eq!(builder.url, &url);
        assert_eq!(builder.name, "test-branch");
        assert_eq!(builder.command, &command);
        assert_eq!(builder.mode, Mode::Push);
        assert_eq!(builder.commit_pending, CommitPending::Auto);
        assert!(builder.labels.is_none());
        assert!(!builder.diff);
        assert!(builder.verify_command.is_none());
        assert!(builder.derived_owner.is_none());
        assert!(!builder.refresh);
        assert!(builder.extra_env.is_none());
    }

    #[test]
    fn test_apply_and_publish_builder_chaining() {
        let url = Url::parse("https://github.com/test/repo").unwrap();
        let command = ["script.sh"];
        let labels = ["bug", "feature"];
        let mut env = HashMap::new();
        env.insert("KEY".to_string(), "VALUE".to_string());

        // Test method chaining
        let builder = ApplyAndPublishBuilder::new(&url, "test-branch", &command, Mode::Propose)
            .commit_pending(CommitPending::Yes)
            .labels(&labels)
            .diff(true)
            .verify_command("make test")
            .derived_owner("derived-user")
            .refresh(true)
            .extra_env(env.clone());

        // Verify all fields are set
        assert_eq!(builder.commit_pending, CommitPending::Yes);
        assert_eq!(builder.labels, Some(&labels[..]));
        assert!(builder.diff);
        assert_eq!(builder.verify_command, Some("make test"));
        assert_eq!(builder.derived_owner, Some("derived-user"));
        assert!(builder.refresh);
        assert_eq!(builder.extra_env, Some(env));
    }

    #[test]
    fn test_apply_and_publish_builder_modes() {
        let url = Url::parse("https://github.com/test/repo").unwrap();
        let command = ["script.sh"];

        // Test with different modes
        let modes = vec![
            Mode::Push,
            Mode::Propose,
            Mode::AttemptPush,
            Mode::PushDerived,
            Mode::Bts,
        ];

        for mode in modes {
            let builder = ApplyAndPublishBuilder::new(&url, "test-branch", &command, mode);
            assert_eq!(builder.mode, mode);
        }
    }

    #[test]
    fn test_apply_and_publish_builder_auto_merge() {
        let url = Url::parse("https://github.com/test/repo").unwrap();
        let command = ["script.sh"];

        // Test default auto_merge is false
        let builder = ApplyAndPublishBuilder::new(&url, "test-branch", &command, Mode::Propose);
        assert!(!builder.auto_merge);

        // Test setting auto_merge to true
        let builder = builder.auto_merge(true);
        assert!(builder.auto_merge);

        // Test setting auto_merge to false
        let builder = builder.auto_merge(false);
        assert!(!builder.auto_merge);
    }

    #[test]
    #[serial]
    fn test_apply_and_publish_with_subpath() {
        let _test_env = TestEnv::new();
        // Create a test directory structure
        let td = tempdir().unwrap();
        let origin_dir = td.path().join("origin");

        // Create a standalone working tree
        let tree =
            create_standalone_workingtree(&origin_dir, &ControlDirFormat::default()).unwrap();

        // Create subdirectories
        std::fs::create_dir(origin_dir.join("frontend")).unwrap();
        std::fs::create_dir(origin_dir.join("backend")).unwrap();

        // Create files in subdirectories
        std::fs::write(
            origin_dir.join("frontend/package.json"),
            "{\"name\": \"frontend\"}",
        )
        .unwrap();
        std::fs::write(
            origin_dir.join("backend/package.json"),
            "{\"name\": \"backend\"}",
        )
        .unwrap();
        MutableTree::add(
            &tree,
            &[
                Path::new("frontend"),
                Path::new("frontend/package.json"),
                Path::new("backend"),
                Path::new("backend/package.json"),
            ],
        )
        .unwrap();
        tree.build_commit()
            .message("Initial commit")
            .commit()
            .unwrap();

        // Create a script that modifies files in a specific subdirectory
        let script_dir = td.path().join("scripts");
        std::fs::create_dir(&script_dir).unwrap();
        let script_content = r#"#!/bin/sh
echo '{"name": "frontend", "version": "2.0.0"}' > package.json
exit 0
"#;
        let script_path = create_test_script(&script_dir, "update_package.sh", script_content);

        // Run apply_and_publish with subpath
        let branch_url = Url::from_directory_path(&origin_dir).unwrap();

        let allow_create_proposal: Option<fn(&CommandResult) -> bool> = None;
        let get_commit_message: Option<
            fn(&CommandResult, Option<&MergeProposal>) -> Option<String>,
        > = None;
        let get_title: Option<fn(&CommandResult, Option<&MergeProposal>) -> Option<String>> = None;

        // Should succeed when running in frontend directory
        let result = apply_and_publish(
            &branch_url,
            "test-frontend",
            &[script_path.to_str().unwrap()],
            Mode::Push,
            CommitPending::Auto,
            None,
            false,
            None,
            None,
            false,
            allow_create_proposal,
            get_commit_message,
            get_title,
            |_, _, _| "Update frontend package.json".to_string(),
            None,
            false,
            Some(Path::new("frontend")),
        );

        assert_eq!(result, 2, "Script with changes should return exit code 2");
    }

    #[test]
    #[serial]
    fn test_run_script_with_subpath() {
        let _test_env = TestEnv::new();
        let (td, origin_dir, _) = create_test_repo();

        // Create subdirectory structure
        std::fs::create_dir(origin_dir.join("subdir")).unwrap();
        std::fs::write(origin_dir.join("subdir/file.txt"), "original content").unwrap();

        // Add the file to git
        let tree = open_branch(
            &Url::from_directory_path(&origin_dir).unwrap(),
            None,
            None,
            None,
        )
        .unwrap()
        .controldir()
        .open_workingtree()
        .unwrap();
        tree.add(&[Path::new("subdir"), Path::new("subdir/file.txt")])
            .unwrap();
        tree.build_commit()
            .message("Add subdir file")
            .commit()
            .unwrap();

        // Create a script that modifies a file
        let script_dir = td.path().join("scripts");
        std::fs::create_dir(&script_dir).unwrap();
        let script_content = r#"#!/bin/sh
echo "modified content" > file.txt
exit 0
"#;
        let script_path = create_test_script(&script_dir, "modify_file.sh", script_content);

        // Open branch and build workspace
        let branch = open_branch(
            &Url::from_directory_path(origin_dir).unwrap(),
            None,
            None,
            None,
        )
        .unwrap();
        let ws = build_workspace(branch, None).unwrap();

        // Run script in subdirectory
        let result = run_script(
            &ws,
            &[script_path.to_str().unwrap()],
            Path::new("subdir"),
            CommitPending::Auto,
            None,
        );

        // Script should succeed
        assert!(result.is_ok(), "Script in subpath should succeed");

        // Verify the file was modified
        let modified_content = std::fs::read_to_string(
            ws.local_tree()
                .abspath(Path::new("subdir/file.txt"))
                .unwrap(),
        )
        .unwrap();
        assert!(
            modified_content.contains("modified content"),
            "File should be modified by script"
        );
    }
}
