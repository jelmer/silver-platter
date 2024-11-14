//! Recipes
use crate::proposal::DescriptionFormat;
use crate::Mode;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;

#[derive(Debug, Serialize, Deserialize, Clone, PartialEq, Eq)]
/// Merge request configuration
pub struct MergeRequest {
    #[serde(rename = "commit-message")]
    #[serde(default, skip_serializing_if = "Option::is_none")]
    /// Commit message template
    pub commit_message: Option<String>,

    /// Title template
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub title: Option<String>,

    #[serde(rename = "propose-threshold")]
    #[serde(default, skip_serializing_if = "Option::is_none")]
    /// Value threshold for proposing the merge request
    pub propose_threshold: Option<u32>,

    /// Description templates
    #[serde(default, deserialize_with = "deserialize_description")]
    pub description: HashMap<Option<DescriptionFormat>, String>,

    /// Whether to enable automatic merge
    #[serde(rename = "auto-merge", default)]
    pub auto_merge: Option<bool>,
}

fn deserialize_description<'de, D>(
    deserializer: D,
) -> Result<HashMap<Option<DescriptionFormat>, String>, D::Error>
where
    D: serde::Deserializer<'de>,
{
    #[derive(Deserialize)]
    #[serde(untagged)]
    enum StringOrMap {
        String(String),
        Map(HashMap<Option<DescriptionFormat>, String>),
    }

    let helper = StringOrMap::deserialize(deserializer)?;
    let mut result = HashMap::new();
    match helper {
        StringOrMap::String(s) => {
            result.insert(None, s);
        }
        StringOrMap::Map(m) => {
            result = m;
        }
    }
    Ok(result)
}

impl MergeRequest {
    /// Render a commit message
    pub fn render_commit_message(&self, context: &tera::Context) -> tera::Result<Option<String>> {
        let mut tera = tera::Tera::default();
        self.commit_message
            .as_ref()
            .map(|m| tera.render_str(m, context))
            .transpose()
    }

    /// Render the title of the merge request
    pub fn render_title(&self, context: &tera::Context) -> tera::Result<Option<String>> {
        let mut tera = tera::Tera::default();
        self.title
            .as_ref()
            .map(|m| tera.render_str(m, context))
            .transpose()
    }

    /// Render the description of the merge request
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

#[derive(serde::Serialize, serde::Deserialize, Debug, Clone)]
#[serde(untagged)]
/// Command as either a shell string or a vector of arguments
pub enum Command {
    /// Command as a shell string
    Shell(String),

    /// Command as a vector of arguments
    Argv(Vec<String>),
}

impl Command {
    /// Get the command as a shell string
    pub fn shell(&self) -> String {
        match self {
            Command::Shell(s) => s.clone(),
            Command::Argv(v) => {
                let args = v.iter().map(|x| x.as_str()).collect::<Vec<_>>();
                shlex::try_join(args).unwrap()
            }
        }
    }

    /// Get the command as a vector of arguments
    pub fn argv(&self) -> Vec<String> {
        match self {
            Command::Shell(s) => vec!["sh".to_string(), "-c".to_string(), s.clone()],
            Command::Argv(v) => v.clone(),
        }
    }
}

/// A recipe builder
pub struct RecipeBuilder {
    recipe: Recipe,
}

impl RecipeBuilder {
    /// Create a new recipe builder
    pub fn new() -> Self {
        Self {
            recipe: Recipe {
                name: None,
                merge_request: None,
                labels: None,
                command: None,
                mode: None,
                resume: None,
                commit_pending: crate::CommitPending::default(),
            },
        }
    }

    /// Set the name of the recipe
    pub fn name(mut self, name: String) -> Self {
        self.recipe.name = Some(name);
        self
    }

    /// Set the merge request configuration
    pub fn merge_request(mut self, merge_request: MergeRequest) -> Self {
        self.recipe.merge_request = Some(merge_request);
        self
    }

    /// Set the labels to apply to the merge request
    pub fn labels(mut self, labels: Vec<String>) -> Self {
        self.recipe.labels = Some(labels);
        self
    }

    /// Set a label to apply to the merge request
    pub fn label(mut self, label: String) -> Self {
        if let Some(labels) = &mut self.recipe.labels {
            labels.push(label);
        } else {
            self.recipe.labels = Some(vec![label]);
        }
        self
    }

    /// Set the command to run
    pub fn command(mut self, command: Command) -> Self {
        self.recipe.command = Some(command);
        self
    }

    /// Set the command to run as an argv
    pub fn argv(mut self, argv: Vec<String>) -> Self {
        self.recipe.command = Some(Command::Argv(argv));
        self
    }

    /// Set the command to run as a shell string
    pub fn shell(mut self, shell: String) -> Self {
        self.recipe.command = Some(Command::Shell(shell));
        self
    }

    /// Set the mode to run the recipe in
    pub fn mode(mut self, mode: Mode) -> Self {
        self.recipe.mode = Some(mode);
        self
    }

    /// Set whether to resume a previous run
    pub fn resume(mut self, resume: bool) -> Self {
        self.recipe.resume = Some(resume);
        self
    }

    /// Set whether to commit pending changes
    pub fn commit_pending(mut self, commit_pending: crate::CommitPending) -> Self {
        self.recipe.commit_pending = commit_pending;
        self
    }

    /// Build the recipe
    pub fn build(self) -> Recipe {
        self.recipe
    }
}

#[derive(Debug, Serialize, Deserialize, Clone)]
/// A recipe
pub struct Recipe {
    /// Name of the recipe
    pub name: Option<String>,

    #[serde(rename = "merge-request")]
    /// Merge request configuration
    pub merge_request: Option<MergeRequest>,

    /// Labels to apply to the merge request
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub labels: Option<Vec<String>>,

    /// Command to run
    pub command: Option<Command>,

    /// Mode to run the recipe in
    pub mode: Option<Mode>,

    /// Whether to resume a previous run
    pub resume: Option<bool>,

    #[serde(rename = "commit-pending")]
    /// Whether to commit pending changes
    pub commit_pending: crate::CommitPending,
}

impl Recipe {
    /// Load a recipe from a file
    pub fn from_path(path: &std::path::Path) -> std::io::Result<Self> {
        let file = std::fs::File::open(path)?;
        let mut recipe: Recipe = serde_yaml::from_reader(file).unwrap();
        if recipe.name.is_none() {
            recipe.name = Some(path.file_stem().unwrap().to_str().unwrap().to_string());
        }
        Ok(recipe)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_simple() {
        let td = tempfile::tempdir().unwrap();
        let path = td.path().join("test.yaml");
        std::fs::write(
            &path,
            r#"---
name: test
command: ["echo", "hello"]
mode: propose
merge-request:
  commit-message: "test commit message"
  title: "test title"
  description:
    plain: "test description"
"#,
        )
        .unwrap();

        let recipe = Recipe::from_path(&path).unwrap();
        assert_eq!(recipe.name, Some("test".to_string()));
        assert_eq!(
            recipe.command.unwrap().argv(),
            vec!["echo".to_string(), "hello".to_string()]
        );
        assert_eq!(recipe.mode, Some(Mode::Propose));
        assert_eq!(
            recipe.merge_request,
            Some(MergeRequest {
                commit_message: Some("test commit message".to_string()),
                title: Some("test title".to_string()),
                propose_threshold: None,
                auto_merge: None,
                description: vec![(
                    Some(DescriptionFormat::Plain),
                    "test description".to_string()
                )]
                .into_iter()
                .collect(),
            })
        );
    }

    #[test]
    fn test_builder() {
        let recipe = RecipeBuilder::new()
            .name("test".to_string())
            .command(Command::Argv(vec!["echo".to_string(), "hello".to_string()]))
            .mode(Mode::Propose)
            .merge_request(MergeRequest {
                commit_message: Some("test commit message".to_string()),
                title: Some("test title".to_string()),
                propose_threshold: None,
                auto_merge: None,
                description: vec![(
                    Some(DescriptionFormat::Plain),
                    "test description".to_string(),
                )]
                .into_iter()
                .collect(),
            })
            .build();
        assert_eq!(recipe.name, Some("test".to_string()));
        assert_eq!(
            recipe.command.unwrap().argv(),
            vec!["echo".to_string(), "hello".to_string()]
        );
    }
}
