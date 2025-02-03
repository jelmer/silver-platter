//! Upload packages to the Debian archive.
use crate::vcs::{open_branch, BranchOpenError};
use breezyshim::branch::Branch;
use breezyshim::debian::apt::{Apt, LocalApt, RemoteApt};
use breezyshim::debian::error::Error as DebianError;
use breezyshim::error::Error as BrzError;
use breezyshim::gpg::VerificationResult;
use breezyshim::revisionid::RevisionId;
use breezyshim::tree::{MutableTree, Tree, WorkingTree};
use debversion::Version;
use std::collections::HashMap;
use std::path::Path;
use std::str::FromStr;

#[cfg(feature = "last-attempt-db")]
use trivialdb as tdb;

#[cfg(feature = "last-attempt-db")]
/// Database for storing the last upload attempt time for each package.
pub struct LastAttemptDatabase {
    db: tdb::Tdb,
}

#[cfg(feature = "last-attempt-db")]
impl LastAttemptDatabase {
    /// Open the last attempt database.
    pub fn open(path: &Path) -> Self {
        Self {
            db: tdb::Tdb::open(
                path,
                None,
                tdb::Flags::empty(),
                libc::O_RDWR | libc::O_CREAT,
                0o755,
            )
            .unwrap(),
        }
    }

    /// Get the last upload attempt time for a package.
    pub fn get(&self, package: &str) -> Option<chrono::DateTime<chrono::FixedOffset>> {
        let key = package.to_string().into_bytes();
        self.db.fetch(&key).unwrap().map(|value| {
            let value = String::from_utf8(value).unwrap();
            chrono::DateTime::parse_from_rfc3339(&value).unwrap()
        })
    }

    /// Set the last upload attempt time for a package.
    pub fn set(&mut self, package: &str, value: chrono::DateTime<chrono::FixedOffset>) {
        let key = package.to_string().into_bytes();
        let value = value.to_rfc3339();
        self.db.store(&key, value.as_bytes(), None).unwrap();
    }

    /// Set the last upload attempt time for a package to the current time.
    pub fn refresh(&mut self, package: &str) {
        self.set(package, chrono::Utc::now().into());
    }
}

#[cfg(feature = "last-attempt-db")]
impl Default for LastAttemptDatabase {
    fn default() -> Self {
        let xdg_dirs = xdg::BaseDirectories::with_prefix("silver-platter").unwrap();
        let last_attempt_path = xdg_dirs.place_data_file("last-upload-attempt.tdb").unwrap();
        Self::open(last_attempt_path.as_path())
    }
}

/// debsign a changes file
pub fn debsign(path: &Path, keyid: Option<&str>) -> Result<(), std::io::Error> {
    let mut args = vec!["debsign".to_string()];
    if let Some(keyid) = keyid {
        args.push(format!("-k{}", keyid));
    }
    args.push(path.file_name().unwrap().to_string_lossy().to_string());
    let status = std::process::Command::new("debsign")
        .args(&args)
        .current_dir(path.parent().unwrap())
        .status()?;

    if !status.success() {
        Err(std::io::Error::new(
            std::io::ErrorKind::Other,
            "debsign failed",
        ))
    } else {
        Ok(())
    }
}

/// dput a changes file
pub fn dput_changes(path: &Path) -> Result<(), std::io::Error> {
    let status = std::process::Command::new("dput")
        .arg(path.file_name().unwrap().to_string_lossy().to_string())
        .current_dir(path.parent().unwrap())
        .status()?;

    if !status.success() {
        Err(std::io::Error::new(
            std::io::ErrorKind::Other,
            "dput failed",
        ))
    } else {
        Ok(())
    }
}

#[cfg(feature = "gpg")]
/// Get the key IDs for Debian maintainers.
pub fn get_maintainer_keys(context: &mut gpgme::Context) -> Result<Vec<String>, gpgme::Error> {
    context.import("/usr/share/keyrings/debian-keyring.gpg")?;

    let mut ids = vec![];

    for key in context.keys()? {
        if let Err(err) = key {
            eprintln!("Error getting key: {}", err);
            continue;
        }

        let key = key.unwrap();

        if let Ok(key_id) = key.id() {
            ids.push(key_id.to_string());
        }

        for subkey in key.subkeys() {
            if let Ok(subkey_id) = subkey.id() {
                ids.push(subkey_id.to_string());
            }
        }
    }

    Ok(ids)
}

#[derive(Clone, Debug)]
/// Result of uploading a package.
pub enum UploadPackageError {
    /// Package was ignored.
    Ignored(String, Option<String>),

    /// Package processing failed.
    ProcessingFailure(String, Option<String>),
}

fn vcswatch_prescan_package(
    _package: &str,
    vw: &VcswatchEntry,
    exclude: Option<&[String]>,
    min_commit_age: Option<i64>,
    allowed_committers: Option<&[String]>,
) -> Result<Option<chrono::DateTime<chrono::Utc>>, UploadPackageError> {
    if let Some(exclude) = exclude {
        if exclude.contains(&vw.package) {
            return Err(UploadPackageError::Ignored(
                "excluded".to_string(),
                Some("Excluded".to_string()),
            ));
        }
    }
    if vw.url.is_none() || vw.vcs.is_none() {
        return Err(UploadPackageError::ProcessingFailure(
            "not-in-vcs".to_string(),
            Some("Not in VCS".to_string()),
        ));
    }
    // TODO(jelmer): check autopkgtest_only ?
    // from debian.deb822 import Deb822
    // pkg_source = Deb822(vw.controlfile)
    // has_testsuite = "Testsuite" in pkg_source
    if vw.commits == 0 {
        return Err(UploadPackageError::Ignored(
            "no-unuploaded-changes".to_string(),
            Some("No unuploaded changes".to_string()),
        ));
    }
    if vw.status.as_deref() == Some("ERROR") {
        log::warn!("vcswatch: unable to access {}: {:?}", vw.package, vw.error);
        return Err(UploadPackageError::ProcessingFailure(
            "vcs-inaccessible".to_string(),
            Some(format!("Unable to access vcs: {:?}", vw.error)),
        ));
    }
    if let Some(last_scan) = vw.last_scan.as_ref() {
        log::debug!("vcswatch last scanned at: {}", last_scan);
    }
    if vw.vcs.as_deref() == Some("Git") {
        if let Some(vcslog) = vw.vcslog.as_ref() {
            match check_git_commits(vcslog, min_commit_age, allowed_committers) {
                Err(RevisionRejected::CommitterNotAllowed(committer, allowed_committers)) => {
                    log::warn!(
                        "{}: committer {} not in allowed list: {:?}",
                        vw.package,
                        committer,
                        allowed_committers,
                    );
                    return Err(UploadPackageError::Ignored(
                        "committer-not-allowed".to_string(),
                        Some(format!(
                            "committer {} not in allowed list: {:?}",
                            committer, allowed_committers
                        )),
                    ));
                }
                Err(RevisionRejected::RecentCommits(commit_age, min_commit_age)) => {
                    log::info!(
                        "{}: Recent commits ({} days < {} days), skipping.",
                        vw.package,
                        commit_age,
                        min_commit_age,
                    );
                    return Err(UploadPackageError::Ignored(
                        "recent-commits".to_string(),
                        Some(format!(
                            "Recent commits ({} days < {} days)",
                            commit_age, min_commit_age
                        )),
                    ));
                }
                Ok(ts) => {
                    return Ok(Some(ts));
                }
            }
        }
    }
    Ok(None)
}

fn check_git_commits(
    vcslog: &str,
    min_commit_age: Option<i64>,
    allowed_committers: Option<&[String]>,
) -> Result<chrono::DateTime<chrono::Utc>, RevisionRejected> {
    #[allow(dead_code)]
    pub struct GitRevision {
        commit_id: String,
        headers: HashMap<String, String>,
        message: String,
    }

    impl Revision for GitRevision {
        fn committer(&self) -> Option<&str> {
            GitRevision::committer(self)
        }

        fn timestamp(&self) -> chrono::DateTime<chrono::Utc> {
            GitRevision::timestamp(self)
        }
    }

    impl GitRevision {
        pub fn committer(&self) -> Option<&str> {
            if let Some(committer) = self.headers.get("Committer") {
                Some(committer)
            } else {
                self.headers.get("Author").map(|s| s.as_str())
            }
        }

        pub fn timestamp(&self) -> chrono::DateTime<chrono::Utc> {
            let datestr = self.headers.get("Date").unwrap();

            chrono::DateTime::parse_from_rfc2822(datestr)
                .unwrap()
                .to_utc()
        }

        pub fn from_lines(lines: &[&str]) -> Self {
            let mut commit_id: Option<String> = None;
            let mut message = vec![];
            let mut headers = std::collections::HashMap::new();
            for (i, line) in lines.iter().enumerate() {
                if let Some(cid) = line.strip_prefix("commit ") {
                    commit_id = Some(cid.to_string());
                } else if line == &"" {
                    message = lines[i + 1..].to_vec();
                    break;
                } else {
                    let mut parts = line.split(": ");
                    let name = parts.next().unwrap();
                    let value = parts.next().unwrap();
                    headers.insert(name.to_string(), value.to_string());
                }
            }
            Self {
                commit_id: commit_id.unwrap(),
                headers,
                message: message.join("\n"),
            }
        }
    }

    let mut last_commit_ts: Option<chrono::DateTime<chrono::Utc>> = None;
    let mut lines: Vec<String> = vec![];
    for line in vcslog.lines() {
        if line.is_empty()
            && lines
                .last()
                .unwrap()
                .chars()
                .next()
                .unwrap()
                .is_whitespace()
        {
            let gitrev = GitRevision::from_lines(
                lines
                    .iter()
                    .map(|s| s.as_ref())
                    .collect::<Vec<_>>()
                    .as_slice(),
            );
            if last_commit_ts.is_none() {
                last_commit_ts = Some(gitrev.timestamp());
            }
            check_revision(&gitrev, min_commit_age, allowed_committers)?;
            lines = vec![];
        } else {
            lines.push(line.to_string());
        }
    }
    if !lines.is_empty() {
        let gitrev = GitRevision::from_lines(
            lines
                .iter()
                .map(|s| s.as_ref())
                .collect::<Vec<_>>()
                .as_slice(),
        );
        if last_commit_ts.is_none() {
            last_commit_ts = Some(gitrev.timestamp());
        }
        check_revision(&gitrev, min_commit_age, allowed_committers)?;
    }
    Ok(last_commit_ts.unwrap())
}

trait Revision {
    fn committer(&self) -> Option<&str>;
    fn timestamp(&self) -> chrono::DateTime<chrono::Utc>;
}

impl Revision for breezyshim::repository::Revision {
    fn committer(&self) -> Option<&str> {
        Some(self.committer.as_str())
    }

    fn timestamp(&self) -> chrono::DateTime<chrono::Utc> {
        chrono::DateTime::from_timestamp(self.timestamp as i64, 0).unwrap()
    }
}

/// Errors that can occur when checking a revision.
pub enum RevisionRejected {
    /// The committer is not allowed.
    CommitterNotAllowed(String, Vec<String>),

    /// The commit is too recent.
    RecentCommits(i64, i64),
}

/// Check whether a revision can be included in an upload.
///
/// # Arguments
/// * `rev` - revision to check
/// * `min_commit_age` - minimum age for revisions
/// * `allowed_committers` - list of allowed committers
fn check_revision(
    rev: &dyn Revision,
    min_commit_age: Option<i64>,
    allowed_committers: Option<&[String]>,
) -> Result<(), RevisionRejected> {
    // TODO(jelmer): deal with timezone
    if let Some(min_commit_age) = min_commit_age {
        let commit_time = rev.timestamp();
        let time_delta = chrono::Utc::now().signed_duration_since(commit_time);
        if time_delta.num_days() < min_commit_age {
            return Err(RevisionRejected::RecentCommits(
                time_delta.num_days(),
                min_commit_age,
            ));
        }
    }

    if let Some(allowed_committers) = allowed_committers.as_ref() {
        // TODO(jelmer): Allow tag to prevent automatic uploads
        let committer = rev.committer().unwrap();
        let committer_email = match breezyshim::config::extract_email_address(committer) {
            Some(email) => email,
            None => {
                log::warn!("Unable to extract email from {}", committer);
                return Err(RevisionRejected::CommitterNotAllowed(
                    committer.to_string(),
                    allowed_committers.iter().map(|s| s.to_string()).collect(),
                ));
            }
        };

        if !allowed_committers.contains(&committer_email) {
            return Err(RevisionRejected::CommitterNotAllowed(
                committer_email,
                allowed_committers.iter().map(|s| s.to_string()).collect(),
            ));
        }
    }

    Ok(())
}

#[derive(serde::Deserialize)]
/// Struct for vcswatch entry
struct VcswatchEntry {
    /// Package name
    package: String,

    /// Control file
    vcslog: Option<String>,

    /// Number of commits
    commits: usize,

    /// Control file
    url: Option<String>,
    last_scan: Option<String>,
    status: Option<String>,
    error: Option<String>,
    vcs: Option<String>,
    archive_version: Option<debversion::Version>,
}

fn vcswatch_prescan_packages(
    packages: &[String],
    inc_stats: &mut dyn FnMut(&str),
    exclude: Option<&[String]>,
    min_commit_age: Option<i64>,
    allowed_committers: Option<&[String]>,
) -> Result<(Vec<String>, usize, HashMap<String, VcswatchEntry>), Box<dyn std::error::Error>> {
    log::info!("Using vcswatch to prescan {} packages", packages.len());

    let url = url::Url::parse("https://qa.debian.org/data/vcswatch/vcswatch.json.gz")?;
    let client = reqwest::blocking::Client::new();
    let request = client
        .request(reqwest::Method::GET, url)
        .header(
            "User-Agent",
            format!("silver-platter/{}", env!("CARGO_PKG_VERSION")),
        )
        .build()?;

    let response = client.execute(request)?;

    assert!(
        response.status().is_success(),
        "Failed to fetch vcswatch data"
    );

    let d = flate2::read::GzDecoder::new(response);
    let entries: Vec<VcswatchEntry> = serde_json::from_reader(d)?;

    let vcswatch = entries
        .into_iter()
        .map(|e| (e.package.clone(), e))
        .collect::<HashMap<_, _>>();

    let mut by_ts = HashMap::new();
    let mut failures = 0;
    for package in packages.iter() {
        let vw = if let Some(p) = vcswatch.get(package) {
            p
        } else {
            continue;
        };
        match vcswatch_prescan_package(package, vw, exclude, min_commit_age, allowed_committers) {
            Err(UploadPackageError::ProcessingFailure(reason, _description)) => {
                inc_stats(reason.as_str());
                failures += 1;
            }
            Err(UploadPackageError::Ignored(reason, _description)) => {
                inc_stats(reason.as_str());
            }
            Ok(ts) => {
                by_ts.insert(package, ts);
            }
        }
    }

    let mut ts_items = by_ts.into_iter().collect::<Vec<_>>();
    ts_items.sort_by(|a, b| b.1.cmp(&a.1));
    let packages = ts_items
        .into_iter()
        .map(|(k, _)| k.to_string())
        .collect::<Vec<_>>();

    Ok((packages, failures, vcswatch))
}

fn find_last_release_revid(branch: &dyn Branch, version: &Version) -> Result<RevisionId, BrzError> {
    use pyo3::prelude::*;
    pyo3::Python::with_gil(|py| -> PyResult<RevisionId> {
        let m = py.import_bound("breezy.plugins.debian.import_dsc")?;
        let dbc = m.getattr("DistributionBranch")?;
        let dbc = dbc.call1((branch.to_object(py), py.None()))?;

        dbc.call_method1("revid_of_version", (version.to_object(py),))?
            .extract::<RevisionId>()
    })
    .map_err(|e| BrzError::from(e))
}

/// Select packages from the apt repository.
fn select_apt_packages(
    apt_repo: &dyn Apt,
    package_names: Option<&[String]>,
    maintainer: Option<&[String]>,
) -> Vec<String> {
    let mut packages = vec![];

    for source in apt_repo.iter_sources() {
        if let Some(maintainer) = maintainer {
            let m = source.maintainer().unwrap();
            let (_fullname, email) = debian_changelog::parseaddr(&m);
            if !maintainer.contains(&email.to_string()) {
                continue;
            }
        }

        if let Some(package_names) = package_names {
            if !package_names.contains(&source.package().unwrap()) {
                continue;
            }
        }

        packages.push(source.package().unwrap());
    }

    packages
}

/// Process a package for upload.
pub fn main(
    mut packages: Vec<String>,
    acceptable_keys: Option<Vec<String>>,
    gpg_verification: bool,
    min_commit_age: Option<i64>,
    diff: bool,
    builder: String,
    mut maintainer: Option<Vec<String>>,
    vcswatch: bool,
    exclude: Option<Vec<String>>,
    autopkgtest_only: bool,
    allowed_committers: Option<Vec<String>>,
    debug: bool,
    shuffle: bool,
    verify_command: Option<String>,
    apt_repository: Option<String>,
    apt_repository_key: Option<std::path::PathBuf>,
) -> Result<(), i32> {
    let mut ret = Ok(());

    if packages.is_empty() && maintainer.is_none() {
        if let Some((_name, email)) = debian_changelog::get_maintainer() {
            log::info!("Processing packages maintained by {}", email);
            maintainer = Some(vec![email]);
        }
    }

    if !vcswatch {
        log::info!(
            "Use --vcswatch to only process packages for which vcswatch found pending commits."
        )
    }

    let apt_repo: Box<dyn Apt> = if let Some(apt_repository) = apt_repository.as_ref() {
        Box::new(RemoteApt::from_string(apt_repository, apt_repository_key.as_deref()).unwrap())
            as _
    } else {
        Box::new(LocalApt::new(None).unwrap()) as _
    };

    if let Some(maintainer) = maintainer.as_ref() {
        packages = select_apt_packages(
            apt_repo.as_ref(),
            Some(packages.as_slice()),
            Some(maintainer),
        );
    }

    if packages.is_empty() {
        log::info!("No packages found.");
        return Err(1);
    }

    if shuffle {
        use rand::seq::SliceRandom;
        // Shuffle packages vec
        let mut rng = rand::rng();
        packages.shuffle(&mut rng);
    }

    let mut stats = HashMap::new();

    let mut inc_stats = |result: &str| {
        *stats.entry(result.to_string()).or_insert(0) += 1;
    };

    let mut extra_data: Option<HashMap<String, VcswatchEntry>> = None;

    if vcswatch {
        let (new_packages, failures, new_extra_data) = vcswatch_prescan_packages(
            packages.as_slice(),
            &mut &mut inc_stats,
            exclude.as_deref(),
            min_commit_age,
            allowed_committers.as_deref(),
        )
        .unwrap();
        packages = new_packages;
        extra_data = Some(new_extra_data);
        if failures > 0 {
            ret = Err(1);
        }
    };

    if packages.len() > 1 {
        log::info!(
            "Uploading {} packages: {}",
            packages.len(),
            packages.join(", ")
        );
    }

    #[cfg(feature = "last-attempt-db")]
    let mut last_attempt = LastAttemptDatabase::default();

    #[cfg(feature = "last-attempt-db")]
    {
        let orig_packages = packages.clone();

        let last_attempt_key = |p: &String| -> (chrono::DateTime<chrono::FixedOffset>, usize) {
            let t = last_attempt.get(p).unwrap_or(chrono::Utc::now().into());
            (t, orig_packages.iter().position(|i| i == p).unwrap())
        };

        packages.sort_by_key(last_attempt_key);
    }

    for package in packages.iter() {
        let extra_package = extra_data.as_ref().and_then(|d| d.get(package));

        match process_package(
            apt_repo.as_ref(),
            package,
            &builder,
            exclude.as_deref(),
            autopkgtest_only,
            gpg_verification,
            acceptable_keys.as_deref(),
            debug,
            diff,
            min_commit_age,
            allowed_committers.as_deref(),
            extra_package.and_then(|p| p.vcs.as_deref()),
            extra_package.and_then(|p| p.url.as_deref()),
            extra_package.map(|p| p.package.as_str()),
            extra_package.and_then(|p| p.archive_version.as_ref()),
            verify_command.as_deref(),
        ) {
            Err(UploadPackageError::ProcessingFailure(reason, _description)) => {
                inc_stats(reason.as_str());
                ret = Err(1);
            }
            Err(UploadPackageError::Ignored(reason, _description)) => inc_stats(reason.as_str()),
            Ok(_) => {
                inc_stats("success");
            }
        }

        #[cfg(feature = "last-attempt-db")]
        last_attempt.refresh(package);
    }

    if packages.len() > 1 {
        log::info!("Results:");
        for (error, c) in stats.iter() {
            log::info!("  {}: {}", error, c);
        }
    }

    ret
}

/// Errors that can occur when preparing a package for upload.
pub enum PrepareUploadError {
    /// Failed to run gbp dch
    GbpDchFailed,

    /// No unuploaded changes since the last upload
    NoUnuploadedChanges(Version),

    /// The last upload was more recent than the previous upload
    LastUploadMoreRecent(Version, Version),

    /// The last release revision was not found
    LastReleaseRevisionNotFound(String, Version),

    /// No unreleased changes
    NoUnreleasedChanges(Version),

    /// Generated changelog file
    GeneratedChangelogFile,

    /// No valid GPG signature
    NoValidGpgSignature(RevisionId, VerificationResult),

    /// Revision rejected
    Rejected(RevisionRejected),

    /// Build failed
    BuildFailed,

    /// Missing upstream tarball
    MissingUpstreamTarball(String, String),

    /// Package version not present
    PackageVersionNotPresent(String, String),

    /// Missing changelog
    MissingChangelog,

    /// Changelog parse error
    ChangelogParseError(String),

    /// Breezy error
    BrzError(BrzError),

    /// Debian error
    DebianError(DebianError),

    /// There is a missing nested tree
    MissingNestedTree(std::path::PathBuf),
}

impl From<BrzError> for PrepareUploadError {
    fn from(e: BrzError) -> Self {
        match e {
            BrzError::MissingNestedTree(p) => PrepareUploadError::MissingNestedTree(p),
            e => PrepareUploadError::BrzError(e),
        }
    }
}

/// Prepare a package for upload.
pub fn prepare_upload_package(
    local_tree: &WorkingTree,
    subpath: &std::path::Path,
    pkg: &str,
    last_uploaded_version: Option<&debversion::Version>,
    builder: &str,
    gpg_strategy: Option<breezyshim::gpg::GPGStrategy>,
    min_commit_age: Option<i64>,
    allowed_committers: Option<&[String]>,
    apt: Option<&dyn Apt>,
) -> Result<(std::path::PathBuf, Option<String>), PrepareUploadError> {
    let mut builder = builder.to_string();
    let debian_path = subpath.join("debian");
    #[cfg(feature = "detect-update-changelog")]
    let run_gbp_dch = {
        let cl_behaviour = debian_analyzer::detect_gbp_dch::guess_update_changelog(
            local_tree,
            debian_path.as_path(),
            None,
        );
        match cl_behaviour {
            Some(cl_behaviour) => cl_behaviour.update_changelog,
            None => true,
        }
    };
    #[cfg(not(feature = "detect-update-changelog"))]
    let run_gbp_dch = false;
    if run_gbp_dch {
        match crate::debian::gbp_dch(local_tree.abspath(subpath).unwrap().as_path()) {
            Ok(_) => {}
            Err(_) => {
                // TODO(jelmer): gbp dch sometimes fails when there is no existing
                // open changelog entry; it fails invoking
                // "dpkg --lt None <old-version>"
                return Err(PrepareUploadError::GbpDchFailed);
            }
        }
        local_tree
            .build_commit()
            .message("update changelog\n\nGbp-Dch: Ignore")
            .specific_files(&[&debian_path.join("changelog")])
            .commit()
            .unwrap();
    }
    let (cl, _top_level) = debian_analyzer::changelog::find_changelog(
        local_tree,
        std::path::Path::new(""),
        Some(false),
    )
    .map_err(|e| match e {
        debian_analyzer::changelog::FindChangelogError::MissingChangelog(..) => {
            PrepareUploadError::MissingChangelog
        }
        debian_analyzer::changelog::FindChangelogError::AddChangelog(..) => {
            panic!("changelog not versioned - should never happen");
        }
        debian_analyzer::changelog::FindChangelogError::ChangelogParseError(reason) => {
            PrepareUploadError::ChangelogParseError(reason)
        }
        debian_analyzer::changelog::FindChangelogError::BrzError(o) => {
            PrepareUploadError::BrzError(o)
        }
    })?;

    let first_block = match cl.iter().next() {
        Some(e) => e,
        None => {
            return Err(PrepareUploadError::NoUnuploadedChanges(
                last_uploaded_version.unwrap().clone(),
            ));
        }
    };
    if let Some(last_uploaded_version) = last_uploaded_version {
        if let Some(first_version) = first_block.version() {
            if first_version == *last_uploaded_version {
                return Err(PrepareUploadError::NoUnuploadedChanges(first_version));
            }
        }

        if let Some(previous_version_in_branch) =
            debian_analyzer::changelog::find_previous_upload(&cl)
        {
            if *last_uploaded_version > previous_version_in_branch {
                return Err(PrepareUploadError::LastUploadMoreRecent(
                    last_uploaded_version.clone(),
                    previous_version_in_branch,
                ));
            }
        }
    }

    if let Some(last_uploaded_version) = last_uploaded_version {
        log::info!("Checking revisions since {}", last_uploaded_version);
    }
    let lock = local_tree.lock_read();
    let last_release_revid: RevisionId = if let Some(last_uploaded_version) = last_uploaded_version
    {
        match find_last_release_revid(local_tree.branch().as_ref(), last_uploaded_version) {
            Ok(revid) => revid,
            Err(BrzError::NoSuchTag(..)) => {
                return Err(PrepareUploadError::LastReleaseRevisionNotFound(
                    pkg.to_string(),
                    last_uploaded_version.clone(),
                ));
            }
            Err(e) => {
                panic!("Unexpected error: {:?}", e);
            }
        }
    } else {
        breezyshim::revisionid::RevisionId::null()
    };
    let graph = local_tree.branch().repository().get_graph();
    let revids = graph
        .iter_lefthand_ancestry(
            &local_tree.branch().last_revision(),
            Some(&[last_release_revid]),
        )
        .collect::<Result<Vec<RevisionId>, _>>()
        .unwrap();
    if revids.is_empty() {
        log::info!("No pending changes");
        return Err(PrepareUploadError::NoUnuploadedChanges(
            first_block.version().unwrap(),
        ));
    }
    if let Some(gpg_strategy) = gpg_strategy {
        log::info!("Verifying GPG signatures...");
        let result = breezyshim::gpg::bulk_verify_signatures(
            &local_tree.branch().repository(),
            revids.iter().collect::<Vec<_>>().as_slice(),
            &gpg_strategy,
        )
        .unwrap();
        for (revid, result) in result {
            if !result.is_valid() {
                return Err(PrepareUploadError::NoValidGpgSignature(revid, result));
            }
        }
    }
    for (_revid, rev) in local_tree.branch().repository().iter_revisions(revids) {
        if let Some(rev) = rev {
            check_revision(&rev, min_commit_age, allowed_committers)
                .map_err(PrepareUploadError::Rejected)?;
        }
    }

    if first_block.is_unreleased().unwrap_or(false) {
        return Err(PrepareUploadError::NoUnreleasedChanges(
            first_block.version().unwrap(),
        ));
    }
    std::mem::drop(lock);
    let mut qa_upload = false;
    #[allow(unused_mut)]
    let mut team_upload = false;
    let control_path = local_tree
        .abspath(debian_path.join("control").as_path())
        .unwrap();
    let mut f = local_tree.get_file_text(control_path.as_path()).unwrap();
    let control =
        debian_control::Control::from_str(std::str::from_utf8_mut(f.as_mut_slice()).unwrap())
            .unwrap();
    let source = control.source().unwrap();
    let maintainer = source.maintainer().unwrap();
    let (_, e) = debian_changelog::parseaddr(&maintainer);
    if e == "packages@qa.debian.org" {
        qa_upload = true;
        // TODO(jelmer): Check whether this is a team upload
        // TODO(jelmer): determine whether this is a NMU upload
    }
    if qa_upload || team_upload {
        let changelog_path = local_tree.abspath(&debian_path.join("changelog")).unwrap();
        let f = local_tree.get_file(changelog_path.as_path()).unwrap();
        let cl = debian_changelog::ChangeLog::read_relaxed(f).unwrap();
        let message = if qa_upload {
            Some("QA Upload.")
        } else if team_upload {
            Some("Team Upload.")
        } else {
            None
        };
        if let Some(message) = message {
            cl.iter().next().unwrap().ensure_first_line("Team upload.");
            local_tree
                .put_file_bytes_non_atomic(changelog_path.as_path(), cl.to_string().as_bytes())
                .unwrap();
            // TODO: Use NullCommitReporter
            local_tree
                .build_commit()
                .message(&format!("Mention {}", message))
                .allow_pointless(true)
                .specific_files(&[debian_path.join("changelog").as_path()])
                .commit()
                .unwrap();
        }
    }
    let tag_name = match breezyshim::debian::release::release(local_tree, subpath) {
        Ok(tag_name) => tag_name,
        Err(breezyshim::debian::release::ReleaseError::GeneratedFile) => {
            return Err(PrepareUploadError::GeneratedChangelogFile);
        }
        Err(e) => {
            panic!("Unexpected error: {:?}", e);
        }
    };
    let target_dir = tempfile::tempdir().unwrap();
    if let Some(last_uploaded_version) = last_uploaded_version {
        builder = builder.replace(
            "${LAST_VERSION}",
            last_uploaded_version.to_string().as_str(),
        );
    }
    let target_changes = breezyshim::debian::build_helper(
        local_tree,
        subpath,
        local_tree.branch().as_ref(),
        target_dir.path(),
        builder.as_str(),
        false,
        apt,
    )
    .map_err(|e| match e {
        DebianError::BrzError(o) => PrepareUploadError::BrzError(o),
        DebianError::MissingUpstreamTarball { package, version } => {
            PrepareUploadError::MissingUpstreamTarball(package, version)
        }
        DebianError::PackageVersionNotPresent { package, version } => {
            PrepareUploadError::PackageVersionNotPresent(package, version)
        }
        DebianError::BuildFailed => PrepareUploadError::BuildFailed,
        e => PrepareUploadError::DebianError(e),
    })?;
    let source = target_changes.get("source").unwrap();
    debsign(std::path::Path::new(&source), None).unwrap();
    Ok((source.into(), Some(tag_name)))
}

/// Process a package for upload.
pub fn process_package(
    apt_repo: &dyn Apt,
    package: &str,
    builder: &str,
    exclude: Option<&[String]>,
    autopkgtest_only: bool,
    gpg_verification: bool,
    acceptable_keys: Option<&[String]>,
    _debug: bool,
    diff: bool,
    min_commit_age: Option<i64>,
    allowed_committers: Option<&[String]>,
    vcs_type: Option<&str>,
    vcs_url: Option<&str>,
    source_name: Option<&str>,
    archive_version: Option<&debversion::Version>,
    verify_command: Option<&str>,
) -> Result<(), UploadPackageError> {
    let mut archive_version = archive_version.cloned();
    let mut source_name = source_name.map(|s| s.to_string());
    let mut vcs_type = vcs_type.map(|s| s.to_string());
    let mut vcs_url = vcs_url.map(|s| s.to_string());
    let exclude = exclude.unwrap_or(&[]);
    log::info!("Processing {}", package);
    // Can't use open_packaging_branch here, since we want to use pkg_source later on.
    let mut has_testsuite;
    if !package.contains('/') {
        let pkg_source = match crate::debian::apt_get_source_package(apt_repo, package) {
            Some(pkg_source) => pkg_source,
            None => {
                log::info!("{}: package not found in apt", package);
                return Err(UploadPackageError::ProcessingFailure(
                    "not-in-apt".to_string(),
                    Some("Package not found in apt".to_string()),
                ));
            }
        };
        if vcs_type.is_none() || vcs_url.is_none() {
            (vcs_type, vcs_url) = match debian_analyzer::vcs::vcs_field(&pkg_source) {
                Some((t, u)) => (Some(t), Some(u)),
                None => {
                    log::info!(
                        "{}: no declared vcs location, skipping",
                        pkg_source.package().unwrap()
                    );
                    return Err(UploadPackageError::ProcessingFailure(
                        "not-in-vcs".to_string(),
                        Some("No declared vcs location".to_string()),
                    ));
                }
            };
        }
        source_name = Some(source_name.unwrap_or_else(|| pkg_source.package().unwrap()));
        if exclude.contains(source_name.as_ref().unwrap()) {
            return Err(UploadPackageError::Ignored("excluded".to_string(), None));
        }
        archive_version = Some(archive_version.unwrap_or_else(|| pkg_source.version().unwrap()));
        has_testsuite = Some(pkg_source.testsuite().is_some());
    } else {
        vcs_url = Some(vcs_url.unwrap_or(package.to_owned()));
        has_testsuite = None;
    }
    let parsed_vcs: debian_control::vcs::ParsedVcs = vcs_url.as_ref().unwrap().parse().unwrap();
    let location: url::Url = parsed_vcs.repo_url.parse().unwrap();
    let branch_name = parsed_vcs.branch;
    let subpath = std::path::PathBuf::from(parsed_vcs.subpath.unwrap_or("".to_string()));
    let probers = crate::probers::select_probers(vcs_type.as_deref());
    let main_branch = match open_branch(
        &location,
        None,
        Some(
            probers
                .iter()
                .map(|p| p.as_ref())
                .collect::<Vec<_>>()
                .as_slice(),
        ),
        branch_name.as_deref(),
    ) {
        Ok(b) => b,
        Err(
            BranchOpenError::Unavailable { description, .. }
            | BranchOpenError::TemporarilyUnavailable { description, .. },
        ) => {
            log::info!(
                "{}: branch unavailable: {}",
                vcs_url.as_ref().unwrap(),
                description
            );
            return Err(UploadPackageError::ProcessingFailure(
                "vcs-inaccessible".to_string(),
                Some(format!("Unable to access vcs: {:?}", description)),
            ));
        }
        Err(BranchOpenError::RateLimited {
            url: _,
            description: _,
            retry_after,
        }) => {
            log::info!(
                "{}: rate limited by server (retrying after {})",
                vcs_url.unwrap(),
                retry_after.map_or("unknown".to_string(), |i| i.to_string())
            );
            return Err(UploadPackageError::ProcessingFailure(
                "rate-limited".to_string(),
                Some(format!(
                    "Rate limited by server (retrying after {})",
                    retry_after.map_or("unknown".to_string(), |i| i.to_string())
                )),
            ));
        }
        Err(BranchOpenError::Missing { description, .. }) => {
            log::info!("{}: branch not found: {}", vcs_url.unwrap(), description);
            return Err(UploadPackageError::ProcessingFailure(
                "vcs-inaccessible".to_string(),
                Some(format!("Unable to access vcs: {:?}", description)),
            ));
        }
        Err(BranchOpenError::Other(description)) => {
            log::info!(
                "{}: error opening branch: {}",
                vcs_url.unwrap(),
                description
            );
            return Err(UploadPackageError::ProcessingFailure(
                "vcs-error".to_string(),
                Some(format!("Unable to access vcs: {:?}", description)),
            ));
        }
        Err(BranchOpenError::Unsupported { description, .. }) => {
            log::info!("{}: branch not found: {}", vcs_url.unwrap(), description);
            return Err(UploadPackageError::ProcessingFailure(
                "vcs-unsupported".to_string(),
                Some(format!("Unable to access vcs: {:?}", description)),
            ));
        }
    };
    let mut ws_builder = crate::workspace::Workspace::builder();
    ws_builder = ws_builder.additional_colocated_branches(
        crate::debian::pick_additional_colocated_branches(main_branch.as_ref()),
    );
    let ws = ws_builder.main_branch(main_branch).build().unwrap();
    if source_name.is_none() {
        let control_path = subpath.join("debian/control");
        let control_text = ws
            .local_tree()
            .get_file_text(control_path.as_path())
            .unwrap();
        let control = debian_control::Control::from_str(
            std::str::from_utf8(control_text.as_slice()).unwrap(),
        )
        .unwrap();
        let source_name = control.source().unwrap().name().unwrap();
        let pkg_source = match crate::debian::apt_get_source_package(apt_repo, &source_name) {
            Some(p) => p,
            None => {
                log::info!("{}: package not found in apt", package);
                return Err(UploadPackageError::ProcessingFailure(
                    "not-in-apt".to_owned(),
                    Some("Package not found in apt".to_owned()),
                ));
            }
        };
        archive_version = pkg_source.version();
        has_testsuite = Some(control.source().unwrap().testsuite().is_some());
    }
    let has_testsuite = has_testsuite.unwrap();
    let source_name = source_name.unwrap();
    if exclude.contains(&source_name) {
        return Err(UploadPackageError::Ignored("excluded".to_string(), None));
    }
    if autopkgtest_only
        && !has_testsuite
        && !ws
            .local_tree()
            .has_filename(&subpath.join("debian/tests/control"))
    {
        log::info!("{}: Skipping, package has no autopkgtest.", source_name);
        return Err(UploadPackageError::Ignored(
            "no-autopkgtest".to_owned(),
            None,
        ));
    }
    let branch_config = ws.local_tree().branch().get_config();
    let gpg_strategy = if gpg_verification {
        let gpg_strategy = breezyshim::gpg::GPGStrategy::new(&branch_config);
        let acceptable_keys = if let Some(acceptable_keys) = acceptable_keys {
            acceptable_keys.iter().map(|s| s.to_string()).collect()
        } else {
            #[cfg(feature = "gpg")]
            {
                let mut context = gpgme::Context::from_protocol(gpgme::Protocol::OpenPgp).unwrap();
                get_maintainer_keys(&mut context).unwrap()
            }
            #[cfg(not(feature = "gpg"))]
            {
                vec![]
            }
        };
        gpg_strategy.set_acceptable_keys(acceptable_keys.as_slice());
        Some(gpg_strategy)
    } else {
        None
    };

    let (target_changes, tag_name) = match prepare_upload_package(
        ws.local_tree(),
        std::path::Path::new(&subpath),
        &source_name,
        archive_version.as_ref(),
        builder,
        gpg_strategy,
        min_commit_age,
        allowed_committers,
        Some(apt_repo),
    ) {
        Ok(r) => r,
        Err(PrepareUploadError::GbpDchFailed) => {
            log::warn!("{}: 'gbp dch' failed to run", source_name);
            return Err(UploadPackageError::ProcessingFailure(
                "gbp-dch-failed".to_string(),
                None,
            ));
        }
        Err(PrepareUploadError::MissingUpstreamTarball(package, version)) => {
            log::warn!(
                "{}: missing upstream tarball: {} {}",
                source_name,
                package,
                version
            );
            return Err(UploadPackageError::ProcessingFailure(
                "missing-upstream-tarball".to_string(),
                Some(format!("Missing upstream tarball: {} {}", package, version)),
            ));
        }
        Err(PrepareUploadError::Rejected(RevisionRejected::CommitterNotAllowed(
            committer,
            allowed_committers,
        ))) => {
            log::warn!(
                "{}: committer {} not in allowed list: {:?}",
                source_name,
                committer,
                allowed_committers,
            );
            return Err(UploadPackageError::Ignored(
                "committer-not-allowed".to_string(),
                Some(format!(
                    "committer {} not in allowed list: {:?}",
                    committer, allowed_committers
                )),
            ));
        }
        Err(PrepareUploadError::BuildFailed) => {
            log::warn!("{}: package failed to build", source_name);
            return Err(UploadPackageError::ProcessingFailure(
                "build-failed".to_string(),
                None,
            ));
        }
        Err(PrepareUploadError::LastReleaseRevisionNotFound(source_name, version)) => {
            log::warn!(
                "{}: Unable to find revision matching last release {}, skipping.",
                source_name,
                version,
            );
            return Err(UploadPackageError::ProcessingFailure(
                "last-release-missing".to_string(),
                Some(format!(
                    "Unable to find revision matching last release {}",
                    version
                )),
            ));
        }
        Err(PrepareUploadError::LastUploadMoreRecent(archive_version, vcs_version)) => {
            log::warn!(
                "{}: Last upload ({}) was more recent than VCS ({})",
                source_name,
                archive_version,
                vcs_version,
            );
            return Err(UploadPackageError::ProcessingFailure(
                "last-upload-not-in-vcs".to_string(),
                Some(format!(
                    "Last upload ({}) was more recent than VCS ({})",
                    archive_version, vcs_version
                )),
            ));
        }
        Err(PrepareUploadError::ChangelogParseError(reason)) => {
            log::info!("{}: Error parsing changelog: {}", source_name, reason);
            return Err(UploadPackageError::ProcessingFailure(
                "changelog-parse-error".to_string(),
                Some(reason),
            ));
        }
        Err(PrepareUploadError::MissingChangelog) => {
            log::info!("{}: No changelog found, skipping.", source_name);
            return Err(UploadPackageError::ProcessingFailure(
                "missing-changelog".to_string(),
                None,
            ));
        }
        Err(PrepareUploadError::GeneratedChangelogFile) => {
            log::info!(
                "{}: Changelog is generated and unable to update, skipping.",
                source_name,
            );
            return Err(UploadPackageError::ProcessingFailure(
                "generated-changelog".to_string(),
                None,
            ));
        }
        Err(PrepareUploadError::Rejected(RevisionRejected::RecentCommits(
            commit_age,
            _max_commit_age,
        ))) => {
            log::info!(
                "{}: Recent commits ({} days), skipping.",
                source_name,
                commit_age,
            );
            return Err(UploadPackageError::Ignored(
                "recent-commits".to_string(),
                Some(format!("Recent commits ({} days)", commit_age)),
            ));
        }
        Err(PrepareUploadError::NoUnuploadedChanges(_version)) => {
            log::info!("{}: No unuploaded changes, skipping.", source_name,);
            return Err(UploadPackageError::Ignored(
                "no-unuploaded-changes".to_string(),
                Some("No unuploaded changes".to_string()),
            ));
        }
        Err(PrepareUploadError::NoUnreleasedChanges(_version)) => {
            log::info!("{}: No unreleased changes, skipping.", source_name,);
            return Err(UploadPackageError::Ignored(
                "no-unreleased-changes".to_string(),
                Some("No unreleased changes".to_string()),
            ));
        }
        Err(PrepareUploadError::MissingNestedTree(_)) => {
            log::error!("{}: missing nested tree", source_name);
            return Err(UploadPackageError::ProcessingFailure(
                "missing-nested-tree".to_string(),
                None,
            ));
        }
        Err(PrepareUploadError::BrzError(e)) => {
            log::error!("{}: error: {:?}", source_name, e);
            return Err(UploadPackageError::ProcessingFailure(
                "vcs-error".to_string(),
                Some(format!("{:?}", e)),
            ));
        }
        Err(PrepareUploadError::DebianError(e)) => {
            log::error!("{}: error: {:?}", source_name, e);
            return Err(UploadPackageError::ProcessingFailure(
                "debian-error".to_string(),
                Some(format!("{:?}", e)),
            ));
        }
        Err(PrepareUploadError::NoValidGpgSignature(revid, _code)) => {
            log::info!(
                "{}: No valid GPG signature for revision {}",
                source_name,
                revid
            );
            return Err(UploadPackageError::ProcessingFailure(
                "no-valid-gpg-signature".to_string(),
                Some(format!("No valid GPG signature for revision {}", revid)),
            ));
        }
        Err(PrepareUploadError::PackageVersionNotPresent(package, version)) => {
            log::warn!(
                "{}: package version {} not present in repository",
                package,
                version
            );
            return Err(UploadPackageError::ProcessingFailure(
                "package-version-not-present".to_string(),
                Some(format!(
                    "Package version {} not present in repository",
                    version
                )),
            ));
        }
    };

    if let Some(verify_command) = verify_command {
        match std::process::Command::new(verify_command)
            .arg(&target_changes)
            .status()
        {
            Ok(o) => {
                if o.code() == Some(1) {
                    return Err(UploadPackageError::Ignored(
                        "verify-command-declined".to_string(),
                        Some(format!(
                            "{}: Verify command {} declined upload",
                            source_name, verify_command
                        )),
                    ));
                } else if o.code() != Some(0) {
                    return Err(UploadPackageError::ProcessingFailure(
                        "verify-command-error".to_string(),
                        Some(format!(
                            "{}: Error running verify command {}: returncode {}",
                            source_name,
                            verify_command,
                            o.code().unwrap()
                        )),
                    ));
                }
            }
            Err(e) => {
                return Err(UploadPackageError::ProcessingFailure(
                    "verify-command-error".to_string(),
                    Some(format!(
                        "{}: Error running verify command {}: {}",
                        source_name, verify_command, e
                    )),
                ));
            }
        }
    }

    let mut tags = HashMap::new();
    if let Some(tag_name) = tag_name.as_ref() {
        log::info!("Pushing tag {}", tag_name);
        tags.insert(
            tag_name.to_string(),
            ws.local_tree()
                .branch()
                .tags()
                .unwrap()
                .lookup_tag(tag_name)
                .unwrap(),
        );
    }
    match ws.push(Some(tags)) {
        Ok(_) => {}
        Err(crate::workspace::Error::PermissionDenied(..)) => {
            log::info!(
                "{}: Permission denied pushing to branch, skipping.",
                source_name,
            );
            return Err(UploadPackageError::ProcessingFailure(
                "vcs-permission-denied".to_string(),
                None,
            ));
        }
        Err(e) => {
            log::error!("{}: Error pushing: {}", source_name, e);
            return Err(UploadPackageError::ProcessingFailure(
                "push-error".to_string(),
                Some(format!("{:?}", e)),
            ));
        }
    }
    dput_changes(&target_changes).unwrap();
    if diff {
        ws.show_diff(Box::new(std::io::stdout()), None, None)
            .unwrap();
    }
    std::mem::drop(ws);
    Ok(())
}
