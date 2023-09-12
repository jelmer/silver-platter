use breezyshim::tree::{MutableTree, Tree, WorkingTree};
use pyo3::prelude::*;
use pyo3::types::PyDict;
use std::path::Path;

pub fn control_files_in_root(tree: &dyn Tree, subpath: &Path) -> bool {
    let debian_path = subpath.join("debian");
    if tree.has_filename(&debian_path) {
        return false;
    }
    let control_path = subpath.join("control");
    if tree.has_filename(&control_path) {
        return true;
    }
    let template_control_path = control_path.with_extension("in");
    return tree.has_filename(&template_control_path);
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ChangelogBehaviour {
    update_changelog: bool,
    explanation: String,
}

impl FromPyObject<'_> for ChangelogBehaviour {
    fn extract(obj: &PyAny) -> PyResult<Self> {
        let update_changelog = obj.getattr("update_changelog")?.extract()?;
        let explanation = obj.getattr("explanation")?.extract()?;
        Ok(ChangelogBehaviour {
            update_changelog,
            explanation,
        })
    }
}

pub fn guess_update_changelog(
    tree: &WorkingTree,
    debian_path: &Path,
) -> Option<ChangelogBehaviour> {
    Python::with_gil(|py| {
        let m = match py.import("lintian_brush") {
            Ok(m) => m,
            Err(e) => {
                log::warn!("Install lintian-brush to detect automatically whether the changelog should be updated.");
                return Some(ChangelogBehaviour {
                    update_changelog: true,
                    explanation: format!(
                        "defaulting to updating changelog since lintian-brush is not installed: {}",
                        e
                    ),
                });
            }
        };

        let guess_update_changelog = m.getattr("guess_update_changelog").unwrap();

        let result = guess_update_changelog
            .call1((tree.to_object(py), debian_path))
            .unwrap();
        result.extract().unwrap()
    })
}

/// Add a changelog entry.
///
/// # Arguments
/// * `tree` - Tree to edit
/// * `path` - Path to the changelog file
/// * `summary` - Entry to add
/// * `maintainer` - Maintainer details; tuple of fullname and email
pub fn add_changelog_entry(
    tree: &dyn MutableTree,
    path: &Path,
    summary: &[&str],
    maintainer: Option<(&str, &str)>,
    timestamp: Option<chrono::DateTime<chrono::FixedOffset>>,
    urgency: Option<&str>,
) {
    // TODO(jelmer): This logic should ideally be in python-debian.
    let f = tree.get_file_text(path).unwrap();
    let contents = Python::with_gil(|py| {
        let m = py.import("debian.changelog").unwrap();
        let Changelog = m.getattr("Changelog").unwrap();
        let cl = Changelog.call0().unwrap();
        let kwargs = PyDict::new(py);
        kwargs.set_item("max_blocks", py.None()).unwrap();
        kwargs.set_item("allow_empty_author", true).unwrap();
        kwargs.set_item("strict", false).unwrap();
        cl.call_method("parse_changelog", (f,), Some(kwargs))
            .unwrap();

        let m = py.import("debmutate.changelog").unwrap();
        let _changelog_add_entry = m.getattr("_changelog_add_entry").unwrap();
        let kwargs = PyDict::new(py);
        kwargs.set_item("summary", summary).unwrap();
        kwargs.set_item("maintainer", maintainer).unwrap();
        kwargs.set_item("timestamp", timestamp).unwrap();
        kwargs.set_item("urgency", urgency).unwrap();
        _changelog_add_entry.call((cl,), Some(kwargs)).unwrap();
        cl.call_method0("__bytes__")
            .unwrap()
            .extract::<Vec<u8>>()
            .unwrap()
    });
    tree.put_file_bytes_non_atomic(path, &contents).unwrap();
}

pub fn is_debcargo_package(tree: &dyn Tree, subpath: &Path) -> bool {
    let control_path = subpath.join("debian").join("debcargo.toml");
    tree.has_filename(&control_path)
}

pub fn get_maintainer_from_env(
    env: std::collections::HashMap<String, String>,
) -> Option<(String, String)> {
    Python::with_gil(|py| {
        let debian_changelog = py.import("debian.changelog").unwrap();
        let get_maintainer = debian_changelog.getattr("get_maintainer").unwrap();

        let os = py.import("os").unwrap();
        let active_env = os.getattr("environ").unwrap();
        let old_env_items = active_env.call_method0("items").unwrap();
        active_env.call_method1("update", (env,)).unwrap();
        let result = get_maintainer.call0().unwrap();

        active_env.call_method0("clear").unwrap();
        active_env.call_method1("update", (old_env_items,)).unwrap();

        result.extract().unwrap()
    })
}
