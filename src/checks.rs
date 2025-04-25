//! Check if the package should be uploaded
use breezyshim::tree::WorkingTree;
use breezyshim::RevisionId;
use std::collections::HashMap;
use std::error::Error;
use std::fmt;
use std::process::Command;

#[derive(Debug, PartialEq)]
/// The pre check failed
pub struct PreCheckFailed;

impl fmt::Display for PreCheckFailed {
    fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result {
        write!(f, "Pre-check failed")
    }
}

impl Error for PreCheckFailed {}

/// Run check to see if the package should be uploaded
pub fn run_pre_check(tree: WorkingTree, script: &str) -> Result<(), PreCheckFailed> {
    let path = tree.abspath(std::path::Path::new("")).unwrap();
    let status = Command::new("sh")
        .arg("-c")
        .arg(script)
        .current_dir(path)
        .status();

    match status {
        Ok(status) => {
            if status.code().unwrap() != 0 {
                Err(PreCheckFailed)
            } else {
                Ok(())
            }
        }
        Err(_) => Err(PreCheckFailed),
    }
}

#[derive(Debug, PartialEq)]
/// The post check failed
pub struct PostCheckFailed;

impl fmt::Display for PostCheckFailed {
    fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result {
        write!(f, "Post-check failed")
    }
}

impl Error for PostCheckFailed {}

/// Post-build check if the package should be uploaded
pub fn run_post_check(
    tree: WorkingTree,
    script: &str,
    since_revid: &RevisionId,
) -> Result<(), PostCheckFailed> {
    let mut env_vars = HashMap::new();
    env_vars.insert("SINCE_REVID", since_revid.to_string());
    let path = tree.abspath(std::path::Path::new("")).unwrap();

    let status = Command::new("sh")
        .arg("-c")
        .arg(script)
        .current_dir(path)
        .envs(&env_vars)
        .status();

    match status {
        Ok(status) => {
            if status.code().unwrap() != 0 {
                Err(PostCheckFailed)
            } else {
                Ok(())
            }
        }
        Err(_) => Err(PostCheckFailed),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use breezyshim::controldir::ControlDirFormat;
    use std::error::Error as StdError;
    use std::path::Path;
    use tempfile::tempdir;

    #[test]
    fn test_pre_check_failed_display() {
        let error = PreCheckFailed;
        assert_eq!(format!("{}", error), "Pre-check failed");

        // Test Error trait implementation
        let error: Box<dyn StdError> = Box::new(PreCheckFailed);
        assert_eq!(error.to_string(), "Pre-check failed");
    }

    #[test]
    fn test_post_check_failed_display() {
        let error = PostCheckFailed;
        assert_eq!(format!("{}", error), "Post-check failed");

        // Test Error trait implementation
        let error: Box<dyn StdError> = Box::new(PostCheckFailed);
        assert_eq!(error.to_string(), "Post-check failed");
    }

    #[test]
    fn test_run_pre_check_success() {
        let td = tempdir().unwrap();
        let wt = breezyshim::controldir::create_standalone_workingtree(
            td.path(),
            &ControlDirFormat::default(),
        )
        .unwrap();

        // Run a successful script
        let result = run_pre_check(wt, "exit 0");
        assert!(result.is_ok());
    }

    #[test]
    fn test_run_pre_check_failure() {
        let td = tempdir().unwrap();
        let wt = breezyshim::controldir::create_standalone_workingtree(
            td.path(),
            &ControlDirFormat::default(),
        )
        .unwrap();

        // Run a failing script
        let result = run_pre_check(wt, "exit 1");
        assert!(result.is_err());
        assert_eq!(result.err().unwrap(), PreCheckFailed);
    }

    #[test]
    fn test_run_pre_check_nonexistent_command() {
        let td = tempdir().unwrap();
        let wt = breezyshim::controldir::create_standalone_workingtree(
            td.path(),
            &ControlDirFormat::default(),
        )
        .unwrap();

        // Run a nonexistent command
        let result = run_pre_check(wt, "nonexistent_command_12345");
        assert!(result.is_err());
        assert_eq!(result.err().unwrap(), PreCheckFailed);
    }

    #[test]
    fn test_run_pre_check_with_file_creation() {
        let td = tempdir().unwrap();
        let wt = breezyshim::controldir::create_standalone_workingtree(
            td.path(),
            &ControlDirFormat::default(),
        )
        .unwrap();

        // Create a test file and check if it exists
        let script = "touch test_file.txt && test -f test_file.txt";
        let result = run_pre_check(wt.clone(), script);
        assert!(result.is_ok());

        // Verify the file was created in the working tree
        let file_path = Path::new("test_file.txt");
        let absolute_path = wt.abspath(file_path).unwrap();
        assert!(absolute_path.exists());
    }

    #[test]
    fn test_run_pre_check_with_multiple_commands() {
        let td = tempdir().unwrap();
        let wt = breezyshim::controldir::create_standalone_workingtree(
            td.path(),
            &ControlDirFormat::default(),
        )
        .unwrap();

        // Run multiple commands in a script
        let script = "mkdir -p test_dir && cd test_dir && touch test_file.txt && cd .. && test -f test_dir/test_file.txt";
        let result = run_pre_check(wt.clone(), script);
        assert!(result.is_ok());

        // Verify the directory and file were created
        let dir_path = Path::new("test_dir");
        let file_path = Path::new("test_dir/test_file.txt");
        let absolute_dir_path = wt.abspath(dir_path).unwrap();
        let absolute_file_path = wt.abspath(file_path).unwrap();
        assert!(absolute_dir_path.exists());
        assert!(absolute_file_path.exists());
    }

    #[test]
    fn test_run_post_check_success() {
        let td = tempdir().unwrap();
        let wt = breezyshim::controldir::create_standalone_workingtree(
            td.path(),
            &ControlDirFormat::default(),
        )
        .unwrap();

        let revid = wt.branch().last_revision();

        // Run a successful script
        let result = run_post_check(wt, "exit 0", &revid);
        assert!(result.is_ok());
    }

    #[test]
    fn test_run_post_check_failure() {
        let td = tempdir().unwrap();
        let wt = breezyshim::controldir::create_standalone_workingtree(
            td.path(),
            &ControlDirFormat::default(),
        )
        .unwrap();

        let revid = wt.branch().last_revision();

        // Run a failing script
        let result = run_post_check(wt, "exit 1", &revid);
        assert!(result.is_err());
        assert_eq!(result.err().unwrap(), PostCheckFailed);
    }

    #[test]
    fn test_run_post_check_environment() {
        let td = tempdir().unwrap();
        let wt = breezyshim::controldir::create_standalone_workingtree(
            td.path(),
            &ControlDirFormat::default(),
        )
        .unwrap();

        let revid = wt.branch().last_revision();

        // Verify that SINCE_REVID environment variable contains the revision ID
        let result = run_post_check(wt, "test \"$SINCE_REVID\" = \"null:\"", &revid);
        assert!(result.is_ok());
    }

    #[test]
    fn test_run_post_check_with_file_operations() {
        let td = tempdir().unwrap();
        let wt = breezyshim::controldir::create_standalone_workingtree(
            td.path(),
            &ControlDirFormat::default(),
        )
        .unwrap();

        // Make an initial commit
        std::fs::write(
            wt.abspath(Path::new("initial.txt")).unwrap(),
            "initial content",
        )
        .unwrap();
        wt.add(&[Path::new("initial.txt")]).unwrap();
        let revid = wt
            .build_commit()
            .message("Initial commit")
            .allow_pointless(true)
            .commit()
            .unwrap();

        // Create a post-check script that creates a file with the revision ID
        let script = "echo $SINCE_REVID > revid.txt && test -f revid.txt";
        let result = run_post_check(wt.clone(), script, &revid);
        assert!(result.is_ok());

        // Verify the file was created and contains the revision ID
        let file_path = wt.abspath(Path::new("revid.txt")).unwrap();
        assert!(file_path.exists());
        let content = std::fs::read_to_string(file_path).unwrap();
        assert_eq!(content.trim(), revid.to_string());
    }

    #[test]
    fn test_run_post_check_nonexistent_command() {
        let td = tempdir().unwrap();
        let wt = breezyshim::controldir::create_standalone_workingtree(
            td.path(),
            &ControlDirFormat::default(),
        )
        .unwrap();

        let revid = wt.branch().last_revision();

        // Run a nonexistent command
        let result = run_post_check(wt, "nonexistent_command_12345", &revid);
        assert!(result.is_err());
        assert_eq!(result.err().unwrap(), PostCheckFailed);
    }
}
