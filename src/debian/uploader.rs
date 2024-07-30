use breezyshim::branch::Branch;
use breezyshim::debian::apt::{Apt, LocalApt, RemoteApt};
use breezyshim::revisionid::RevisionId;
use debversion::Version;
use std::collections::HashMap;
use std::path::Path;

#[cfg(feature = "last-attempt-db")]
use trivialdb as tdb;

#[cfg(feature = "last-attempt-db")]
pub struct LastAttemptDatabase {
    db: tdb::Tdb,
}

#[cfg(feature = "last-attempt-db")]
impl LastAttemptDatabase {
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

    pub fn get(&self, package: &str) -> Option<chrono::DateTime<chrono::FixedOffset>> {
        let key = package.to_string().into_bytes();
        self.db.fetch(&key).unwrap().map(|value| {
            let value = String::from_utf8(value).unwrap();
            chrono::DateTime::parse_from_rfc3339(&value).unwrap()
        })
    }

    pub fn set(&mut self, package: &str, value: chrono::DateTime<chrono::FixedOffset>) {
        let key = package.to_string().into_bytes();
        let value = value.to_rfc3339();
        self.db.store(&key, value.as_bytes(), None).unwrap();
    }

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
pub enum PackageResult {
    Ignored(String, Option<String>),
    ProcessingFailure(String, Option<String>),
}

pub fn vcswatch_prescan_package(
    _package: &str,
    vw: &VcswatchEntry,
    exclude: Option<&[String]>,
    min_commit_age: Option<i64>,
    allowed_committers: Option<&[String]>,
) -> Result<Option<chrono::DateTime<chrono::Utc>>, PackageResult> {
    if let Some(exclude) = exclude {
        if exclude.contains(&vw.package) {
            return Err(PackageResult::Ignored(
                "excluded".to_string(),
                Some("Excluded".to_string()),
            ));
        }
    }
    if vw.url.is_none() || vw.vcs.is_none() {
        return Err(PackageResult::ProcessingFailure(
            "not-in-vcs".to_string(),
            Some("Not in VCS".to_string()),
        ));
    }
    // TODO(jelmer): check autopkgtest_only ?
    // from debian.deb822 import Deb822
    // pkg_source = Deb822(vw.controlfile)
    // has_testsuite = "Testsuite" in pkg_source
    if vw.commits == 0 {
        return Err(PackageResult::Ignored(
            "no-unuploaded-changes".to_string(),
            Some("No unuploaded changes".to_string()),
        ));
    }
    if vw.status.as_deref() == Some("ERROR") {
        log::warn!("vcswatch: unable to access {}: {:?}", vw.package, vw.error);
        return Err(PackageResult::ProcessingFailure(
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
                    return Err(PackageResult::Ignored(
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
                    return Err(PackageResult::Ignored(
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

pub enum RevisionRejected {
    CommitterNotAllowed(String, Vec<String>),
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
pub struct VcswatchEntry {
    package: String,
    vcslog: Option<String>,
    commits: usize,
    url: Option<String>,
    last_scan: Option<String>,
    status: Option<String>,
    error: Option<String>,
    vcs: Option<String>,
    archive_version: Option<debversion::Version>,
}

pub fn vcswatch_prescan_packages(
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
            Err(PackageResult::ProcessingFailure(reason, _description)) => {
                inc_stats(reason.as_str());
                failures += 1;
            }
            Err(PackageResult::Ignored(reason, _description)) => {
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

pub fn find_last_release_revid(branch: &dyn Branch, version: &Version) -> RevisionId {
    use pyo3::prelude::*;
    pyo3::Python::with_gil(|py| -> PyResult<RevisionId> {
        let m = py.import_bound("breezy.plugins.debian.import_dsc")?;
        let dbc = m.getattr("DistributionBranch")?;
        let dbc = dbc.call1((branch.to_object(py), py.None()))?;

        dbc.call_method1("revid_of_version", (version.to_object(py),))?
            .extract::<RevisionId>()
    })
    .unwrap()
}

pub fn process_package(
    apt_repo: &dyn Apt,
    package: &str,
    builder: &str,
    exclude: Option<&[String]>,
    autopkgtest_only: bool,
    gpg_verification: bool,
    acceptable_keys: Option<&[String]>,
    debug: bool,
    diff: bool,
    min_commit_age: usize,
    allowed_committers: Option<&[String]>,
    vcs_type: Option<&str>,
    vcs_url: Option<&str>,
    source_name: Option<&str>,
    archive_version: Option<&debversion::Version>,
    verify_command: Option<&str>,
) -> Result<(), PackageResult> {
    use pyo3::prelude::*;
    pyo3::import_exception!(silver_platter.debian.uploader, PackageIgnored);
    pyo3::import_exception!(silver_platter.debian.uploader, PackageProcessingFailure);
    Python::with_gil(|py| {
        let m = py.import_bound("silver_platter.debian.uploader").unwrap();
        let process_package = m.getattr("process_package").unwrap();
        let kwargs = pyo3::types::PyDict::new_bound(py);
        kwargs.set_item("debug", debug).unwrap();
        kwargs.set_item("diff", diff).unwrap();
        kwargs.set_item("verify_command", verify_command).unwrap();
        kwargs.set_item("source_name", source_name).unwrap();
        kwargs.set_item("archive_version", archive_version).unwrap();
        kwargs.set_item("vcs_url", vcs_url).unwrap();
        kwargs.set_item("vcs_type", vcs_type).unwrap();
        kwargs
            .set_item("allowed_committers", allowed_committers)
            .unwrap();
        kwargs.set_item("min_commit_age", min_commit_age).unwrap();
        kwargs
            .set_item("autopkgtest_only", autopkgtest_only)
            .unwrap();
        kwargs.set_item("exclude", exclude).unwrap();
        kwargs.set_item("builder", builder).unwrap();
        kwargs.set_item("acceptable_keys", acceptable_keys).unwrap();
        kwargs
            .set_item("gpg_verification", gpg_verification)
            .unwrap();
        kwargs.set_item("apt_repo", apt_repo.to_object(py)).unwrap();
        kwargs.set_item("package", package).unwrap();
        match process_package.call((), Some(&kwargs)) {
            Ok(_) => Ok(()),
            Err(e) if e.is_instance_of::<PackageIgnored>(py) => {
                let value = e.value_bound(py);
                let reason = value
                    .getattr("reason")
                    .unwrap()
                    .extract::<String>()
                    .unwrap();
                let description: Option<String> =
                    value.getattr("description").unwrap().extract().unwrap();

                Err(PackageResult::Ignored(reason, description))
            }
            Err(e) if e.is_instance_of::<PackageProcessingFailure>(py) => {
                let value = e.value_bound(py);
                let reason = value
                    .getattr("reason")
                    .unwrap()
                    .extract::<String>()
                    .unwrap();
                let description: Option<String> =
                    value.getattr("description").unwrap().extract().unwrap();

                Err(PackageResult::ProcessingFailure(reason, description))
            }
            Err(e) => Err(PackageResult::ProcessingFailure(
                "unknown".to_string(),
                Some(format!("{:?}", e)),
            )),
        }
    })
}

pub fn select_apt_packages(
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

pub fn main(
    mut packages: Vec<String>,
    acceptable_keys: Option<Vec<String>>,
    gpg_verification: bool,
    min_commit_age: usize,
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
) -> i32 {
    let mut ret = 0;

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
        return 1;
    }

    if shuffle {
        use rand::seq::SliceRandom;
        // Shuffle packages vec
        let mut rng = rand::thread_rng();
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
            Some(min_commit_age as i64),
            allowed_committers.as_deref(),
        )
        .unwrap();
        packages = new_packages;
        extra_data = Some(new_extra_data);
        if failures > 0 {
            ret = 1
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
            Err(PackageResult::ProcessingFailure(reason, _description)) => {
                inc_stats(reason.as_str());
                ret = 1;
            }
            Err(PackageResult::Ignored(reason, _description)) => inc_stats(reason.as_str()),
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
