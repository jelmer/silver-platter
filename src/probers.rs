//! Selection of probers

/// Get a prober for a particular VCS type.
pub fn get_prober(vcs_type: &str) -> Option<Box<dyn breezyshim::controldir::PyProber>> {
    match vcs_type {
        "bzr" => breezyshim::bazaar::RemoteBzrProber::new()
            .map(|prober| Box::new(prober) as Box<dyn breezyshim::controldir::PyProber>),
        "git" => breezyshim::git::RemoteGitProber::new()
            .map(|prober| Box::new(prober) as Box<dyn breezyshim::controldir::PyProber>),
        "hg" => breezyshim::mercurial::SmartHgProber::new()
            .map(|prober| Box::new(prober) as Box<dyn breezyshim::controldir::PyProber>),
        "svn" => breezyshim::subversion::SvnRepositoryProber::new()
            .map(|prober| Box::new(prober) as Box<dyn breezyshim::controldir::PyProber>),
        "fossil" => breezyshim::fossil::RemoteFossilProber::new()
            .map(|prober| Box::new(prober) as Box<dyn breezyshim::controldir::PyProber>),
        "darcs" => breezyshim::darcs::DarcsProber::new()
            .map(|prober| Box::new(prober) as Box<dyn breezyshim::controldir::PyProber>),
        "cvs" => breezyshim::cvs::CVSProber::new()
            .map(|prober| Box::new(prober) as Box<dyn breezyshim::controldir::PyProber>),

        _ => None,
    }
}

/// Select all probers relevant to a particular VCS type.
pub fn select_probers(vcs_type: Option<&str>) -> Vec<Box<dyn breezyshim::controldir::PyProber>> {
    match vcs_type {
        Some("bzr") => [
            breezyshim::bazaar::RemoteBzrProber::new()
                .map(|prober| Box::new(prober) as Box<dyn breezyshim::controldir::PyProber>),
            Some(Box::new(breezyshim::bazaar::BzrProber::new())
                as Box<dyn breezyshim::controldir::PyProber>),
        ]
        .into_iter()
        .flatten()
        .collect(),
        Some("git") => [
            breezyshim::git::RemoteGitProber::new()
                .map(|prober| Box::new(prober) as Box<dyn breezyshim::controldir::PyProber>),
            Some(Box::new(breezyshim::git::LocalGitProber::new())
                as Box<dyn breezyshim::controldir::PyProber>),
        ]
        .into_iter()
        .flatten()
        .collect(),
        Some(vcs_type) => get_prober(vcs_type).into_iter().collect(),
        None => breezyshim::controldir::all_probers(),
    }
}

/// Select probers with the given VCS type as the first prober.
pub fn select_preferred_probers(
    vcs_type: Option<&str>,
) -> Vec<Box<dyn breezyshim::controldir::PyProber>> {
    let mut probers = breezyshim::controldir::all_probers();
    if let Some(vcs_type) = vcs_type {
        if let Some(prober) = get_prober(&vcs_type.to_lowercase()) {
            probers.insert(0, prober);
        }
    }
    probers
}

#[cfg(test)]
mod tests {
    #[test]
    fn test_probers() {
        let _ = super::select_probers(None);

        let ps = super::select_probers(Some("bzr"));
        assert_eq!(ps.len(), 2);
    }

    #[test]
    fn test_select_probers_opens_local_branch() {
        for (vcs_type, format) in [("git", "git"), ("bzr", "2a")] {
            let td = tempfile::tempdir().unwrap();
            let url = url::Url::from_directory_path(td.path()).unwrap();
            let format = breezyshim::controldir::FORMAT_REGISTRY
                .make_controldir(format)
                .unwrap();
            breezyshim::controldir::create_branch_convenience_as_generic(&url, None, &format)
                .unwrap();
            let probers = super::select_probers(Some(vcs_type));
            let probers = probers.iter().map(|p| p.as_ref()).collect::<Vec<_>>();
            crate::vcs::open_branch(&url, None, Some(&probers), None).unwrap();
        }
    }

    #[test]
    fn test_preferred_probers() {
        let _ = super::select_preferred_probers(None);

        let ps = super::select_preferred_probers(Some("bzr"));
        assert!(ps.len() > 1);
    }
}
