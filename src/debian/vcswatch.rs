use chrono::{DateTime, Duration, FixedOffset, Utc};
use flate2::read::GzDecoder;
use log::{debug, error, info, warn};
use reqwest::header::USER_AGENT;
use std::collections::HashMap;
use std::error::Error;
use std::io::Read;

#[derive(Debug)]
enum PackageProcessingFailure {
    NotInVCS,
    VCSInaccessible,
}

impl ToString for PackageProcessingFailure {
    fn to_string(&self) -> String {
        match self {
            PackageProcessingFailure::NotInVCS => "not-in-vcs".to_string(),
            PackageProcessingFailure::VCSInaccessible => "vcs-inaccessible".to_string(),
        }
    }
}

#[derive(Debug)]
enum PackageIgnored {
    Excluded,
    NoUnuploadedChanges,
    CommitterNotAllowed(String),
    RecentCommits(i64, i64),
}

impl ToString for PackageIgnored {
    fn to_string(&self) -> String {
        match self {
            PackageIgnored::Excluded => "excluded".to_string(),
            PackageIgnored::NoUnuploadedChanges => "no-unuploaded-changes".to_string(),
            PackageIgnored::CommitterNotAllowed(_) => "committer-not-allowed".to_string(),
            PackageIgnored::RecentCommits(_, _) => "recent-commits".to_string(),
        }
    }
}

enum PackageStatus {
    Ignored(PackageIgnored),
    Failure(PackageProcessingFailure),
    Success(Option<chrono::DateTime<chrono::FixedOffset>>),
}

type IncStats = Box<dyn Fn(&str)>;

#[derive(Debug, serde::Deserialize)]
pub struct VcswatchPackage {
    package: String,
    url: Option<String>,
    vcs: Option<String>,
    commits: Option<usize>,
    status: Option<String>,
    error: Option<String>,
    last_scan: Option<String>,
    vcslog: Option<String>,
}

fn vcswatch_prescan_package(
    vw: &VcswatchPackage,
    exclude: Option<&Vec<&str>>,
    min_commit_age: Option<i64>,
    allowed_committers: Option<&Vec<&str>>,
) -> Result<PackageStatus, Box<dyn Error>> {
    if let Some(exclude_list) = exclude {
        if exclude_list.contains(&vw.package.as_str()) {
            return Ok(PackageStatus::Ignored(PackageIgnored::Excluded));
        }
    }

    if vw.url.is_none() || vw.vcs.is_none() {
        return Ok(PackageStatus::Failure(PackageProcessingFailure::NotInVCS));
    }

    // Similar TODO: check autopkgtest_only ?

    if vw.commits == Some(0) {
        return Ok(PackageStatus::Ignored(PackageIgnored::NoUnuploadedChanges));
    }

    if vw.status.as_deref() == Some("ERROR") {
        warn!(
            "vcswatch: unable to access {}: {}",
            vw.package,
            vw.error.as_ref().unwrap()
        );
        return Ok(PackageStatus::Failure(
            PackageProcessingFailure::VCSInaccessible,
        ));
    }

    if let Some(last_scan) = vw.last_scan.as_ref() {
        debug!("vcswatch last scanned at: {}", last_scan);
    }

    if vw.vcs.as_deref() == Some("Git") {
        if let Some(vcslog) = vw.vcslog.as_ref() {
            check_git_commits(vcslog, min_commit_age, allowed_committers)
        } else {
            Ok(PackageStatus::Success(None))
        }
    } else {
        Ok(PackageStatus::Success(None))
    }
}

pub fn vcswatch_prescan_packages<'a>(
    packages: Vec<&'a str>,
    inc_stats: IncStats,
    exclude: Option<Vec<&str>>,
    min_commit_age: Option<i64>,
    allowed_committers: Option<Vec<&str>>,
) -> Result<(Vec<&'a str>, i32, HashMap<String, VcswatchPackage>), Box<dyn Error>> {
    info!("Using vcswatch to prescan {} packages", packages.len());

    let url = "https://qa.debian.org/data/vcswatch/vcswatch.json.gz";
    let client = reqwest::blocking::Client::new();
    let res = client
        .get(url)
        .header(USER_AGENT, "silver-platter")
        .send()?;
    let decoder = GzDecoder::new(res);
    let mut reader = std::io::BufReader::new(decoder);
    let mut string = String::new();
    reader.read_to_string(&mut string)?;

    let vcswatch_data: Vec<VcswatchPackage> = serde_json::from_str(&string)?;
    let mut vcswatch = HashMap::new();
    for item in vcswatch_data {
        vcswatch.insert(item.package.clone(), item);
    }

    let mut by_ts = HashMap::new();
    let mut failures = 0;

    for package in &packages {
        if let Some(vw) = vcswatch.get(*package) {
            match vcswatch_prescan_package(
                vw,
                exclude.as_ref(),
                min_commit_age,
                allowed_committers.as_ref(),
            ) {
                Ok(PackageStatus::Success(ts)) => {
                    if let Some(ts_value) = ts {
                        by_ts.insert(*package, ts_value);
                    }
                }
                Ok(PackageStatus::Failure(e)) => {
                    inc_stats(e.to_string().as_str());
                    failures += 1;
                }
                Ok(PackageStatus::Ignored(e)) => {
                    inc_stats(e.to_string().as_str());
                }
                Err(e) => {
                    inc_stats("unknown-error");
                    error!("vcswatch: error processing {}: {}", package, e);
                }
            }
        }
    }

    let mut by_ts = by_ts.into_iter().collect::<Vec<_>>();
    by_ts.sort_by(|a, b| b.1.cmp(&a.1));
    let sorted_by_ts: Vec<&str> = by_ts.iter().map(|(k, _v)| *k).collect::<Vec<_>>();

    Ok((sorted_by_ts, failures, vcswatch))
}

struct GitRevision {
    #[allow(dead_code)]
    commit_id: String,
    headers: HashMap<String, String>,
    #[allow(dead_code)]
    message: Vec<String>,
}

impl GitRevision {
    fn committer(&self) -> Option<&String> {
        self.headers.get("Committer").or(self.headers.get("Author"))
    }

    fn timestamp(&self) -> Option<DateTime<FixedOffset>> {
        if let Some(datestr) = self.headers.get("Date") {
            if let Ok(dt) = DateTime::parse_from_str(datestr, "%a %b %d %H:%M:%S %Y %z") {
                return Some(dt);
            }
        }
        None
    }

    // Corresponds to the from_lines class method in Python
    fn from_lines(lines: Vec<String>) -> Self {
        let mut commit_id = String::new();
        let mut message = vec![];
        let mut headers = HashMap::new();

        for (i, line) in lines.iter().enumerate() {
            if let Some(cid) = line.strip_prefix("commit ") {
                commit_id = cid.to_string();
            } else if line.is_empty() {
                message = lines[i + 1..].to_vec();
                break;
            } else if let Some((name, value)) = line.split_once(": ") {
                headers.insert(name.to_string(), value.to_string());
            }
        }

        Self {
            commit_id,
            headers,
            message,
        }
    }
}

pub enum RevisionStatus {
    RecentCommits(usize, usize),
    CommitterNotAllowed(String),
    Ok,
}

fn check_revision(
    rev: &GitRevision,
    min_commit_age: Option<i64>,
    allowed_committers: Option<&Vec<&str>>,
) -> Result<PackageStatus, Box<dyn std::error::Error>> {
    // Check min_commit_age
    if let Some(min_commit_age) = min_commit_age {
        let time_delta: Duration = Utc::now() - rev.timestamp().unwrap().with_timezone(&Utc);
        if time_delta.num_days() < min_commit_age {
            return Ok(PackageStatus::Ignored(PackageIgnored::RecentCommits(
                time_delta.num_days(),
                min_commit_age,
            )));
        }
    }

    let committer_email = if let Some(committer) = rev.committer() {
        if let Some((_, _name, email)) =
            lazy_regex::regex_captures!(r"^(?P<name>.*) <(?P<email>.*)>$", committer.as_str())
        {
            email
        } else {
            committer
        }
    } else {
        warn!("Unable to extract email from {:?}", rev.committer());
        return Ok(PackageStatus::Ignored(PackageIgnored::CommitterNotAllowed(
            rev.committer().unwrap_or(&String::from("")).to_string(),
        )));
    };

    // Check if committer is allowed
    if let Some(allowed_committers) = allowed_committers {
        if !allowed_committers.contains(&committer_email) {
            return Ok(PackageStatus::Ignored(PackageIgnored::CommitterNotAllowed(
                committer_email.to_string(),
            )));
        }
    }

    Ok(PackageStatus::Success(rev.timestamp()))
}

fn parse_git_vcslog(vcslog: &str) -> Result<Vec<GitRevision>, Box<dyn std::error::Error>> {
    let mut lines = vec![];
    let mut revisions = vec![];

    for line in vcslog.lines() {
        if line.is_empty() && lines.last().unwrap_or(&String::new()).trim().is_empty() {
            let gitrev = GitRevision::from_lines(lines.clone());
            revisions.push(gitrev);
            lines = vec![];
        } else {
            lines.push(line.to_string());
        }
    }

    if !lines.is_empty() {
        let gitrev = GitRevision::from_lines(lines);
        revisions.push(gitrev);
    }

    Ok(revisions)
}

fn check_git_commits(
    vcslog: &str,
    min_commit_age: Option<i64>,
    allowed_committers: Option<&Vec<&str>>,
) -> Result<PackageStatus, Box<dyn std::error::Error>> {
    let mut last_commit_ts = None;

    for gitrev in parse_git_vcslog(vcslog)? {
        match check_revision(&gitrev, min_commit_age, allowed_committers)? {
            PackageStatus::Success(ts) => {
                if let Some(ts_value) = ts {
                    last_commit_ts = Some(ts_value);
                }
            }
            PackageStatus::Failure(e) => {
                return Ok(PackageStatus::Failure(e));
            }
            PackageStatus::Ignored(e) => {
                return Ok(PackageStatus::Ignored(e));
            }
        }
    }

    Ok(PackageStatus::Success(last_commit_ts))
}
