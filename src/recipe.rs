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
    #[serde(
        rename = "auto-merge",
        default,
        skip_serializing_if = "Option::is_none"
    )]
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
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub resume: Option<bool>,

    #[serde(rename = "commit-pending")]
    /// Whether to commit pending changes
    #[serde(default, skip_serializing_if = "crate::CommitPending::is_default")]
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

    #[test]
    fn test_builder_with_optional_fields() {
        let recipe = RecipeBuilder::new()
            .name("test".to_string())
            .command(Command::Argv(vec!["echo".to_string(), "hello".to_string()]))
            .mode(Mode::Propose)
            .label("test-label".to_string())
            .label("another-label".to_string())
            .resume(true)
            .commit_pending(crate::CommitPending::Yes)
            .build();

        assert_eq!(recipe.name, Some("test".to_string()));
        assert_eq!(
            recipe.labels,
            Some(vec!["test-label".to_string(), "another-label".to_string()])
        );
        assert_eq!(recipe.resume, Some(true));
        assert_eq!(recipe.commit_pending, crate::CommitPending::Yes);
    }

    #[test]
    fn test_command_shell() {
        let shell_command = Command::Shell("echo hello".to_string());

        // Test shell() method
        assert_eq!(shell_command.shell(), "echo hello");

        // Test argv() method for shell command
        assert_eq!(
            shell_command.argv(),
            vec!["sh".to_string(), "-c".to_string(), "echo hello".to_string()]
        );
    }

    #[test]
    fn test_command_argv() {
        let argv_command = Command::Argv(vec!["echo".to_string(), "hello".to_string()]);

        // Test shell() method for argv command
        assert_eq!(argv_command.shell(), "echo hello");

        // Test argv() method
        assert_eq!(
            argv_command.argv(),
            vec!["echo".to_string(), "hello".to_string()]
        );
    }

    #[test]
    fn test_merge_request_render() {
        let merge_request = MergeRequest {
            commit_message: Some("Commit: {{ var }}".to_string()),
            title: Some("Title: {{ var }}".to_string()),
            propose_threshold: None,
            auto_merge: None,
            description: [
                (
                    Some(DescriptionFormat::Markdown),
                    "Markdown: {{ var }}".to_string(),
                ),
                (
                    Some(DescriptionFormat::Plain),
                    "Plain: {{ var }}".to_string(),
                ),
                (None, "Default: {{ var }}".to_string()),
            ]
            .into_iter()
            .collect(),
        };

        let mut context = tera::Context::new();
        context.insert("var", "test-value");

        // Test rendering commit message
        let commit_message = merge_request.render_commit_message(&context).unwrap();
        assert_eq!(commit_message, Some("Commit: test-value".to_string()));

        // Test rendering title
        let title = merge_request.render_title(&context).unwrap();
        assert_eq!(title, Some("Title: test-value".to_string()));

        // Test rendering description with specific format
        let markdown_desc = merge_request
            .render_description(DescriptionFormat::Markdown, &context)
            .unwrap();
        assert_eq!(markdown_desc, Some("Markdown: test-value".to_string()));

        // Test rendering description with another format
        let plain_desc = merge_request
            .render_description(DescriptionFormat::Plain, &context)
            .unwrap();
        assert_eq!(plain_desc, Some("Plain: test-value".to_string()));

        // Test rendering description with format not defined (should fall back to default)
        let html_desc = merge_request
            .render_description(DescriptionFormat::Html, &context)
            .unwrap();
        assert_eq!(html_desc, Some("Default: test-value".to_string()));
    }

    #[test]
    fn test_merge_request_no_templates() {
        let merge_request = MergeRequest {
            commit_message: None,
            title: None,
            propose_threshold: None,
            auto_merge: None,
            description: HashMap::new(),
        };

        let context = tera::Context::new();

        // Test rendering with no templates
        let commit_message = merge_request.render_commit_message(&context).unwrap();
        assert_eq!(commit_message, None);

        let title = merge_request.render_title(&context).unwrap();
        assert_eq!(title, None);

        let desc = merge_request
            .render_description(DescriptionFormat::Markdown, &context)
            .unwrap();
        assert_eq!(desc, None);
    }

    #[test]
    fn test_merge_request_auto_merge() {
        // Test default value
        let merge_request = MergeRequest {
            commit_message: None,
            title: None,
            propose_threshold: None,
            description: std::collections::HashMap::new(),
            auto_merge: None,
        };
        assert_eq!(merge_request.auto_merge, None);

        // Test explicit true value
        let merge_request = MergeRequest {
            commit_message: None,
            title: None,
            propose_threshold: None,
            description: std::collections::HashMap::new(),
            auto_merge: Some(true),
        };
        assert_eq!(merge_request.auto_merge, Some(true));

        // Test explicit false value
        let merge_request = MergeRequest {
            commit_message: None,
            title: None,
            propose_threshold: None,
            description: std::collections::HashMap::new(),
            auto_merge: Some(false),
        };
        assert_eq!(merge_request.auto_merge, Some(false));
    }

    #[test]
    fn test_merge_request_auto_merge_serialization() {
        use serde_yaml;

        // Test serialization with auto_merge: true
        let merge_request = MergeRequest {
            commit_message: None,
            title: None,
            propose_threshold: None,
            description: std::collections::HashMap::new(),
            auto_merge: Some(true),
        };
        let yaml = serde_yaml::to_string(&merge_request).unwrap();
        assert!(yaml.contains("auto-merge: true"));

        // Test deserialization
        let yaml_content = r#"
auto-merge: true
"#;
        let merge_request: MergeRequest = serde_yaml::from_str(yaml_content).unwrap();
        assert_eq!(merge_request.auto_merge, Some(true));

        // Test deserialization with false
        let yaml_content = r#"
auto-merge: false
"#;
        let merge_request: MergeRequest = serde_yaml::from_str(yaml_content).unwrap();
        assert_eq!(merge_request.auto_merge, Some(false));
    }
}
