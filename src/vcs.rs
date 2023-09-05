use breezyshim::{
    get_transport, join_segment_parameters, split_segment_parameters, Branch, ControlDir, Prober,
    Transport,
};
use percent_encoding::{utf8_percent_encode, CONTROLS};
use pyo3::exceptions::PyException;
use pyo3::prelude::*;
use pyo3::{create_exception, import_exception};

create_exception!(silver_platter.utils, BranchMissing, PyException);
create_exception!(silver_platter.utils, BranchUnsupported, PyException);
create_exception!(silver_platter.utils, BranchUnavailable, PyException);
create_exception!(silver_platter.utils, BranchRateLimited, PyException);
create_exception!(
    silver_platter.utils,
    BranchTemporarilyUnavailable,
    PyException
);

pub enum BranchOpenError {
    Unsupported {
        url: url::Url,
        description: String,
        vcs: Option<String>,
    },
    Missing {
        url: url::Url,
        description: String,
    },
    RateLimited {
        url: url::Url,
        description: String,
        retry_after: Option<f64>,
    },
    Unavailable {
        url: url::Url,
        description: String,
    },
    TemporarilyUnavailable {
        url: url::Url,
        description: String,
    },
    Other(PyErr),
}

impl From<BranchOpenError> for PyErr {
    fn from(e: BranchOpenError) -> Self {
        match e {
            BranchOpenError::Unsupported {
                url,
                description,
                vcs,
            } => BranchUnsupported::new_err(format!(
                "Unsupported VCS for {}: {} ({})",
                url,
                description,
                vcs.unwrap_or_else(|| "unknown".to_string())
            )),
            BranchOpenError::Missing { url, description } => {
                BranchMissing::new_err(format!("Missing branch {}: {}", url, description))
            }
            BranchOpenError::RateLimited {
                url,
                description,
                retry_after,
            } => BranchRateLimited::new_err(format!(
                "Rate limited {}: {} (retry after: {:?})",
                url, description, retry_after
            )),
            BranchOpenError::Unavailable { url, description } => {
                BranchUnavailable::new_err(format!("Unavailable {}: {}", url, description))
            }
            BranchOpenError::TemporarilyUnavailable { url, description } => {
                BranchTemporarilyUnavailable::new_err(format!(
                    "Temporarily unavailable {}: {}",
                    url, description
                ))
            }
            BranchOpenError::Other(e) => e,
        }
    }
}

import_exception!(socket, error);
import_exception!(breezy.errors, NotBranchError);
import_exception!(breezy.transport, UnsupportedProtocol);
import_exception!(breezy.transport, UnusableRedirect);
import_exception!(breezy.errors, ConnectionError);
import_exception!(breezy.errors, PermissionDenied);
import_exception!(breezy.urlutils, InvalidURL);
import_exception!(breezy.errors, TransportError);
import_exception!(breezy.errors, UnsupportedFormatError);
import_exception!(breezy.errors, UnknownFormatError);
import_exception!(breezy.errors, UnsupportedVcs);
import_exception!(breezy.git.remote, RemoteGitError);
import_exception!(http.client, IncompleteRead);
import_exception!(breezy.bzr, LineEndingError);
import_exception!(breezy.errors, InvalidHttpResponse);

impl BranchOpenError {
    pub fn from_err(py: Python, url: url::Url, e: &breezyshim::branch::BranchOpenError) -> Self {
        match e {
            breezyshim::branch::BranchOpenError::Other(e) => {
                Self::from_py_err(py, url, e).unwrap_or_else(|| Self::Other(e.clone_ref(py)))
            }
            breezyshim::branch::BranchOpenError::NotBranchError(e) => Self::Unavailable {
                url,
                description: e.clone(),
            },
            breezyshim::branch::BranchOpenError::DependencyNotPresent(l, e) => Self::Unavailable {
                url,
                description: format!("missing {}: {}", l, e),
            },
            breezyshim::branch::BranchOpenError::NoColocatedBranchSupport => Self::Unsupported {
                url,
                description: "no colocated branch support".to_string(),
                vcs: None,
            },
        }
    }

    pub fn from_py_err(py: Python, url: url::Url, e: &PyErr) -> Option<Self> {
        if e.is_instance_of::<error>(py) {
            return Some(Self::Unavailable {
                url,
                description: format!("Socket error: {}", e),
            });
        }
        if e.is_instance_of::<NotBranchError>(py) {
            return Some(Self::Unavailable {
                url,
                description: format!("Branch does not exist: {}", e),
            });
        }
        if e.is_instance_of::<UnsupportedProtocol>(py) {
            return Some(Self::Unsupported {
                url,
                description: e.to_string(),
                vcs: None,
            });
        }
        if e.is_instance_of::<ConnectionError>(py) {
            if e.to_string()
                .contains("Temporary failure in name resolution")
            {
                return Some(Self::TemporarilyUnavailable {
                    url,
                    description: e.to_string(),
                });
            } else {
                return Some(Self::Unavailable {
                    url,
                    description: e.to_string(),
                });
            }
        }
        if e.is_instance_of::<PermissionDenied>(py) {
            return Some(Self::Unavailable {
                url,
                description: e.to_string(),
            });
        }
        if e.is_instance_of::<InvalidURL>(py) {
            return Some(Self::Unavailable {
                url,
                description: e.to_string(),
            });
        }
        if e.is_instance_of::<InvalidHttpResponse>(py) {
            if e.to_string().contains("Unexpected HTTP status 429") {
                let headers = e.value(py).getattr("headers").unwrap();
                if let Ok(retry_after) = headers.get_item("Retry-After") {
                    let retry_after = retry_after.extract::<String>().unwrap();
                    match retry_after.parse::<f64>() {
                        Ok(retry_after) => {
                            return Some(Self::RateLimited {
                                url,
                                description: e.to_string(),
                                retry_after: Some(retry_after),
                            });
                        }
                        Err(e) => {
                            log::warn!("Unable to parse retry-after header: {}", retry_after);
                            return Some(Self::RateLimited {
                                url,
                                description: e.to_string(),
                                retry_after: None,
                            });
                        }
                    }
                }
                return Some(Self::RateLimited {
                    url,
                    description: e.to_string(),
                    retry_after: None,
                });
            }
            return Some(Self::Unavailable {
                url,
                description: e.to_string(),
            });
        }
        if e.is_instance_of::<TransportError>(py) {
            return Some(Self::Unavailable {
                url,
                description: e.to_string(),
            });
        }
        if e.is_instance_of::<UnusableRedirect>(py) {
            return Some(Self::Unavailable {
                url,
                description: e.to_string(),
            });
        }
        if e.is_instance_of::<UnsupportedVcs>(py) {
            return Some(Self::Unsupported {
                url,
                description: e.to_string(),
                vcs: e
                    .value(py)
                    .getattr("vcs")
                    .unwrap()
                    .extract::<Option<String>>()
                    .unwrap(),
            });
        }
        if e.is_instance_of::<UnsupportedFormatError>(py) {
            return Some(Self::Unsupported {
                url,
                description: e.to_string(),
                vcs: None,
            });
        }
        if e.is_instance_of::<UnknownFormatError>(py) {
            return Some(Self::Unsupported {
                url,
                description: e.to_string(),
                vcs: None,
            });
        }
        if e.is_instance_of::<RemoteGitError>(py) {
            return Some(Self::Unavailable {
                url,
                description: e.to_string(),
            });
        }
        if e.is_instance_of::<LineEndingError>(py) {
            return Some(Self::Unavailable {
                url,
                description: e.to_string(),
            });
        }
        if e.is_instance_of::<IncompleteRead>(py) {
            return Some(Self::Unavailable {
                url,
                description: e.to_string(),
            });
        }
        None
    }
}

pub fn open_branch(
    url: url::Url,
    possible_transports: Option<Vec<Transport>>,
    probers: Option<&[Prober]>,
    name: Option<&str>,
) -> Result<Branch, BranchOpenError> {
    let (url, params) = split_segment_parameters(&url);

    let name = if let Some(name) = name {
        Some(name.to_string())
    } else {
        params.get("name").map(|s| s.to_string())
    };

    let transport = get_transport(&url, possible_transports);
    Python::with_gil(|py| {
        let dir = ControlDir::open_from_transport(&transport, probers).map_err(|e| {
            BranchOpenError::from_py_err(py, url.clone(), &e)
                .unwrap_or_else(|| BranchOpenError::Other(e))
        })?;
        dir.open_branch(name.as_deref())
            .map_err(|e| BranchOpenError::from_err(py, url.clone(), &e))
    })
}

pub fn open_branch_containing(
    url: url::Url,
    possible_transports: Option<Vec<Transport>>,
    probers: Option<&[Prober]>,
    name: Option<&str>,
) -> Result<(Branch, String), BranchOpenError> {
    let (url, params) = split_segment_parameters(&url);

    let name = if let Some(name) = name {
        Some(name.to_string())
    } else {
        params.get("name").map(|s| s.to_string())
    };

    let transport = get_transport(&url, possible_transports);
    Python::with_gil(|py| {
        let (dir, subpath) = ControlDir::open_containing_from_transport(&transport, probers)
            .map_err(|e| {
                BranchOpenError::from_py_err(py, url.clone(), &e)
                    .unwrap_or_else(|| BranchOpenError::Other(e))
            })?;
        Ok((
            dir.open_branch(name.as_deref())
                .map_err(|e| BranchOpenError::from_err(py, url.clone(), &e))?,
            subpath,
        ))
    })
}

/// Get the full URL for a branch.
///
/// Ideally this should just return Branch.user_url,
/// but that currently exclude the branch name
/// in some situations.
pub fn full_branch_url(branch: &Branch) -> url::Url {
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
