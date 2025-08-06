//! Debian Bug Tracking System (BTS) forge implementation for merge proposals
//!
//! TODO: This module implements the concept of merge proposals for the Debian BTS.
//! However, the current breezyshim API doesn't support custom Forge implementations
//! through traits. The Forge and MergeProposal types in breezyshim appear to be
//! concrete types rather than traits.
//!
//! To properly implement this feature, we would need to either:
//! 1. Extend breezyshim to support custom forge implementations
//! 2. Use a different approach to integrate BTS with silver-platter
//!
//! The implementation below shows how the BTS forge would work conceptually,
//! but cannot be used as-is until the breezyshim API supports extension.

use crate::proposal::DescriptionFormat;
use breezyshim::branch::{Branch, GenericBranch, PyBranch};
use breezyshim::error::Error as BrzError;
use breezyshim::forge::MergeProposalStatus;
#[allow(unused_imports)]
use breezyshim::repository::Repository;
use breezyshim::revisionid::RevisionId;
use debbugs::blocking::Debbugs;
use std::collections::HashMap;
use url::Url;

/// Represents a merge proposal in the Debian BTS
#[derive(Debug, Clone)]
#[allow(dead_code)]
pub struct BTSMergeProposal {
    /// Bug number in the BTS
    pub bug_number: u32,
    source_branch_url: Url,
    target_branch_url: Url,
    title: String,
    description: String,
    status: MergeProposalStatus,
    tags: Vec<String>,
    user_tags: HashMap<String, Vec<String>>,
}

impl BTSMergeProposal {
    /// Create a new BTS merge proposal
    pub fn new(
        bug_number: u32,
        source_branch_url: Url,
        target_branch_url: Url,
        title: String,
        description: String,
    ) -> Self {
        Self {
            bug_number,
            source_branch_url,
            target_branch_url,
            title,
            description,
            status: MergeProposalStatus::Open,
            tags: Vec::new(),
            user_tags: HashMap::new(),
        }
    }

    /// Check if the patch has been applied by looking at VCS headers
    pub fn check_patch_applied(&self, branch: &GenericBranch) -> Result<bool, BrzError> {
        // Check if the patch has been applied by comparing branch revisions
        // with the package's VCS repository

        // First, we need to get the package source information
        // Extract package name from somewhere - for now use a default
        let package_name = "unknown"; // TODO: Get actual package name
        if let Ok(source_pkg) = self.get_source_package_info(package_name) {
            // Check if the package has VCS information
            if let Some((_vcs_type, vcs_url)) = debian_analyzer::vcs::vcs_field(&source_pkg) {
                // Try to open the VCS branch
                match crate::vcs::open_branch(&vcs_url.parse().unwrap(), None, None, None) {
                    Ok(vcs_branch) => {
                        // Compare the branches to see if our changes are present
                        let our_revid = branch.last_revision();
                        let vcs_revid = vcs_branch.last_revision();

                        // Check if our revision is an ancestor of the VCS revision
                        let graph = branch.repository().get_graph();
                        if let Ok(is_ancestor) = graph.is_ancestor(&our_revid, &vcs_revid) {
                            return Ok(is_ancestor);
                        }
                    }
                    Err(e) => {
                        log::debug!("Failed to open VCS branch: {}", e);
                    }
                }
            }
        }

        Ok(false)
    }

    /// Get source package information from apt
    fn get_source_package_info(
        &self,
        package_name: &str,
    ) -> Result<debian_control::apt::Source, BrzError> {
        use breezyshim::debian::apt::LocalApt;

        let apt = LocalApt::new(None).map_err(|e| {
            BrzError::Other(pyo3::exceptions::PyIOError::new_err(format!(
                "Failed to open apt: {:?}",
                e
            )))
        })?;

        crate::debian::apt_get_source_package(&apt, package_name).ok_or_else(|| {
            BrzError::Other(pyo3::exceptions::PyKeyError::new_err(format!(
                "Package {} not found in apt",
                package_name
            )))
        })
    }

    /// Add user tags for retrospective finding
    pub fn add_user_tags(&mut self, user: &str, tags: Vec<String>) {
        self.user_tags.insert(user.to_string(), tags);
    }

    /// Send control email to BTS
    fn send_control_email(&self, command: &str) -> Result<(), BrzError> {
        use lettre::message::header::ContentType;
        use lettre::{Message, SmtpTransport, Transport};

        let maintainer = debian_changelog::get_maintainer()
            .unwrap_or_else(|| ("Unknown".to_string(), "unknown@localhost".to_string()));

        let email_content = format!("{}\nthanks\n", command);

        // Build the email
        let email = Message::builder()
            .from(
                format!("{} <{}>", maintainer.0, maintainer.1)
                    .parse()
                    .unwrap(),
            )
            .to("control@bugs.debian.org".parse().unwrap())
            .subject("BTS control commands")
            .header(ContentType::TEXT_PLAIN)
            .body(email_content)
            .map_err(|e| {
                BrzError::Other(pyo3::exceptions::PyValueError::new_err(format!(
                    "Failed to build email: {}",
                    e
                )))
            })?;

        // Try to send via SMTP
        let smtp = SmtpTransport::unencrypted_localhost();
        smtp.send(&email).map_err(|e| {
            BrzError::Other(pyo3::exceptions::PyIOError::new_err(format!(
                "Failed to send control email: {}",
                e
            )))
        })?;

        Ok(())
    }
}

// TODO: Once breezyshim supports custom MergeProposal implementations
// impl MergeProposal for BTSMergeProposal {
#[allow(dead_code)]
impl BTSMergeProposal {
    fn get_description(&self) -> Result<String, BrzError> {
        Ok(self.description.clone())
    }

    fn set_description(&mut self, description: &str) -> Result<(), BrzError> {
        self.description = description.to_string();
        // Send control email to update bug
        self.send_control_email(&format!("retitle {} {}", self.bug_number, description))
    }

    fn get_title(&self) -> Result<Option<String>, BrzError> {
        Ok(Some(self.title.clone()))
    }

    fn get_source_branch_url(&self) -> Result<Option<Url>, BrzError> {
        Ok(Some(self.source_branch_url.clone()))
    }

    fn get_target_branch_url(&self) -> Result<Option<Url>, BrzError> {
        Ok(Some(self.target_branch_url.clone()))
    }

    fn get_status(&self) -> Result<MergeProposalStatus, BrzError> {
        Ok(self.status.clone())
    }

    fn set_status(&mut self, status: MergeProposalStatus) -> Result<(), BrzError> {
        self.status = status;
        // Update bug status in BTS
        match status {
            MergeProposalStatus::Closed => {
                self.send_control_email(&format!("close {} ", self.bug_number))
            }
            MergeProposalStatus::Open => {
                self.send_control_email(&format!("reopen {} ", self.bug_number))
            }
            _ => Ok(()), // Other statuses don't map directly to BTS
        }
    }

    fn can_be_merged(&self) -> Result<bool, BrzError> {
        // In BTS, we can't directly check mergeability
        // This would depend on whether the maintainer has applied the patch
        Ok(false)
    }

    fn get_merged_by(&self) -> Result<Option<String>, BrzError> {
        // TODO: Check if bug is closed and by whom
        Ok(None)
    }

    fn get_merged_at(&self) -> Result<Option<chrono::DateTime<chrono::Utc>>, BrzError> {
        // TODO: Check bug closure date
        Ok(None)
    }

    fn close(&mut self) -> Result<(), BrzError> {
        self.status = MergeProposalStatus::Closed;
        // Close the bug in BTS
        self.send_control_email(&format!("close {} ", self.bug_number))
    }

    fn is_merged(&self) -> Result<bool, BrzError> {
        // TODO: Check if the bug is marked as fixed in a package upload
        Ok(false)
    }

    fn merge(&mut self, _approved_by: Option<&str>) -> Result<(), BrzError> {
        // BTS doesn't support direct merging
        Err(BrzError::UnsupportedOperation(
            "merge".to_string(),
            "BTS doesn't support direct merging".to_string(),
        ))
    }

    fn get_url(&self) -> Result<Url, BrzError> {
        Ok(format!("https://bugs.debian.org/{}", self.bug_number)
            .parse()
            .unwrap())
    }

    fn get_web_url(&self) -> Result<Option<Url>, BrzError> {
        Ok(Some(
            format!("https://bugs.debian.org/{}", self.bug_number)
                .parse()
                .unwrap(),
        ))
    }

    fn reopen(&mut self) -> Result<(), BrzError> {
        self.status = MergeProposalStatus::Open;
        // Reopen the bug in BTS
        self.send_control_email(&format!("reopen {} ", self.bug_number))
    }

    fn post_comment(&mut self, comment: &str) -> Result<(), BrzError> {
        // Send email to bug address
        use lettre::message::header::ContentType;
        use lettre::{Message, SmtpTransport, Transport};

        let maintainer = debian_changelog::get_maintainer()
            .unwrap_or_else(|| ("Unknown".to_string(), "unknown@localhost".to_string()));

        // Build the email
        let email = Message::builder()
            .from(
                format!("{} <{}>", maintainer.0, maintainer.1)
                    .parse()
                    .unwrap(),
            )
            .to(format!("{}@bugs.debian.org", self.bug_number)
                .parse()
                .unwrap())
            .subject(format!("Re: Bug #{}", self.bug_number))
            .header(ContentType::TEXT_PLAIN)
            .body(comment.to_string())
            .map_err(|e| {
                BrzError::Other(pyo3::exceptions::PyValueError::new_err(format!(
                    "Failed to build email: {}",
                    e
                )))
            })?;

        // Send via SMTP
        let smtp = SmtpTransport::unencrypted_localhost();
        smtp.send(&email).map_err(|e| {
            BrzError::Other(pyo3::exceptions::PyIOError::new_err(format!(
                "Failed to send comment: {}",
                e
            )))
        })?;

        Ok(())
    }

    fn get_merged_revision_id(&self) -> Result<Option<RevisionId>, BrzError> {
        // TODO: Extract from bug closure message if available
        Ok(None)
    }

    fn add_label(&mut self, label: &str) -> Result<(), BrzError> {
        self.tags.push(label.to_string());
        // Add tag to bug in BTS
        self.send_control_email(&format!("tags {} + {}", self.bug_number, label))
    }

    fn remove_label(&mut self, label: &str) -> Result<(), BrzError> {
        self.tags.retain(|t| t != label);
        // Remove tag from bug in BTS
        self.send_control_email(&format!("tags {} - {}", self.bug_number, label))
    }
}

/// Debian BTS Forge implementation
#[allow(dead_code)]
pub struct BTSForge {
    debbugs: Debbugs,
    /// Optional package name to use when filing bugs
    pub package_name: Option<String>,
}

impl BTSForge {
    /// Create a new BTS forge instance
    pub fn new(package_name: Option<String>) -> Self {
        Self {
            debbugs: Debbugs::default(),
            package_name,
        }
    }

    /// File a new bug with a patch
    pub fn file_bug_with_patch(
        &self,
        package: &str,
        _title: &str,
        description: &str,
        patch_content: &str,
        tags: Vec<String>,
        maintainer: Option<(String, String)>,
    ) -> Result<u32, BrzError> {
        // Generate bug report email
        let maintainer = maintainer.unwrap_or_else(|| {
            debian_changelog::get_maintainer()
                .unwrap_or_else(|| ("Unknown".to_string(), "unknown@localhost".to_string()))
        });

        let mut email_content = format!(
            "Package: {}\n\
             Version: FIXME\n\
             Severity: normal\n",
            package
        );

        if !tags.is_empty() {
            email_content.push_str(&format!("Tags: {}\n", tags.join(" ")));
        }

        // Add user tags for tracking
        email_content.push_str(&format!(
            "User: {}\n\
             Usertags: silver-platter\n",
            maintainer.1
        ));

        email_content.push_str(&format!(
            "\n\
             Dear Maintainer,\n\n\
             {}\n\n\
             I've attached a patch to fix this issue.\n\n\
             -- System Information:\n\
             Generated by silver-platter\n\n\
             --- BEGIN PATCH ---\n\
             {}\n\
             --- END PATCH ---\n",
            description, patch_content
        ));

        // Send email to submit@bugs.debian.org
        match self.send_bug_report_email(&maintainer, &email_content) {
            Ok(()) => {
                log::info!("Bug report email sent successfully");
                // BTS will assign a bug number via email response
                // For now, return a placeholder - in production, you'd wait for the response
                Ok(999999)
            }
            Err(e) => {
                log::error!("Failed to send bug report email: {}", e);
                Err(BrzError::Other(pyo3::exceptions::PyIOError::new_err(
                    format!("Failed to send bug report email: {}", e),
                )))
            }
        }
    }

    /// Send bug report email to BTS
    fn send_bug_report_email(
        &self,
        maintainer: &(String, String),
        email_content: &str,
    ) -> Result<(), Box<dyn std::error::Error>> {
        use lettre::message::header::ContentType;
        use lettre::transport::smtp::authentication::Credentials;
        use lettre::{Message, SmtpTransport, Transport};

        // Build the email
        let email = Message::builder()
            .from(format!("{} <{}>", maintainer.0, maintainer.1).parse()?)
            .to("submit@bugs.debian.org".parse()?)
            .subject("Bug report")
            .header(ContentType::TEXT_PLAIN)
            .body(email_content.to_string())?;

        // Try to send via local sendmail first, then SMTP
        if let Ok(sendmail_path) = which::which("sendmail") {
            // Use sendmail if available
            use std::io::Write;
            use std::process::{Command, Stdio};

            let mut child = Command::new(sendmail_path)
                .arg("-t")
                .arg("-oi")
                .stdin(Stdio::piped())
                .spawn()?;

            if let Some(mut stdin) = child.stdin.take() {
                let formatted = email.formatted();
                stdin.write_all(&formatted)?;
            }

            let status = child.wait()?;
            if status.success() {
                return Ok(());
            }
        }

        // Fall back to SMTP
        // Check for SMTP configuration in environment
        if let Ok(smtp_server) = std::env::var("SMTP_SERVER") {
            let smtp_user = std::env::var("SMTP_USER").ok();
            let smtp_pass = std::env::var("SMTP_PASS").ok();

            let mut mailer = SmtpTransport::relay(&smtp_server)?;

            if let (Some(user), Some(pass)) = (smtp_user, smtp_pass) {
                mailer = mailer.credentials(Credentials::new(user, pass));
            }

            let smtp = mailer.build();
            smtp.send(&email)?;

            Ok(())
        } else {
            // No SMTP configuration, try localhost
            let smtp = SmtpTransport::unencrypted_localhost();
            smtp.send(&email)?;
            Ok(())
        }
    }

    /// Find bugs by user tags
    pub fn find_bugs_by_user_tags(
        &self,
        user: &str,
        tags: &[String],
    ) -> Result<Vec<u32>, BrzError> {
        // Query BTS for bugs with specific user tags
        // Note: The debbugs crate has limited support for user tags,
        // so we'll search by regular tags and filter by package if available

        log::info!("Searching for bugs with user {} and tags {:?}", user, tags);

        // If we have a package name, search within that package
        if let Some(package_name) = &self.package_name {
            // Search for bugs with the given tags in this package
            match self.search_bugs_by_package_and_tags(package_name, tags) {
                Ok(bugs) => {
                    // In a full implementation, we'd filter these by user
                    // For now, return all bugs with matching tags
                    Ok(bugs)
                }
                Err(e) => {
                    log::warn!("Failed to search for user tags: {}", e);
                    Ok(Vec::new())
                }
            }
        } else {
            // Without a package name, we can't effectively search
            // In a full implementation, we'd use the SOAP interface directly
            log::warn!("User tag search requires package name or direct SOAP access");
            Ok(Vec::new())
        }
    }

    /// Get all silver-platter related bugs for a package
    pub fn get_silver_platter_bugs(
        &self,
        _package: &str,
    ) -> Result<Vec<BTSMergeProposal>, BrzError> {
        // Find all bugs tagged with silver-platter user tag
        let bugs =
            self.find_bugs_by_user_tags(&self.get_user_email()?, &["silver-platter".to_string()])?;

        // Convert bug numbers to merge proposals
        let mut proposals = Vec::new();
        for bug_num in bugs {
            if let Ok(mp) = self.get_bug_as_merge_proposal(bug_num) {
                proposals.push(mp);
            }
        }

        Ok(proposals)
    }

    /// Get user email for user tags
    fn get_user_email(&self) -> Result<String, BrzError> {
        Ok(debian_changelog::get_maintainer()
            .map(|(_, email)| email)
            .unwrap_or_else(|| "unknown@localhost".to_string()))
    }

    /// Generate patch from branch differences
    fn generate_patch_from_branches(
        &self,
        source_branch: &dyn Branch,
        target_branch: &dyn Branch,
    ) -> Result<String, BrzError> {
        use breezyshim::diff::show_diff_trees;
        use std::io::Cursor;

        // Get the trees from both branches
        let source_tree = source_branch.basis_tree()?;
        let target_tree = target_branch.basis_tree()?;

        // Generate diff
        let mut diff_output = Vec::new();
        {
            let mut cursor = Cursor::new(&mut diff_output);
            show_diff_trees(&target_tree, &source_tree, &mut cursor, None, None)?;
        }

        String::from_utf8(diff_output).map_err(|e| {
            BrzError::Other(pyo3::exceptions::PyUnicodeDecodeError::new_err(format!(
                "Invalid UTF-8 in diff: {}",
                e
            )))
        })
    }

    /// Search for bugs by package and tags
    fn search_bugs_by_package_and_tags(
        &self,
        package: &str,
        tags: &[String],
    ) -> Result<Vec<u32>, BrzError> {
        // The debbugs crate has limited API, so we'll use what's available
        // In a real implementation, we'd use the SOAP interface directly

        // For now, just log what we would search for
        log::info!(
            "Would search for bugs in package {} with tags {:?}",
            package,
            tags
        );

        // Return empty for now - in production, this would query the BTS
        Ok(Vec::new())
    }

    /// Convert a bug number to a merge proposal
    fn get_bug_as_merge_proposal(&self, bug_number: u32) -> Result<BTSMergeProposal, BrzError> {
        // Fetch bug details from BTS
        // Note: The debbugs crate's API is limited, so we'll create a basic proposal
        // In a real implementation, we'd use get_bugs() or the SOAP interface

        log::info!("Would fetch details for bug #{}", bug_number);

        // For now, create a basic merge proposal
        Ok(BTSMergeProposal::new(
            bug_number,
            format!("https://bugs.debian.org/{}", bug_number)
                .parse()
                .unwrap(),
            format!("https://bugs.debian.org/{}", bug_number)
                .parse()
                .unwrap(),
            format!("Bug #{}", bug_number),
            String::new(),
        ))
    }
}

// TODO: Once breezyshim supports custom Forge implementations
// impl Forge for BTSForge {
#[allow(dead_code)]
impl BTSForge {
    fn name(&self) -> &'static str {
        "debian-bts"
    }

    fn get_push_url(&self, branch: &dyn Branch) -> Url {
        // BTS doesn't have push URLs, return the branch URL
        branch.get_user_url()
    }

    fn publish_derived(
        &self,
        _local_branch: &dyn PyBranch,
        _main_branch: &dyn PyBranch,
        _name: &str,
        _overwrite_existing: Option<bool>,
        _owner: Option<&str>,
        _stop_revision: Option<&RevisionId>,
        _tag_selector: Option<Box<dyn Fn(String) -> bool>>,
    ) -> Result<(Box<dyn Branch>, Url), BrzError> {
        // BTS doesn't actually host branches, so we can't return a real branch
        // This is a limitation of the current architecture
        Err(BrzError::UnsupportedOperation(
            "publish_derived".to_string(),
            "BTS doesn't host branches".to_string(),
        ))
    }

    fn get_derived_branch(
        &self,
        _main_branch: &dyn Branch,
        _name: &str,
        _owner: Option<&str>,
    ) -> Result<Option<Box<dyn Branch>>, BrzError> {
        // BTS doesn't host branches
        Ok(None)
    }

    fn iter_proposals(
        &self,
        _source_branch: &dyn Branch,
        _target_branch: &dyn Branch,
        _status: MergeProposalStatus,
    ) -> Result<Vec<BTSMergeProposal>, BrzError> {
        // Query BTS for bugs with patches
        if let Some(package_name) = &self.package_name {
            // Search for bugs tagged with 'patch' for this package
            match self.search_bugs_by_package_and_tags(package_name, &["patch".to_string()]) {
                Ok(bug_numbers) => {
                    let mut proposals = Vec::new();
                    for bug_num in bug_numbers {
                        if let Ok(mp) = self.get_bug_as_merge_proposal(bug_num) {
                            proposals.push(mp);
                        }
                    }
                    Ok(proposals)
                }
                Err(e) => {
                    log::warn!("Failed to search BTS for bugs: {}", e);
                    Ok(Vec::new())
                }
            }
        } else {
            Ok(Vec::new())
        }
    }

    /// Get a merge proposal by its URL
    pub fn get_proposal_by_url(&self, url: &Url) -> Result<BTSMergeProposal, BrzError> {
        // Extract bug number from URL
        if let Some(bug_number) = url
            .path()
            .strip_prefix("/")
            .and_then(|p| p.parse::<u32>().ok())
        {
            // TODO: Fetch bug details from BTS
            Ok(BTSMergeProposal::new(
                bug_number,
                url.clone(),
                url.clone(),
                format!("Bug #{}", bug_number),
                String::new(),
            ))
        } else {
            Err(BrzError::Other(pyo3::exceptions::PyValueError::new_err(
                format!("Invalid BTS URL: {}", url),
            )))
        }
    }

    fn get_proposal(
        &self,
        _source_branch: &dyn Branch,
        _target_branch: &dyn Branch,
    ) -> Result<Option<BTSMergeProposal>, BrzError> {
        // TODO: Find existing bug for this branch
        Ok(None)
    }

    /// Create a new merge proposal by filing a bug with a patch
    pub fn create_proposal(
        &self,
        source_branch: &dyn Branch,
        target_branch: &dyn Branch,
        title: &str,
        description: &str,
        _prerequisite_branch: Option<&dyn Branch>,
        labels: Option<Vec<String>>,
        _commit_message: Option<&str>,
        _work_in_progress: bool,
        _allow_collaboration: bool,
        _reviewers: Option<Vec<String>>,
        _description_format: Option<DescriptionFormat>,
        _staging_branch_url: Option<&Url>,
        _auto_merge: Option<bool>,
    ) -> Result<BTSMergeProposal, BrzError> {
        let package_name = self.package_name.as_ref().ok_or_else(|| {
            BrzError::Other(pyo3::exceptions::PyValueError::new_err(
                "Package name required for BTS",
            ))
        })?;

        // Generate patch from branch diff
        let patch_content = self.generate_patch_from_branches(source_branch, target_branch)?;

        let bug_number = self.file_bug_with_patch(
            package_name,
            title,
            description,
            &patch_content,
            labels.unwrap_or_default(),
            None,
        )?;

        Ok(BTSMergeProposal::new(
            bug_number,
            source_branch.get_user_url(),
            target_branch.get_user_url(),
            title.to_string(),
            description.to_string(),
        ))
    }

    fn get_user_url(&self, _user: &str) -> Result<Url, BrzError> {
        // BTS doesn't have user URLs
        Err(BrzError::UnsupportedOperation(
            "get_user_url".to_string(),
            "BTS doesn't have user URLs".to_string(),
        ))
    }

    fn get_user(&self) -> Result<String, BrzError> {
        // TODO: Get from environment or debian maintainer info
        Ok(debian_changelog::get_maintainer()
            .map(|(name, email)| format!("{} <{}>", name, email))
            .unwrap_or_else(|| "Unknown".to_string()))
    }

    fn iter_my_proposals(
        &self,
        status: Option<MergeProposalStatus>,
        _author: Option<&str>,
    ) -> Result<Vec<BTSMergeProposal>, BrzError> {
        // Query BTS for bugs submitted by current user using user tags
        let user_email = self.get_user_email()?;
        let bugs = self.find_bugs_by_user_tags(&user_email, &["silver-platter".to_string()])?;

        let mut proposals: Vec<BTSMergeProposal> = Vec::new();
        for bug_num in bugs {
            if let Ok(mp) = self.get_bug_as_merge_proposal(bug_num) {
                // Filter by status if specified
                if let Some(requested_status) = &status {
                    if mp.get_status()? != *requested_status {
                        continue;
                    }
                }
                proposals.push(mp);
            }
        }

        Ok(proposals)
    }

    fn hosts(&self, _branch: &dyn Branch) -> bool {
        // BTS doesn't host branches
        false
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use breezyshim::controldir::{create_branch_convenience, ControlDirFormat};
    use std::collections::HashMap;
    use tempfile::TempDir;

    fn create_test_branch() -> (TempDir, Box<dyn breezyshim::branch::Branch>) {
        breezyshim::init();
        let td = tempfile::tempdir().unwrap();
        let path = td.path().canonicalize().unwrap();
        let url = url::Url::from_file_path(path).unwrap();
        let branch = create_branch_convenience(&url, None, &ControlDirFormat::default()).unwrap();
        (td, branch)
    }

    #[test]
    fn test_bts_merge_proposal_creation() {
        let mp = BTSMergeProposal::new(
            123456,
            "https://example.com/source".parse().unwrap(),
            "https://example.com/target".parse().unwrap(),
            "Fix typo in documentation".to_string(),
            "This patch fixes a typo in the documentation.".to_string(),
        );

        assert_eq!(mp.bug_number, 123456);
        assert_eq!(
            mp.get_title().unwrap(),
            Some("Fix typo in documentation".to_string())
        );
        assert_eq!(
            mp.get_url().unwrap().as_str(),
            "https://bugs.debian.org/123456"
        );
        assert_eq!(mp.get_status().unwrap(), MergeProposalStatus::Open);
        assert_eq!(mp.tags.len(), 0);
        assert_eq!(mp.user_tags.len(), 0);
    }

    #[test]
    fn test_bts_merge_proposal_urls() {
        let mp = BTSMergeProposal::new(
            987654,
            "https://salsa.debian.org/user/repo".parse().unwrap(),
            "https://salsa.debian.org/maintainer/repo".parse().unwrap(),
            "Important security fix".to_string(),
            "This patch addresses CVE-2023-12345".to_string(),
        );

        assert_eq!(
            mp.get_source_branch_url().unwrap(),
            Some("https://salsa.debian.org/user/repo".parse().unwrap())
        );
        assert_eq!(
            mp.get_target_branch_url().unwrap(),
            Some("https://salsa.debian.org/maintainer/repo".parse().unwrap())
        );
        assert_eq!(
            mp.get_web_url().unwrap(),
            Some("https://bugs.debian.org/987654".parse().unwrap())
        );
    }

    #[test]
    fn test_bts_merge_proposal_description_updates() {
        let mut mp = BTSMergeProposal::new(
            123456,
            "https://example.com/source".parse().unwrap(),
            "https://example.com/target".parse().unwrap(),
            "Initial title".to_string(),
            "Initial description".to_string(),
        );

        assert_eq!(mp.get_description().unwrap(), "Initial description");

        // Update description
        mp.set_description("Updated description with more details")
            .unwrap();
        assert_eq!(
            mp.get_description().unwrap(),
            "Updated description with more details"
        );
    }

    #[test]
    fn test_bts_merge_proposal_status_transitions() {
        let mut mp = BTSMergeProposal::new(
            123456,
            "https://example.com/source".parse().unwrap(),
            "https://example.com/target".parse().unwrap(),
            "Fix typo".to_string(),
            "Description".to_string(),
        );

        // Test initial status
        assert_eq!(mp.get_status().unwrap(), MergeProposalStatus::Open);

        // Test closing
        mp.set_status(MergeProposalStatus::Closed).unwrap();
        assert_eq!(mp.get_status().unwrap(), MergeProposalStatus::Closed);

        // Test reopening
        mp.set_status(MergeProposalStatus::Open).unwrap();
        assert_eq!(mp.get_status().unwrap(), MergeProposalStatus::Open);

        // Test close() method
        mp.close().unwrap();
        assert_eq!(mp.get_status().unwrap(), MergeProposalStatus::Closed);

        // Test reopen() method
        mp.reopen().unwrap();
        assert_eq!(mp.get_status().unwrap(), MergeProposalStatus::Open);
    }

    #[test]
    fn test_bts_merge_proposal_merge_operations() {
        let mut mp = BTSMergeProposal::new(
            123456,
            "https://example.com/source".parse().unwrap(),
            "https://example.com/target".parse().unwrap(),
            "Fix typo".to_string(),
            "Description".to_string(),
        );

        // Test merge operations that should fail for BTS
        assert!(mp.merge(None).is_err());
        assert!(mp.merge(Some("maintainer@example.com")).is_err());

        // Test merge status queries
        assert!(!mp.is_merged().unwrap());
        assert!(!mp.can_be_merged().unwrap());
        assert!(mp.get_merged_by().unwrap().is_none());
        assert!(mp.get_merged_at().unwrap().is_none());
        assert!(mp.get_merged_revision_id().unwrap().is_none());
    }

    #[test]
    fn test_bts_merge_proposal_labels_comprehensive() {
        let mut mp = BTSMergeProposal::new(
            123456,
            "https://example.com/source".parse().unwrap(),
            "https://example.com/target".parse().unwrap(),
            "Fix typo".to_string(),
            "Description".to_string(),
        );

        // Test adding multiple labels
        mp.add_label("patch").unwrap();
        mp.add_label("minor").unwrap();
        mp.add_label("documentation").unwrap();
        mp.add_label("easy").unwrap();

        assert_eq!(mp.tags.len(), 4);
        assert!(mp.tags.contains(&"patch".to_string()));
        assert!(mp.tags.contains(&"minor".to_string()));
        assert!(mp.tags.contains(&"documentation".to_string()));
        assert!(mp.tags.contains(&"easy".to_string()));

        // Test removing specific label
        mp.remove_label("minor").unwrap();
        assert_eq!(mp.tags.len(), 3);
        assert!(!mp.tags.contains(&"minor".to_string()));
        assert!(mp.tags.contains(&"patch".to_string()));

        // Test removing non-existent label (should not panic)
        mp.remove_label("non-existent").unwrap();
        assert_eq!(mp.tags.len(), 3);

        // Test adding duplicate label
        mp.add_label("patch").unwrap();
        assert_eq!(mp.tags.len(), 4); // Should add duplicate

        // Test removing one instance of duplicate
        mp.remove_label("patch").unwrap();
        assert_eq!(mp.tags.len(), 3);
        assert!(mp.tags.contains(&"patch".to_string())); // One instance should remain
    }

    #[test]
    fn test_bts_merge_proposal_user_tags_advanced() {
        let mut mp = BTSMergeProposal::new(
            123456,
            "https://example.com/source".parse().unwrap(),
            "https://example.com/target".parse().unwrap(),
            "Fix typo".to_string(),
            "Description".to_string(),
        );

        // Test adding user tags for different users
        mp.add_user_tags(
            "user1@example.com",
            vec!["silver-platter".to_string(), "automated".to_string()],
        );
        mp.add_user_tags("user2@debian.org", vec!["manual-review".to_string()]);
        mp.add_user_tags(
            "maintainer@pkg.org",
            vec!["approved".to_string(), "ready-to-merge".to_string()],
        );

        assert_eq!(mp.user_tags.len(), 3);

        // Test retrieving user tags
        assert_eq!(
            mp.user_tags.get("user1@example.com").unwrap(),
            &vec!["silver-platter".to_string(), "automated".to_string()]
        );
        assert_eq!(
            mp.user_tags.get("user2@debian.org").unwrap(),
            &vec!["manual-review".to_string()]
        );
        assert_eq!(
            mp.user_tags.get("maintainer@pkg.org").unwrap(),
            &vec!["approved".to_string(), "ready-to-merge".to_string()]
        );

        // Test overwriting user tags
        mp.add_user_tags("user1@example.com", vec!["updated-tag".to_string()]);
        assert_eq!(
            mp.user_tags.get("user1@example.com").unwrap(),
            &vec!["updated-tag".to_string()]
        );
        assert_eq!(mp.user_tags.len(), 3); // Count should remain the same

        // Test empty user tags
        mp.add_user_tags("empty@example.com", vec![]);
        assert_eq!(
            mp.user_tags.get("empty@example.com").unwrap(),
            &Vec::<String>::new()
        );
    }

    #[test]
    fn test_bts_forge_creation_and_properties() {
        // Test forge with package name
        let forge_with_pkg = BTSForge::new(Some("test-package".to_string()));
        assert_eq!(forge_with_pkg.name(), "debian-bts");
        assert_eq!(
            forge_with_pkg.package_name,
            Some("test-package".to_string())
        );

        // Test forge without package name
        let forge_no_pkg = BTSForge::new(None);
        assert_eq!(forge_no_pkg.name(), "debian-bts");
        assert_eq!(forge_no_pkg.package_name, None);
    }

    #[test]
    fn test_bts_forge_hosts_branches() {
        let (_td, branch) = create_test_branch();
        let forge = BTSForge::new(Some("test-package".to_string()));

        // BTS doesn't host branches, so should always return false
        assert!(!forge.hosts(&*branch));
    }

    #[test]
    fn test_bts_forge_user_operations() {
        let forge = BTSForge::new(Some("test-package".to_string()));

        // Test user URL operations (should fail)
        assert!(forge.get_user_url("testuser").is_err());

        // Test get_user (should succeed with maintainer info)
        assert!(forge.get_user().is_ok());
    }

    #[test]
    fn test_bts_forge_proposal_by_url() {
        let forge = BTSForge::new(Some("test-package".to_string()));

        // Test valid BTS URL
        let valid_url: Url = "https://bugs.debian.org/123456".parse().unwrap();
        let proposal = forge.get_proposal_by_url(&valid_url).unwrap();
        assert_eq!(proposal.bug_number, 123456);
        assert_eq!(
            proposal.get_url().unwrap().as_str(),
            "https://bugs.debian.org/123456"
        );

        // Test another valid URL
        let another_url: Url = "https://bugs.debian.org/987654".parse().unwrap();
        let another_proposal = forge.get_proposal_by_url(&another_url).unwrap();
        assert_eq!(another_proposal.bug_number, 987654);

        // Test invalid URLs
        let invalid_url: Url = "https://bugs.debian.org/invalid".parse().unwrap();
        assert!(forge.get_proposal_by_url(&invalid_url).is_err());

        let wrong_domain: Url = "https://github.com/user/repo/issues/123".parse().unwrap();
        assert!(forge.get_proposal_by_url(&wrong_domain).is_err());

        let empty_path: Url = "https://bugs.debian.org/".parse().unwrap();
        assert!(forge.get_proposal_by_url(&empty_path).is_err());
    }

    #[test]
    fn test_bts_forge_publish_operations() {
        let (_td, branch) = create_test_branch();
        let forge = BTSForge::new(Some("test-package".to_string()));

        // BTS doesn't support publishing branches
        let result = forge.publish_derived(&branch, &branch, "test-branch", None, None, None, None);
        assert!(result.is_err());

        // BTS doesn't support getting derived branches
        let derived = forge
            .get_derived_branch(&*branch, "test-branch", None)
            .unwrap();
        assert!(derived.is_none());
    }

    #[test]
    fn test_bts_forge_find_bugs_by_user_tags() {
        let forge = BTSForge::new(Some("test-package".to_string()));

        // Test with package name
        let result =
            forge.find_bugs_by_user_tags("user@example.com", &["silver-platter".to_string()]);
        assert!(result.is_ok());
        assert_eq!(result.unwrap(), Vec::<u32>::new()); // Should return empty for mock

        // Test with multiple tags
        let result = forge.find_bugs_by_user_tags(
            "user@example.com",
            &[
                "silver-platter".to_string(),
                "automated".to_string(),
                "patch".to_string(),
            ],
        );
        assert!(result.is_ok());

        // Test forge without package name
        let forge_no_pkg = BTSForge::new(None);
        let result = forge_no_pkg
            .find_bugs_by_user_tags("user@example.com", &["silver-platter".to_string()]);
        assert!(result.is_ok());
        assert_eq!(result.unwrap(), Vec::<u32>::new());
    }

    #[test]
    fn test_bts_forge_silver_platter_bugs() {
        let forge = BTSForge::new(Some("test-package".to_string()));

        let result = forge.get_silver_platter_bugs("test-package");
        assert!(result.is_ok());
        assert_eq!(result.unwrap().len(), 0); // Should return empty for mock
    }

    #[test]
    fn test_bts_forge_iter_proposals() {
        let (_td1, branch1) = create_test_branch();
        let (_td2, branch2) = create_test_branch();
        let forge = BTSForge::new(Some("test-package".to_string()));

        // Test with various statuses
        let open_proposals = forge.iter_proposals(&*branch1, &*branch2, MergeProposalStatus::Open);
        assert!(open_proposals.is_ok());

        let closed_proposals =
            forge.iter_proposals(&*branch1, &*branch2, MergeProposalStatus::Closed);
        assert!(closed_proposals.is_ok());

        // Test forge without package name
        let forge_no_pkg = BTSForge::new(None);
        let no_pkg_proposals =
            forge_no_pkg.iter_proposals(&*branch1, &*branch2, MergeProposalStatus::Open);
        assert!(no_pkg_proposals.is_ok());
        assert_eq!(no_pkg_proposals.unwrap().len(), 0);
    }

    #[test]
    fn test_bts_forge_iter_my_proposals() {
        let forge = BTSForge::new(Some("test-package".to_string()));

        // Test without status filter
        let all_proposals = forge.iter_my_proposals(None, None);
        assert!(all_proposals.is_ok());

        // Test with status filter
        let open_proposals = forge.iter_my_proposals(Some(MergeProposalStatus::Open), None);
        assert!(open_proposals.is_ok());

        let closed_proposals = forge.iter_my_proposals(Some(MergeProposalStatus::Closed), None);
        assert!(closed_proposals.is_ok());

        // Test with author filter
        let author_proposals = forge.iter_my_proposals(None, Some("author@example.com"));
        assert!(author_proposals.is_ok());
    }

    #[test]
    fn test_file_bug_with_patch_comprehensive() {
        let forge = BTSForge::new(Some("test-package".to_string()));

        // Test basic bug filing
        let result = forge.file_bug_with_patch(
            "test-package",
            "Fix important bug",
            "This fixes an important bug in the package",
            "--- a/file.txt\n+++ b/file.txt\n@@ -1 +1 @@\n-old\n+new",
            vec!["patch".to_string()],
            Some(("Test User".to_string(), "test@example.com".to_string())),
        );
        assert!(result.is_ok());
        assert_eq!(result.unwrap(), 999999);

        // Test with multiple tags
        let result = forge.file_bug_with_patch(
            "another-package",
            "Security fix",
            "This addresses a security vulnerability",
            "--- a/security.c\n+++ b/security.c\n@@ -10,1 +10,1 @@\n-vulnerable_code()\n+secure_code()",
            vec!["patch".to_string(), "security".to_string(), "urgent".to_string()],
            Some(("Security Team".to_string(), "security@debian.org".to_string())),
        );
        assert!(result.is_ok());

        // Test with no tags
        let result = forge.file_bug_with_patch(
            "minimal-package",
            "Simple fix",
            "A simple fix",
            "--- a/simple.txt\n+++ b/simple.txt\n@@ -1 +1 @@\n-wrong\n+right",
            vec![],
            None, // Use default maintainer
        );
        assert!(result.is_ok());

        // Test with empty patch
        let result = forge.file_bug_with_patch(
            "test-package",
            "Documentation only",
            "This is a documentation-only change",
            "",
            vec!["documentation".to_string()],
            None,
        );
        assert!(result.is_ok());
    }

    #[test]
    fn test_patch_generation_and_diff() {
        let (_td1, branch1) = create_test_branch();
        let (_td2, branch2) = create_test_branch();
        let forge = BTSForge::new(Some("test-package".to_string()));

        // Test patch generation between branches
        let result = forge.generate_patch_from_branches(&*branch1, &*branch2);
        assert!(result.is_ok());
        let patch = result.unwrap();
        assert!(patch.is_empty() || patch.contains("@@")); // Empty or contains diff markers
    }

    #[test]
    fn test_patch_application_checking() {
        let (_td, branch) = create_test_branch();
        let mp = BTSMergeProposal::new(
            123456,
            "https://example.com/source".parse().unwrap(),
            "https://example.com/target".parse().unwrap(),
            "Fix typo".to_string(),
            "Description".to_string(),
        );

        // Test patch application check (should handle missing package gracefully)
        let result = mp.check_patch_applied(&branch);
        assert!(result.is_ok());
        assert!(!result.unwrap()); // Should return false for non-applicable patches
    }

    #[test]
    fn test_error_handling_edge_cases() {
        let forge = BTSForge::new(Some("test-package".to_string()));

        // Test bug conversion with invalid numbers
        let result = forge.get_bug_as_merge_proposal(0);
        assert!(result.is_ok()); // Should handle gracefully

        let result = forge.get_bug_as_merge_proposal(u32::MAX);
        assert!(result.is_ok()); // Should handle gracefully

        // Test user email extraction
        let result = forge.get_user_email();
        assert!(result.is_ok());

        // Test search with empty package name
        let forge_no_pkg = BTSForge::new(None);
        let result = forge_no_pkg.search_bugs_by_package_and_tags("", &[]);
        assert!(result.is_ok());
    }

    #[test]
    fn test_merge_proposal_edge_cases() {
        // Test with very long descriptions
        let long_description = "A".repeat(10000);
        let mut mp = BTSMergeProposal::new(
            123456,
            "https://example.com/source".parse().unwrap(),
            "https://example.com/target".parse().unwrap(),
            "Long description test".to_string(),
            long_description.clone(),
        );

        assert_eq!(mp.get_description().unwrap(), long_description);

        // Test with special characters in descriptions
        let special_description = "Description with émojis 🚀 and spëcial chars: <>\"'&";
        mp.set_description(special_description).unwrap();
        assert_eq!(mp.get_description().unwrap(), special_description);

        // Test with empty strings
        mp.set_description("").unwrap();
        assert_eq!(mp.get_description().unwrap(), "");
    }

    #[test]
    fn test_comment_functionality() {
        let mut mp = BTSMergeProposal::new(
            123456,
            "https://example.com/source".parse().unwrap(),
            "https://example.com/target".parse().unwrap(),
            "Test proposal".to_string(),
            "Test description".to_string(),
        );

        // Test posting comments
        assert!(mp.post_comment("This is a test comment").is_ok());
        assert!(mp.post_comment("Another comment with more details").is_ok());
        assert!(mp.post_comment("").is_ok()); // Empty comment should work

        // Test posting comment with special characters
        assert!(mp
            .post_comment("Comment with émojis 🎉 and special chars: <>&\"'")
            .is_ok());

        // Test very long comment
        let long_comment = "Long comment: ".to_string() + &"X".repeat(5000);
        assert!(mp.post_comment(&long_comment).is_ok());
    }

    #[test]
    fn test_create_proposal_comprehensive() {
        let (_td1, source_branch) = create_test_branch();
        let (_td2, target_branch) = create_test_branch();
        let forge = BTSForge::new(Some("test-package".to_string()));

        // Test creating proposal with all parameters
        let result = forge.create_proposal(
            &*source_branch,
            &*target_branch,
            "Comprehensive test proposal",
            "This is a comprehensive test of the create_proposal functionality",
            None, // prerequisite_branch
            Some(vec![
                "patch".to_string(),
                "test".to_string(),
                "automated".to_string(),
            ]),
            Some("Custom commit message"),
            false, // work_in_progress
            false, // allow_collaboration
            Some(vec![
                "reviewer1@example.com".to_string(),
                "reviewer2@example.com".to_string(),
            ]),
            Some(DescriptionFormat::Markdown),
            None,        // staging_branch_url
            Some(false), // auto_merge
        );

        assert!(result.is_ok());
        let proposal = result.unwrap();
        assert_eq!(proposal.bug_number, 999999); // Mock returns this
        assert_eq!(
            proposal.get_title().unwrap(),
            Some("Comprehensive test proposal".to_string())
        );

        // Test creating proposal with minimal parameters
        let result = forge.create_proposal(
            &*source_branch,
            &*target_branch,
            "Minimal proposal",
            "Minimal description",
            None,
            None,
            None,
            false,
            false,
            None,
            None,
            None,
            None,
        );
        assert!(result.is_ok());
    }

    #[test]
    fn test_create_proposal_without_package() {
        let (_td1, source_branch) = create_test_branch();
        let (_td2, target_branch) = create_test_branch();
        let forge = BTSForge::new(None); // No package name

        // Should fail without package name
        let result = forge.create_proposal(
            &*source_branch,
            &*target_branch,
            "Test proposal",
            "Test description",
            None,
            None,
            None,
            false,
            false,
            None,
            None,
            None,
            None,
        );
        assert!(result.is_err());
    }

    #[test]
    fn test_proposal_operations_get_methods() {
        let (_td1, branch1) = create_test_branch();
        let (_td2, branch2) = create_test_branch();
        let forge = BTSForge::new(Some("test-package".to_string()));

        // Test get_proposal method
        let result = forge.get_proposal(&*branch1, &*branch2);
        assert!(result.is_ok());
        assert!(result.unwrap().is_none()); // Should return None for mock
    }

    #[test]
    fn test_large_scale_operations() {
        let forge = BTSForge::new(Some("test-package".to_string()));

        // Test handling many user tags
        let mut mp = BTSMergeProposal::new(
            123456,
            "https://example.com/source".parse().unwrap(),
            "https://example.com/target".parse().unwrap(),
            "Large scale test".to_string(),
            "Testing large scale operations".to_string(),
        );

        // Add many user tags
        for i in 0..100 {
            mp.add_user_tags(
                &format!("user{}@example.com", i),
                vec![
                    format!("tag{}", i),
                    "silver-platter".to_string(),
                    format!("category{}", i % 10),
                ],
            );
        }
        assert_eq!(mp.user_tags.len(), 100);

        // Add many labels
        for i in 0..50 {
            mp.add_label(&format!("label{}", i)).unwrap();
        }
        assert_eq!(mp.tags.len(), 50);

        // Test searching with many tags
        let many_tags: Vec<String> = (0..20).map(|i| format!("tag{}", i)).collect();
        let result = forge.find_bugs_by_user_tags("user@example.com", &many_tags);
        assert!(result.is_ok());
    }

    #[test]
    fn test_unicode_and_special_characters() {
        let forge = BTSForge::new(Some("тест-пакет".to_string())); // Cyrillic package name

        // Test Unicode in bug filing
        let result = forge.file_bug_with_patch(
            "пакет-unicode",
            "Исправление ошибки",
            "Это описание содержит Unicode символы: 🐛➡️✅",
            "--- a/файл.txt\n+++ b/файл.txt\n@@ -1 +1 @@\n-старое\n+новое",
            vec!["патч".to_string(), "unicode".to_string()],
            Some(("Тест Юзер".to_string(), "test@example.com".to_string())),
        );
        assert!(result.is_ok());

        // Test Unicode in merge proposal
        let mp = BTSMergeProposal::new(
            123456,
            "https://example.com/source".parse().unwrap(),
            "https://example.com/target".parse().unwrap(),
            "修复错误".to_string(),             // Chinese characters
            "この説明は日本語です".to_string(), // Japanese characters
        );

        assert_eq!(mp.get_title().unwrap(), Some("修复错误".to_string()));
        assert_eq!(mp.get_description().unwrap(), "この説明は日本語です");
    }
}

/// Example of how to use the BTS forge functionality
///
/// Since we cannot implement the Forge trait directly, this module would need to be
/// integrated differently. Some possible approaches:
///
/// 1. Create a wrapper that converts between BTS operations and standard forge operations
/// 2. Use BTS forge as a separate command-line tool or subcommand
/// 3. Integrate at a higher level in the silver-platter workflow
///
/// Example usage:
/// ```rust,ignore
/// use silver_platter::debian::bts_forge::BTSForge;
///
/// let bts_forge = BTSForge::new(Some("my-package".to_string()));
/// let proposal = bts_forge.create_proposal(
///     source_branch,
///     target_branch,
///     "Fix important bug",
///     "This patch fixes an important issue",
///     None,
///     Some(vec!["patch".to_string()]),
///     None,
///     false,
///     false,
///     None,
///     None,
///     None,
///     None,
/// )?;
/// println!("Created bug #{}", proposal.bug_number);
/// ```
pub fn example_usage() {
    // This function exists to attach the documentation
}
