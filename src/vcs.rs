use crate::breezyshim::{
    get_transport, split_segment_parameters, Branch, ControlDir, Prober, Transport,
};
use pyo3::prelude::*;

pub fn open_branch(
    url: url::Url,
    possible_transports: Option<Vec<Transport>>,
    probers: Option<&[Prober]>,
    mut name: Option<&str>,
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
