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
