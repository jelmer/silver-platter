use crate::breezyshim::{
    get_transport, join_segment_parameters, split_segment_parameters, Branch, ControlDir, Prober,
    Transport,
};
use percent_encoding::{utf8_percent_encode, CONTROLS};
use pyo3::prelude::*;

pub fn open_branch(
    url: url::Url,
    possible_transports: Option<Vec<Transport>>,
    probers: Option<&[Prober]>,
    name: Option<&str>,
) -> PyResult<Branch> {
    let (url, params) = split_segment_parameters(&url);

    let name = if let Some(name) = name {
        Some(name.to_string())
    } else {
        params.get("name").map(|s| s.to_string())
    };

    let transport = get_transport(&url, possible_transports);
    let dir = ControlDir::open_from_transport(&transport, probers)?;
    dir.open_branch(name.as_deref())
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
