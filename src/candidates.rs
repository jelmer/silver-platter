use crate::Mode;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct Candidate {
    pub url: url::Url,
    pub name: Option<String>,
    pub branch: Option<String>,
    pub subpath: Option<String>,
    #[serde(rename = "default-mode")]
    pub default_mode: Option<Mode>,
}

#[derive(Debug, Clone, Default)]
pub struct Candidates(Vec<Candidate>);

impl Candidates {
    pub fn from_path(path: &std::path::Path) -> std::io::Result<Self> {
        let f = std::fs::File::open(path)?;
        let candidates: Vec<Candidate> = serde_yaml::from_reader(f).unwrap();
        Ok(Self(candidates))
    }

    pub fn candidates(&self) -> &[Candidate] {
        self.0.as_slice()
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
