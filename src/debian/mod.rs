use breezyshim::tree::{Tree, WorkingTree};
use pyo3::prelude::*;
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

pub fn guess_update_changelog(tree: &WorkingTree, debian_path: &Path) -> ChangelogBehaviour {
    Python::with_gil(|py| {
        let m = match py.import("lintian_brush") {
            Ok(m) => m,
            Err(e) => {
                log::warn!("Install lintian-brush to detect automatically whether the changelog should be updated.");
                return ChangelogBehaviour {
                    update_changelog: true,
                    explanation: format!(
                        "defaulting to updating changelog since lintian-brush is not installed: {}",
                        e
                    ),
                };
            }
        };

        let guess_update_changelog = m.getattr("guess_update_changelog").unwrap();

        let result = guess_update_changelog
            .call1((tree.to_object(py), debian_path))
            .unwrap();
        result.extract().unwrap()
    })
}
