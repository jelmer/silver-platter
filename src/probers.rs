use breezyshim::controldir::Prober;

pub fn get_prober(vcs_type: &str) -> Option<Box<dyn Prober>> {
    match vcs_type {
        "bzr" => breezyshim::bazaar::RemoteBzrProber::new()
            .map(|prober| Box::new(prober) as Box<dyn Prober>),
        "git" => breezyshim::git::RemoteGitProber::new()
            .map(|prober| Box::new(prober) as Box<dyn Prober>),
        "hg" => breezyshim::mercurial::SmartHgProber::new()
            .map(|prober| Box::new(prober) as Box<dyn Prober>),
        "svn" => breezyshim::subversion::SvnRepositoryProber::new()
            .map(|prober| Box::new(prober) as Box<dyn Prober>),
        "fossil" => breezyshim::fossil::RemoteFossilProber::new()
            .map(|prober| Box::new(prober) as Box<dyn Prober>),
        "darcs" => {
            breezyshim::darcs::DarcsProber::new().map(|prober| Box::new(prober) as Box<dyn Prober>)
        }
        "cvs" => {
            breezyshim::cvs::CVSProber::new().map(|prober| Box::new(prober) as Box<dyn Prober>)
        }

        _ => None,
    }
}

pub fn select_probers(vcs_type: Option<&str>) -> Vec<Box<dyn Prober>> {
    if let Some(vcs_type) = vcs_type {
        if let Some(prober) = get_prober(vcs_type) {
            return vec![prober];
        }
        vec![]
    } else {
        breezyshim::controldir::all_probers()
    }
}

pub fn select_preferred_probers(vcs_type: Option<&str>) -> Vec<Box<dyn Prober>> {
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
        assert_eq!(ps.len(), 1);
    }

    #[test]
    fn test_preferred_probers() {
        let _ = super::select_preferred_probers(None);

        let ps = super::select_preferred_probers(Some("bzr"));
        assert!(ps.len() > 1);
    }
}
