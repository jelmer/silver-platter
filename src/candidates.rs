//! Candidates for packages.
use crate::Mode;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Eq)]
/// Error type for shortname extraction
pub enum ShortnameError {
    /// URL has no path segments
    NoPathSegments,
    /// No non-empty path segments found
    NoValidSegments,
}

impl std::fmt::Display for ShortnameError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ShortnameError::NoPathSegments => write!(f, "URL has no path segments"),
            ShortnameError::NoValidSegments => write!(f, "No non-empty path segments found"),
        }
    }
}

impl std::error::Error for ShortnameError {}

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
    pub fn shortname(&self) -> Result<std::borrow::Cow<str>, ShortnameError> {
        match &self.name {
            Some(name) => Ok(std::borrow::Cow::Borrowed(name)),
            None => {
                if let Some(segments) = self.url.path_segments() {
                    let segments: Vec<_> = segments.collect();

                    // Find the last non-empty segment
                    let last_non_empty = segments.iter().rev().find(|s| !s.is_empty());

                    match last_non_empty {
                        Some(segment) => Ok(std::borrow::Cow::Owned(segment.to_string())),
                        None => Err(ShortnameError::NoValidSegments),
                    }
                } else {
                    Err(ShortnameError::NoPathSegments)
                }
            }
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

        assert_eq!(candidate.shortname().unwrap(), "dulwich");
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

        assert_eq!(candidate.shortname().unwrap(), "foo");
    }

    #[test]
    fn test_shortname_cow_behavior() {
        use std::borrow::Cow;

        // Test borrowed case (when name exists)
        let candidate_with_name = Candidate {
            url: url::Url::parse("https://github.com/jelmer/dulwich").unwrap(),
            name: Some("myproject".to_string()),
            branch: None,
            subpath: None,
            default_mode: None,
        };

        let shortname = candidate_with_name.shortname().unwrap();
        assert!(matches!(shortname, Cow::Borrowed(_)));
        assert_eq!(shortname, "myproject");

        // Test owned case (when name is None)
        let candidate_without_name = Candidate {
            url: url::Url::parse("https://github.com/jelmer/dulwich").unwrap(),
            name: None,
            branch: None,
            subpath: None,
            default_mode: None,
        };

        let shortname = candidate_without_name.shortname().unwrap();
        assert!(matches!(shortname, Cow::Owned(_)));
        assert_eq!(shortname, "dulwich");
    }

    #[test]
    fn test_shortname_edge_cases() {
        // Test URL without path segments
        let candidate_no_path = Candidate {
            url: url::Url::parse("https://github.com/").unwrap(),
            name: None,
            branch: None,
            subpath: None,
            default_mode: None,
        };
        assert!(candidate_no_path.shortname().is_err());
        assert_eq!(
            candidate_no_path.shortname().unwrap_err(),
            ShortnameError::NoValidSegments
        );

        // Test URL with trailing slash
        let candidate_trailing_slash = Candidate {
            url: url::Url::parse("https://github.com/jelmer/project/").unwrap(),
            name: None,
            branch: None,
            subpath: None,
            default_mode: None,
        };
        assert_eq!(candidate_trailing_slash.shortname().unwrap(), "project");
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
