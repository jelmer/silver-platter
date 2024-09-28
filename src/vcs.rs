//! Version control system (VCS) support.
use breezyshim::controldir::{open_containing_from_transport, open_from_transport};
use breezyshim::error::Error as BrzError;
use breezyshim::{
    get_transport, join_segment_parameters, split_segment_parameters, Branch, Prober, Transport,
};
use percent_encoding::{utf8_percent_encode, CONTROLS};

#[derive(Debug)]
/// Errors that can occur when opening a branch.
pub enum BranchOpenError {
    /// The VCS is not supported.
    Unsupported {
        /// The URL of the branch.
        url: url::Url,
        /// A description of the error.
        description: String,
        /// The VCS that is not supported.
        vcs: Option<String>,
    },
    /// The branch is missing.
    Missing {
        /// The URL of the branch.
        url: url::Url,

        /// A description of the error.
        description: String,
    },
    /// The branch is rate limited.
    RateLimited {
        /// The URL of the branch.
        url: url::Url,

        /// A description of the error.
        description: String,

        /// The time to wait before retrying.
        retry_after: Option<f64>,
    },
    /// The branch is unavailable.
    Unavailable {
        /// The URL of the branch.
        url: url::Url,

        /// A description of the error.
        description: String,
    },
    /// The branch is temporarily unavailable.
    TemporarilyUnavailable {
        /// The URL of the branch.
        url: url::Url,

        /// A description of the error.
        description: String,
    },

    /// An error occurred.
    Other(String),
}

impl std::fmt::Display for BranchOpenError {
    fn fmt(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
        match self {
            BranchOpenError::Unsupported {
                url,
                description,
                vcs,
            } => write!(
                f,
                "Unsupported VCS for {}: {} ({})",
                url,
                description,
                vcs.as_deref().unwrap_or("unknown")
            ),
            BranchOpenError::Missing { url, description } => {
                write!(f, "Missing branch {}: {}", url, description)
            }
            BranchOpenError::RateLimited {
                url,
                description,
                retry_after,
            } => write!(
                f,
                "Rate limited {}: {} (retry after: {:?})",
                url, description, retry_after
            ),
            BranchOpenError::Unavailable { url, description } => {
                write!(f, "Unavailable {}: {}", url, description)
            }
            BranchOpenError::TemporarilyUnavailable { url, description } => {
                write!(f, "Temporarily unavailable {}: {}", url, description)
            }
            BranchOpenError::Other(e) => write!(f, "Error: {}", e),
        }
    }
}

#[cfg(feature = "pyo3")]
impl From<BranchOpenError> for pyo3::PyErr {
    fn from(e: BranchOpenError) -> Self {
        use pyo3::import_exception;
        import_exception!(silver_platter, BranchUnsupported);
        import_exception!(silver_platter, BranchTemporarilyUnavailable);
        import_exception!(silver_platter, BranchUnavailable);
        import_exception!(silver_platter, BranchRateLimited);
        import_exception!(silver_platter, BranchMissing);

        use pyo3::exceptions::PyRuntimeError;
        match e {
            BranchOpenError::Unsupported {
                url,
                description,
                vcs,
            } => BranchUnsupported::new_err((url.to_string(), description, vcs)),
            BranchOpenError::Missing { url, description } => {
                BranchMissing::new_err((url.to_string(), description))
            }
            BranchOpenError::RateLimited {
                url,
                description,
                retry_after,
            } => BranchRateLimited::new_err((url.to_string(), description, retry_after)),
            BranchOpenError::Unavailable { url, description } => {
                BranchUnavailable::new_err((url.to_string(), description))
            }
            BranchOpenError::TemporarilyUnavailable { url, description } => {
                BranchTemporarilyUnavailable::new_err((url.to_string(), description))
            }
            BranchOpenError::Other(e) => PyRuntimeError::new_err((e,)),
        }
    }
}

impl BranchOpenError {
    /// Convert a BrzError to a BranchOpenError.
    pub fn from_err(url: url::Url, e: &BrzError) -> Self {
        match e {
            BrzError::NotBranchError(e, reason) => {
                let description = if let Some(reason) = reason {
                    format!("{}: {}", e, reason)
                } else {
                    e.to_string()
                };
                Self::Missing { url, description }
            }
            BrzError::DependencyNotPresent(l, e) => Self::Unavailable {
                url,
                description: format!("missing {}: {}", l, e),
            },
            BrzError::NoColocatedBranchSupport => Self::Unsupported {
                url,
                description: "no colocated branch support".to_string(),
                vcs: None,
            },
            BrzError::Socket(e) => Self::Unavailable {
                url,
                description: format!("Socket error: {}", e),
            },
            BrzError::UnsupportedProtocol(url, extra) => Self::Unsupported {
                url: url.parse().unwrap(),
                description: if let Some(extra) = extra {
                    format!("Unsupported protocol: {}", extra)
                } else {
                    "Unsupported protocol".to_string()
                },
                vcs: None,
            },
            BrzError::ConnectionError(msg) => {
                if e.to_string()
                    .contains("Temporary failure in name resolution")
                {
                    Self::TemporarilyUnavailable {
                        url,
                        description: msg.to_string(),
                    }
                } else {
                    Self::Unavailable {
                        url,
                        description: msg.to_string(),
                    }
                }
            }
            BrzError::PermissionDenied(path, extra) => Self::Unavailable {
                url,
                description: format!(
                    "Permission denied: {}: {}",
                    path.to_string_lossy(),
                    extra.as_deref().unwrap_or("")
                ),
            },
            BrzError::InvalidURL(url, extra) => Self::Unavailable {
                url: url.parse().unwrap(),
                description: extra
                    .as_ref()
                    .map(|s| s.to_string())
                    .unwrap_or_else(|| format!("Invalid URL: {}", url)),
            },
            BrzError::InvalidHttpResponse(_path, msg, _orig_error, headers) => {
                if msg.to_string().contains("Unexpected HTTP status 429") {
                    if let Some(retry_after) = headers.get("Retry-After") {
                        match retry_after.parse::<f64>() {
                            Ok(retry_after) => {
                                return Self::RateLimited {
                                    url,
                                    description: e.to_string(),
                                    retry_after: Some(retry_after),
                                };
                            }
                            Err(e) => {
                                log::warn!("Unable to parse retry-after header: {}", retry_after);
                                return Self::RateLimited {
                                    url,
                                    description: e.to_string(),
                                    retry_after: None,
                                };
                            }
                        }
                    }
                    Self::RateLimited {
                        url,
                        description: e.to_string(),
                        retry_after: None,
                    }
                } else {
                    Self::Unavailable {
                        url,
                        description: e.to_string(),
                    }
                }
            }
            BrzError::TransportError(message) => Self::Unavailable {
                url,
                description: message.to_string(),
            },
            BrzError::UnusableRedirect(source, target, reason) => Self::Unavailable {
                url,
                description: format!("Unusable redirect: {} -> {}: {}", source, target, reason),
            },
            BrzError::UnsupportedVcs(vcs) => Self::Unsupported {
                url,
                description: e.to_string(),
                vcs: Some(vcs.clone()),
            },
            BrzError::UnsupportedFormat(format) => Self::Unsupported {
                url,
                description: e.to_string(),
                vcs: Some(format.clone()),
            },
            BrzError::UnknownFormat(_format) => Self::Unsupported {
                url,
                description: e.to_string(),
                vcs: None,
            },
            BrzError::RemoteGitError(msg) => Self::Unavailable {
                url,
                description: msg.to_string(),
            },
            BrzError::LineEndingError(msg) => Self::Unavailable {
                url,
                description: msg.to_string(),
            },
            BrzError::IncompleteRead(_partial, _expected) => Self::Unavailable {
                url,
                description: e.to_string(),
            },
            _ => Self::Other(e.to_string()),
        }
    }
}

/// Open a branch from a URL.
pub fn open_branch(
    url: &url::Url,
    possible_transports: Option<&mut Vec<Transport>>,
    probers: Option<&[&dyn Prober]>,
    name: Option<&str>,
) -> Result<Box<dyn Branch>, BranchOpenError> {
    let (url, params) = split_segment_parameters(url);

    let name = if let Some(name) = name {
        Some(name.to_string())
    } else {
        params.get("name").map(|s| s.to_string())
    };

    let transport = get_transport(&url, possible_transports)
        .map_err(|e| BranchOpenError::from_err(url.clone(), &e))?;
    let dir = open_from_transport(&transport, probers)
        .map_err(|e| BranchOpenError::from_err(url.clone(), &e))?;
    dir.open_branch(name.as_deref())
        .map_err(|e| BranchOpenError::from_err(url.clone(), &e))
}

/// Open a branch, either at the specified URL or in a containing directory.
///
/// Return the branch and the subpath of the URL that was used to open it.
pub fn open_branch_containing(
    url: &url::Url,
    possible_transports: Option<&mut Vec<Transport>>,
    probers: Option<&[&dyn Prober]>,
    name: Option<&str>,
) -> Result<(Box<dyn Branch>, String), BranchOpenError> {
    let (url, params) = split_segment_parameters(url);

    let name = if let Some(name) = name {
        Some(name.to_string())
    } else {
        params.get("name").map(|s| s.to_string())
    };

    let transport = match get_transport(&url, possible_transports) {
        Ok(transport) => transport,
        Err(e) => return Err(BranchOpenError::from_err(url.clone(), &e)),
    };
    let (dir, subpath) =
        open_containing_from_transport(&transport, probers).map_err(|e| match e {
            BrzError::UnknownFormat(_) => {
                unreachable!("open_containing_from_transport should not return UnknownFormat")
            }
            e => BranchOpenError::from_err(url.clone(), &e),
        })?;
    Ok((
        dir.open_branch(name.as_deref())
            .map_err(|e| BranchOpenError::from_err(url.clone(), &e))?,
        subpath,
    ))
}

/// Get the full URL for a branch.
///
/// Ideally this should just return Branch.user_url,
/// but that currently exclude the branch name
/// in some situations.
pub fn full_branch_url(branch: &dyn Branch) -> url::Url {
    if branch.name().is_none() {
        return branch.get_user_url();
    }
    let (url, mut params) = split_segment_parameters(&branch.get_user_url());
    if branch.name().as_deref() != Some("") {
        params.insert(
            "branch".to_string(),
            utf8_percent_encode(branch.name().unwrap().as_str(), CONTROLS).to_string(),
        );
    }
    join_segment_parameters(&url, params)
}
