use breezyshim::tree::{MutableTree, Tree, WorkingTree};
use pyo3::prelude::*;
use pyo3::types::PyDict;
use std::path::Path;

pub mod codemod;

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
    tree.has_filename(&template_control_path)
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ChangelogBehaviour {
    pub update_changelog: bool,
    pub explanation: String,
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
    maintainer: Option<&(String, String)>,
    timestamp: Option<chrono::DateTime<chrono::FixedOffset>>,
    urgency: Option<&str>,
) {
    let urgency = urgency.unwrap_or("low");
    // TODO(jelmer): This logic should ideally be in python-debian.
    let f = tree.get_file_text(path).unwrap();
    let contents = Python::with_gil(|py| {
        let m = py.import("debian.changelog").unwrap();
        let cl_cls = m.getattr("Changelog").unwrap();
        let cl = cl_cls.call0().unwrap();
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

#[cfg(test)]
mod tests {
    use super::*;
    use breezyshim::controldir::ControlDir;
    use breezyshim::tree::WorkingTree;
    use std::path::Path;

    pub fn make_branch_and_tree(path: &std::path::Path) -> WorkingTree {
        let path = path.canonicalize().unwrap();
        let url = url::Url::from_file_path(path).unwrap();
        let branch = ControlDir::create_branch_convenience(&url).unwrap();
        branch.controldir().open_workingtree().unwrap()
    }

    #[test]
    fn test_edit_existing_new_author() {
        let tree = make_branch_and_tree(Path::new("."));
        std::fs::create_dir_all("debian").unwrap();
        std::fs::write(
            "debian/changelog",
            r#"lintian-brush (0.35) UNRELEASED; urgency=medium

  * Initial change.
  * Support updating templated debian/control files that use cdbs
    template.

 -- Joe Example <joe@example.com>  Fri, 04 Oct 2019 02:36:13 +0000
"#
            .as_bytes(),
        )
        .unwrap();
        tree.add(&[(Path::new("debian")), (Path::new("debian/changelog"))])
            .unwrap();
        add_changelog_entry(
            &tree,
            Path::new("debian/changelog"),
            &["Add a foo"],
            Some(&("Jane Example".to_string(), "jane@example.com".to_string())),
            None,
            None,
        );
        assert_eq!(
            r#"lintian-brush (0.35) UNRELEASED; urgency=medium

  [ Joe Example ]
  * Initial change.
  * Support updating templated debian/control files that use cdbs
    template.

  [ Jane Example ]
  * Add a foo

 -- Joe Example <joe@example.com>  Fri, 04 Oct 2019 02:36:13 +0000
"#
            .as_bytes(),
            std::fs::read("debian/changelog").unwrap(),
        );
    }

    #[test]
    fn test_edit_existing_multi_new_author() {
        let tree = make_branch_and_tree(Path::new("."));
        std::fs::create_dir_all("debian").unwrap();
        std::fs::write(
            "debian/changelog",
            r#"\
lintian-brush (0.35) UNRELEASED; urgency=medium

  [ Jane Example ]
  * Support updating templated debian/control files that use cdbs
    template.

  [ Joe Example ]
  * Another change

 -- Joe Example <joe@example.com>  Fri, 04 Oct 2019 02:36:13 +0000
"#,
        )
        .unwrap();
        tree.add(&[(Path::new("debian")), (Path::new("debian/changelog"))])
            .unwrap();
        std::env::set_var("DEBFULLNAME", "Jane Example");
        std::env::set_var("DEBEMAIL", "jane@example.com");
        add_changelog_entry(
            &tree,
            Path::new("debian/changelog"),
            &["Add a foo"],
            None,
            None,
            None,
        );
        assert_eq!(
            r#"\
lintian-brush (0.35) UNRELEASED; urgency=medium

  [ Jane Example ]
  * Support updating templated debian/control files that use cdbs
    template.

  [ Joe Example ]
  * Another change

  [ Jane Example ]
  * Add a foo

 -- Joe Example <joe@example.com>  Fri, 04 Oct 2019 02:36:13 +0000
"#
            .as_bytes(),
            std::fs::read("debian/changelog").unwrap()
        );
    }

    #[test]
    fn test_edit_existing_existing_author() {
        let tree = make_branch_and_tree(Path::new("."));
        std::fs::create_dir_all("debian").unwrap();
        std::fs::write(
            "debian/changelog",
            r#"\
lintian-brush (0.35) UNRELEASED; urgency=medium

  * Support updating templated debian/control files that use cdbs
    template.

 -- Joe Example <joe@example.com>  Fri, 04 Oct 2019 02:36:13 +0000
"#,
        )
        .unwrap();
        tree.add(&[(Path::new("debian")), (Path::new("debian/changelog"))])
            .unwrap();
        add_changelog_entry(
            &tree,
            Path::new("debian/changelog"),
            &["Add a foo"],
            None,
            None,
            None,
        );
        assert_eq!(
            r#"\
lintian-brush (0.35) UNRELEASED; urgency=medium

  * Support updating templated debian/control files that use cdbs
    template.
  * Add a foo

 -- Joe Example <joe@example.com>  Fri, 04 Oct 2019 02:36:13 +0000
"#
            .as_bytes(),
            std::fs::read("debian/changelog").unwrap()
        );
    }

    #[test]
    fn test_add_new() {
        let tree = make_branch_and_tree(Path::new("."));
        std::fs::create_dir_all("debian").unwrap();
        std::fs::write(
            "debian/changelog",
            r#"\
lintian-brush (0.35) unstable; urgency=medium

  * Support updating templated debian/control files that use cdbs
    template.

 -- Joe Example <joe@example.com>  Fri, 04 Oct 2019 02:36:13 +0000
"#,
        )
        .unwrap();
        tree.add(&[(Path::new("debian")), (Path::new("debian/changelog"))])
            .unwrap();
        std::env::set_var("DEBFULLNAME", "Jane Example");
        std::env::set_var("DEBEMAIL", "jane@example.com");
        std::env::set_var("DEBCHANGE_VENDOR", "debian");
        let timestamp = chrono::DateTime::<chrono::FixedOffset>::parse_from_rfc3339(
            "2020-05-24T15:27:26+00:00",
        )
        .unwrap();
        add_changelog_entry(
            &tree,
            Path::new("debian/changelog"),
            &["Add a foo"],
            None,
            Some(timestamp),
            None,
        );
        assert_eq!(
            r#"\
lintian-brush (0.36) UNRELEASED; urgency=medium

  * Add a foo

 -- Jane Example <jane@example.com>  Sun, 24 May 2020 15:27:26 -0000

lintian-brush (0.35) unstable; urgency=medium

  * Support updating templated debian/control files that use cdbs
    template.

 -- Joe Example <joe@example.com>  Fri, 04 Oct 2019 02:36:13 +0000
"#
            .as_bytes(),
            std::fs::read("debian/changelog").unwrap()
        );
    }

    #[test]
    fn test_edit_broken_first_line() {
        let tree = make_branch_and_tree(Path::new("."));
        std::fs::create_dir_all("debian").unwrap();
        std::fs::write(
            "debian/changelog",
            r#"\
THIS IS NOT A PARSEABLE LINE
lintian-brush (0.35) UNRELEASED; urgency=medium

  * Support updating templated debian/control files that use cdbs
    template.

 -- Joe Example <joe@example.com>  Fri, 04 Oct 2019 02:36:13 +0000
"#,
        )
        .unwrap();
        tree.add(&[Path::new("debian"), Path::new("debian/changelog")])
            .unwrap();
        std::env::set_var("DEBFULLNAME", "Jane Example");
        std::env::set_var("DEBEMAIL", "jane@example.com");
        add_changelog_entry(
            &tree,
            Path::new("debian/changelog"),
            &["Add a foo", "+ Bar"],
            None,
            None,
            None,
        );
        assert_eq!(
            r#"\
THIS IS NOT A PARSEABLE LINE
lintian-brush (0.35) UNRELEASED; urgency=medium

  [ Joe Example ]
  * Support updating templated debian/control files that use cdbs
    template.

  [ Jane Example ]
  * Add a foo
    + Bar

 -- Joe Example <joe@example.com>  Fri, 04 Oct 2019 02:36:13 +0000
"#
            .as_bytes(),
            std::fs::read("debian/changelog").unwrap()
        );
    }

    #[test]
    fn test_add_long_line() {
        let tree = make_branch_and_tree(Path::new("."));
        std::fs::create_dir_all("debian").unwrap();
        std::fs::write(
            "debian/changelog",
            r#"\
lintian-brush (0.35) UNRELEASED; urgency=medium

  * Support updating templated debian/control files that use cdbs
    template.

 -- Joe Example <joe@example.com>  Fri, 04 Oct 2019 02:36:13 +0000
"#
            .as_bytes(),
        )
        .unwrap();
        tree.add(&[Path::new("debian"), Path::new("debian/changelog")])
            .unwrap();
        std::env::set_var("DEBFULLNAME", "Jane Example");
        std::env::set_var("DEBEMAIL", "joe@example.com");
        add_changelog_entry(
            &tree,
            Path::new("debian/changelog"),
            &[
                "This is adding a very long sentence that is longer than would fit on a single line in a 80-character-wide line."
            ],
            None,
            None,
            None,
        );
        assert_eq!(
            r#"\
lintian-brush (0.35) UNRELEASED; urgency=medium

  * Support updating templated debian/control files that use cdbs
    template.
  * This is adding a very long sentence that is longer than would fit on a
    single line in a 80-character-wide line.

 -- Joe Example <joe@example.com>  Fri, 04 Oct 2019 02:36:13 +0000
"#
            .as_bytes(),
            std::fs::read("debian/changelog").unwrap()
        );
    }

    #[test]
    fn test_add_long_subline() {
        let tree = make_branch_and_tree(Path::new("."));
        std::fs::create_dir_all("debian").unwrap();
        std::fs::write(
            "debian/changelog",
            r#"\
lintian-brush (0.35) UNRELEASED; urgency=medium

  * Support updating templated debian/control files that use cdbs
    template.

 -- Joe Example <joe@example.com>  Fri, 04 Oct 2019 02:36:13 +0000
"#
            .as_bytes(),
        )
        .unwrap();
        tree.add(&[Path::new("debian"), Path::new("debian/changelog")])
            .unwrap();
        std::env::set_var("DEBFULLNAME", "Jane Example");
        std::env::set_var("DEBEMAIL", "joe@example.com");
        add_changelog_entry(
            &tree,
            Path::new("debian/changelog"),
            &[
                "This is the main item.",
                "+ This is adding a very long sentence that is longer than would fit on a single line in a 80-character-wide line.",
            ],
            None, None, None
        );
        assert_eq!(
            r#"\
lintian-brush (0.35) UNRELEASED; urgency=medium

  * Support updating templated debian/control files that use cdbs
    template.
  * This is the main item.
    + This is adding a very long sentence that is longer than would fit on a
      single line in a 80-character-wide line.

 -- Joe Example <joe@example.com>  Fri, 04 Oct 2019 02:36:13 +0000
"#
            .as_bytes(),
            std::fs::read("debian/changelog").unwrap()
        );
    }

    #[test]
    fn test_trailer_only() {
        let tree = make_branch_and_tree(Path::new("."));
        std::fs::create_dir_all("debian").unwrap();
        std::fs::write(
            Path::new("debian/changelog"),
            r#"\
lintian-brush (0.35) unstable; urgency=medium

  * This line already existed.

 --
"#
            .as_bytes(),
        )
        .unwrap();
        tree.add(&[Path::new("debian"), Path::new("debian/changelog")])
            .unwrap();
        std::env::set_var("DEBFULLNAME", "Jane Example");
        std::env::set_var("DEBEMAIL", "joe@example.com");
        add_changelog_entry(
            &tree,
            Path::new("debian/changelog"),
            &["And this one is new."],
            None,
            None,
            None,
        );
        assert_eq!(
            r#"\
lintian-brush (0.35) unstable; urgency=medium

  * This line already existed.
  * And this one is new.

 --
"#
            .as_bytes(),
            std::fs::read("debian/changelog").unwrap()
        );
    }

    #[test]
    fn test_trailer_only_existing_author() {
        let tree = make_branch_and_tree(Path::new("."));
        std::fs::create_dir_all("debian").unwrap();
        std::fs::write(
            "debian/changelog",
            r#"\
lintian-brush (0.35) unstable; urgency=medium

  * This line already existed.

  [ Jane Example ]
  * And this one has an existing author.

 --
"#
            .as_bytes(),
        )
        .unwrap();
        tree.add(&[Path::new("debian"), Path::new("debian/changelog")])
            .unwrap();
        std::env::set_var("DEBFULLNAME", "Joe Example");
        std::env::set_var("DEBEMAIL", "joe@example.com");
        add_changelog_entry(
            &tree,
            Path::new("debian/changelog"),
            &["And this one is new."],
            None,
            None,
            None,
        );
        assert_eq!(
            r#"\
lintian-brush (0.35) unstable; urgency=medium

  * This line already existed.

  [ Jane Example ]
  * And this one has an existing author.

  [ Joe Example ]
  * And this one is new.

 --
"#
            .as_bytes(),
            std::fs::read("debian/changelog").unwrap()
        );
    }
}

#[cfg(test)]
mod get_maintainer_from_env_tests {
    use super::*;

    #[test]
    fn test_normal() {
        let t = get_maintainer_from_env(std::collections::HashMap::new());
        assert!(t.is_some());
    }

    #[test]
    fn test_env() {
        let mut d = std::collections::HashMap::new();
        d.insert("DEBFULLNAME".to_string(), "Jelmer".to_string());
        d.insert("DEBEMAIL".to_string(), "jelmer@example.com".to_string());
        let t = get_maintainer_from_env(d);
        assert_eq!(
            Some(("Jelmer".to_string(), "jelmer@example.com".to_string())),
            t
        );
    }
}
