use crate::proposal::DescriptionFormat;
use crate::Mode;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;

#[derive(Debug, Serialize, Deserialize, Clone, PartialEq, Eq)]
pub struct MergeRequest {
    #[serde(rename = "commit-message")]
    pub commit_message: Option<String>,

    pub title: Option<String>,

    #[serde(rename = "propose-threshold")]
    pub propose_threshold: Option<u32>,

    pub description: HashMap<Option<DescriptionFormat>, String>,
}

impl MergeRequest {
    pub fn render_commit_message(&self, context: &tera::Context) -> tera::Result<Option<String>> {
        let mut tera = tera::Tera::default();
        self.commit_message
            .as_ref()
            .map(|m| tera.render_str(m, context))
            .transpose()
    }

    pub fn render_title(&self, context: &tera::Context) -> tera::Result<Option<String>> {
        let mut tera = tera::Tera::default();
        self.title
            .as_ref()
            .map(|m| tera.render_str(m, context))
            .transpose()
    }

    pub fn render_description(
        &self,
        description_format: DescriptionFormat,
        context: &tera::Context,
    ) -> tera::Result<Option<String>> {
        let mut tera = tera::Tera::default();
        let template = if let Some(template) = self.description.get(&Some(description_format)) {
            template
        } else if let Some(template) = self.description.get(&None) {
            template
        } else {
            return Ok(None);
        };
        Ok(Some(tera.render_str(template.as_str(), context)?))
    }
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct Recipe {
    pub name: Option<String>,

    #[serde(rename = "merge-request")]
    pub merge_request: Option<MergeRequest>,

    pub labels: Option<Vec<String>>,

    pub command: Option<Vec<String>>,

    pub mode: Option<Mode>,

    pub resume: Option<bool>,

    #[serde(rename = "commit-pending")]
    pub commit_pending: crate::CommitPending,
}

impl Recipe {
    pub fn from_path(path: &std::path::Path) -> std::io::Result<Self> {
        let file = std::fs::File::open(path)?;
        let mut recipe: Recipe = serde_yaml::from_reader(file).unwrap();
        if recipe.name.is_none() {
            recipe.name = Some(path.file_stem().unwrap().to_str().unwrap().to_string());
        }
        Ok(recipe)
    }
}

#[test]
fn test_simple() {
    let td = tempfile::tempdir().unwrap();
    let path = td.path().join("test.yaml");
    std::fs::write(
        &path,
        r#"name: test
command: ["echo", "hello"]
mode: propose
merge-request:
  commit-message: "test commit message"
  title: "test title"
  description: "test description"
"#,
    )
    .unwrap();

    let recipe = Recipe::from_path(&path).unwrap();
    assert_eq!(recipe.name, Some("test".to_string()));
    assert_eq!(
        recipe.command,
        Some(vec!["echo".to_string(), "hello".to_string()])
    );
    assert_eq!(recipe.mode, Some(Mode::Propose));
    assert_eq!(
        recipe.merge_request,
        Some(MergeRequest {
            commit_message: Some("test commit message".to_string()),
            title: Some("test title".to_string()),
            propose_threshold: None,
            description: vec![(None, "test description".to_string())]
                .into_iter()
                .collect(),
        })
    );
}
