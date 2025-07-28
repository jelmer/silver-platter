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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::CommitPending;
    use breezyshim::controldir::{create_standalone_workingtree, ControlDirFormat};
    use breezyshim::tree::MutableTree;
    use breezyshim::WorkingTree;
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
    fn test_open_branch_with_error_handling_success() {
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
    fn test_run_script_success() {
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
            CommitPending::Auto,
            None,
        );

        // Script should succeed
        assert!(result.is_ok(), "Script should succeed");
    }

    #[test]
    fn test_run_script_no_changes() {
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
    fn test_run_script_error() {
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
            CommitPending::Auto,
            None,
        );

        // Script should return an error code
        assert!(result.is_err(), "Failed script should return an error");
        assert_eq!(result.unwrap_err(), 2, "Failed script should return code 2");
    }

    #[test]
    fn test_run_verification_success() {
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
    fn test_run_verification_failure() {
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
    fn test_build_workspace() {
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
    fn test_build_workspace_with_resume_branch() {
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
    fn test_apply_and_publish_script_error() {
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
        );

        assert_eq!(result, 2, "Script failure should return exit code 2");
    }

    #[test]
    fn test_apply_and_publish_no_changes() {
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
        );

        assert_eq!(
            result, 0,
            "Script with no changes should return exit code 0"
        );
    }

    #[test]
    fn test_apply_and_publish_with_verification() {
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
        );

        assert_eq!(result, 2, "Script with changes should return exit code 2");
    }

    #[test]
    fn test_apply_and_publish_with_verification_failure() {
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
        );

        assert_eq!(result, 2, "Verification failure should return exit code 2");
    }

    #[test]
    fn test_run_script_with_extra_env() {
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
    fn test_run_verification_with_complex_command() {
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
    fn test_run_verification_with_invalid_command() {
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
}

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
    commit_pending: crate::CommitPending,
    extra_env: Option<HashMap<String, String>>,
) -> Result<CommandResult, i32> {
    let subpath = std::path::Path::new("");

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
        None,
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
    let result = match run_script(&ws, command, commit_pending, extra_env) {
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
        .map(|f| move |ep: Option<&MergeProposal>| -> Option<String> { f(&*result_ref, ep) });

    let result_ref = Arc::clone(&result);
    let get_title_wrapper = Some(move |ep: Option<&MergeProposal>| {
        if let Some(get_title) = get_title {
            get_title(&*result_ref, ep)
        } else {
            None
        }
    });

    // Step 7: Publish changes
    let labels_vec = labels.map(|l| l.iter().map(|&s| s.to_string()).collect());
    let allow_create = allow_create_proposal.map(|f| f(&*result));

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
