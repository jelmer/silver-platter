//! Candidates for packages.
use crate::Mode;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Deserialize, Serialize)]
/// A candidate for a package.
pub struct Candidate {
    /// The URL of the repository.
    pub url: url::Url,

    /// The name of the package.
    pub name: Option<String>,

    /// The branch to use.
    pub branch: Option<String>,

    /// The subpath to use.
    pub subpath: Option<std::path::PathBuf>,

    #[serde(rename = "default-mode")]
    /// The default mode to use.
    pub default_mode: Option<Mode>,
}

impl Candidate {
    /// Return the short name of the candidate.
    pub fn shortname(&self) -> std::borrow::Cow<str> {
        match &self.name {
            Some(name) => std::borrow::Cow::Borrowed(name),
            None => std::borrow::Cow::Owned(
                self.url
                    .path_segments()
                    .and_then(|segments| segments.last())
                    .unwrap_or("unknown")
                    .to_string(),
            ),
        }
    }
}

#[derive(Debug, Clone, Default)]
/// Candidates
pub struct Candidates(Vec<Candidate>);

impl Candidates {
    /// Load packages from a file
    pub fn from_path(path: &std::path::Path) -> std::io::Result<Self> {
        let f = std::fs::File::open(path)?;
        let candidates: Vec<Candidate> = serde_yaml::from_reader(f).unwrap();
        Ok(Self(candidates))
    }

    /// Return a slice of the candidates.
    pub fn candidates(&self) -> &[Candidate] {
        self.0.as_slice()
    }

    /// Return an iterator over the candidates.
    pub fn iter(&self) -> impl Iterator<Item = &Candidate> {
        self.0.iter()
    }

    /// Create an empty Candidates object.
    pub fn new() -> Self {
        Self(Vec::new())
    }
}

impl TryFrom<serde_yaml::Value> for Candidates {
    type Error = serde_yaml::Error;

    fn try_from(yaml: serde_yaml::Value) -> Result<Self, Self::Error> {
        Ok(Self(serde_yaml::from_value(yaml)?))
    }
}

impl From<Vec<Candidate>> for Candidates {
    fn from(candidates: Vec<Candidate>) -> Self {
        Self(candidates)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn test_read() {
        let td = tempfile::tempdir().unwrap();
        let path = td.path().join("candidates.yaml");
        std::fs::write(
            &path,
            r#"---
    - url: https://github.com/jelmer/dulwich
    - name: samba
      url: https://git.samba.org/samba.git
    "#,
        )
        .unwrap();
        let candidates = Candidates::from_path(&path).unwrap();
        assert_eq!(candidates.candidates().len(), 2);
        assert_eq!(
            candidates.candidates()[0].url,
            url::Url::parse("https://github.com/jelmer/dulwich").unwrap()
        );
        assert_eq!(
            candidates.candidates()[1].url,
            url::Url::parse("https://git.samba.org/samba.git").unwrap()
        );
        assert_eq!(candidates.candidates()[1].name, Some("samba".to_string()));
    }

    #[test]
    fn test_shortname() {
        let candidate = Candidate {
            url: url::Url::parse("https://github.com/jelmer/dulwich").unwrap(),
            name: None,
            branch: None,
            subpath: None,
            default_mode: None,
        };

        assert_eq!(candidate.shortname(), "dulwich");
    }

    #[test]
    fn test_shortname_stored() {
        let candidate = Candidate {
            url: url::Url::parse("https://github.com/jelmer/dulwich").unwrap(),
            name: Some("foo".to_string()),
            branch: None,
            subpath: None,
            default_mode: None,
        };

        assert_eq!(candidate.shortname(), "foo");
    }

    #[test]
    fn test_candidates_new() {
        let candidates = Candidates::new();
        assert_eq!(candidates.candidates().len(), 0);
    }

    #[test]
    fn test_candidates_from_vec() {
        let candidate1 = Candidate {
            url: url::Url::parse("https://github.com/jelmer/dulwich").unwrap(),
            name: Some("dulwich".to_string()),
            branch: None,
            subpath: None,
            default_mode: None,
        };

        let candidate2 = Candidate {
            url: url::Url::parse("https://github.com/jelmer/silver-platter").unwrap(),
            name: Some("silver-platter".to_string()),
            branch: None,
            subpath: None,
            default_mode: None,
        };

        let candidates_vec = vec![candidate1, candidate2];
        let candidates = Candidates::from(candidates_vec);

        assert_eq!(candidates.candidates().len(), 2);
        assert_eq!(candidates.candidates()[0].name, Some("dulwich".to_string()));
        assert_eq!(
            candidates.candidates()[1].name,
            Some("silver-platter".to_string())
        );
    }

    #[test]
    fn test_candidates_iter() {
        let candidate1 = Candidate {
            url: url::Url::parse("https://github.com/jelmer/dulwich").unwrap(),
            name: Some("dulwich".to_string()),
            branch: None,
            subpath: None,
            default_mode: None,
        };

        let candidate2 = Candidate {
            url: url::Url::parse("https://github.com/jelmer/silver-platter").unwrap(),
            name: Some("silver-platter".to_string()),
            branch: None,
            subpath: None,
            default_mode: None,
        };

        let candidates = Candidates::from(vec![candidate1, candidate2]);

        let names: Vec<String> = candidates.iter().map(|c| c.name.clone().unwrap()).collect();

        assert_eq!(
            names,
            vec!["dulwich".to_string(), "silver-platter".to_string()]
        );
    }

    #[test]
    fn test_try_from_yaml() {
        let yaml = serde_yaml::from_str::<serde_yaml::Value>(
            r#"
        - url: https://github.com/jelmer/dulwich
          name: dulwich
        - url: https://github.com/jelmer/silver-platter
          name: silver-platter
          branch: main
        "#,
        )
        .unwrap();

        let candidates = Candidates::try_from(yaml).unwrap();

        assert_eq!(candidates.candidates().len(), 2);
        assert_eq!(candidates.candidates()[0].name, Some("dulwich".to_string()));
        assert_eq!(candidates.candidates()[1].branch, Some("main".to_string()));
    }
}
