use breezyshim::tree::{MutableTree, Tree, WorkingTree};
use debian_changelog::{ChangeLog, Urgency};

use pyo3::prelude::*;
use pyo3::types::PyDict;

use std::path::Path;

pub const DEFAULT_BUILDER: &str = "sbuild --no-clean-source";

pub mod codemod;
pub mod uploader;
pub mod vcswatch;

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
    urgency: Option<Urgency>,
) {
    let maintainer = if let Some(maintainer) = maintainer {
        Some(maintainer.clone())
    } else {
        debian_changelog::get_maintainer()
    };
    // TODO(jelmer): This logic should ideally be in python-debian.
    let f = tree.get_file(path).unwrap();

    let mut cl = ChangeLog::read_relaxed(f).unwrap();

    let summary = vec![format!("* {}", summary[0])]
        .into_iter()
        .chain(summary[1..].iter().map(|l| format!("  {}", l)))
        .collect::<Vec<_>>();

    cl.auto_add_change(
        summary
            .iter()
            .map(|l| l.as_str())
            .collect::<Vec<_>>()
            .as_slice(),
        maintainer.unwrap(),
        timestamp,
        urgency,
    );
    tree.put_file_bytes_non_atomic(path, cl.to_string().as_bytes())
        .unwrap();
}

pub fn is_debcargo_package(tree: &dyn Tree, subpath: &Path) -> bool {
    let control_path = subpath.join("debian").join("debcargo.toml");
    tree.has_filename(&control_path)
}

#[cfg(test)]
mod tests {
    use super::*;
    use breezyshim::controldir::ControlDir;
    use breezyshim::tree::WorkingTree;
    use std::path::Path;

    pub fn make_branch_and_tree(path: &std::path::Path) -> WorkingTree {
        breezyshim::init().unwrap();
        let path = path.canonicalize().unwrap();
        let url = url::Url::from_file_path(path).unwrap();
        let branch = ControlDir::create_branch_convenience(&url).unwrap();
        branch.controldir().open_workingtree().unwrap()
    }

    #[test]
    fn test_edit_existing_new_author() {
        let td = tempfile::tempdir().unwrap();
        let tree = make_branch_and_tree(td.path());
        std::fs::create_dir_all(td.path().join("debian")).unwrap();
        std::fs::write(
            td.path().join("debian/changelog"),
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
"#,
            std::fs::read_to_string(td.path().join("debian/changelog")).unwrap(),
        );
    }

    #[test]
    fn test_edit_existing_multi_new_author() {
        let td = tempfile::tempdir().unwrap();
        let tree = make_branch_and_tree(td.path());
        std::fs::create_dir_all(td.path().join("debian")).unwrap();
        std::fs::write(
            td.path().join("debian/changelog"),
            r#"lintian-brush (0.35) UNRELEASED; urgency=medium

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

  [ Jane Example ]
  * Support updating templated debian/control files that use cdbs
    template.

  [ Joe Example ]
  * Another change

  [ Jane Example ]
  * Add a foo

 -- Joe Example <joe@example.com>  Fri, 04 Oct 2019 02:36:13 +0000
"#,
            std::fs::read_to_string(td.path().join("debian/changelog")).unwrap()
        );
    }

    #[test]
    fn test_edit_existing_existing_author() {
        let td = tempfile::tempdir().unwrap();
        let tree = make_branch_and_tree(td.path());
        std::fs::create_dir_all(td.path().join("debian")).unwrap();
        std::fs::write(
            td.path().join("debian/changelog"),
            r#"lintian-brush (0.35) UNRELEASED; urgency=medium

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
            Some(&("Joe Example".to_string(), "joe@example.com".to_string())),
            None,
            None,
        );
        assert_eq!(
            r#"lintian-brush (0.35) UNRELEASED; urgency=medium

  * Support updating templated debian/control files that use cdbs
    template.
  * Add a foo

 -- Joe Example <joe@example.com>  Fri, 04 Oct 2019 02:36:13 +0000
"#,
            std::fs::read_to_string(td.path().join("debian/changelog")).unwrap()
        );
    }

    #[test]
    fn test_add_new() {
        let td = tempfile::tempdir().unwrap();
        let tree = make_branch_and_tree(td.path());
        std::fs::create_dir_all(td.path().join("debian")).unwrap();
        std::fs::write(
            td.path().join("debian/changelog"),
            r#"lintian-brush (0.35) unstable; urgency=medium

  * Support updating templated debian/control files that use cdbs
    template.

 -- Joe Example <joe@example.com>  Fri, 04 Oct 2019 02:36:13 +0000
"#,
        )
        .unwrap();
        tree.add(&[(Path::new("debian")), (Path::new("debian/changelog"))])
            .unwrap();
        std::env::set_var("DEBCHANGE_VENDOR", "debian");
        let timestamp = chrono::DateTime::<chrono::FixedOffset>::parse_from_rfc3339(
            "2020-05-24T15:27:26+00:00",
        )
        .unwrap();
        add_changelog_entry(
            &tree,
            Path::new("debian/changelog"),
            &["Add a foo"],
            Some(&(
                String::from("Jane Example"),
                String::from("jane@example.com"),
            )),
            Some(timestamp),
            None,
        );
        assert_eq!(
            r#"lintian-brush (0.36) UNRELEASED; urgency=low

  * Add a foo

 -- Jane Example <jane@example.com>  Sun, 24 May 2020 15:27:26 +0000

lintian-brush (0.35) unstable; urgency=medium

  * Support updating templated debian/control files that use cdbs
    template.

 -- Joe Example <joe@example.com>  Fri, 04 Oct 2019 02:36:13 +0000
"#,
            std::fs::read_to_string(td.path().join("debian/changelog")).unwrap()
        );
    }

    #[test]
    fn test_edit_broken_first_line() {
        let td = tempfile::tempdir().unwrap();
        let tree = make_branch_and_tree(td.path());
        std::fs::create_dir_all(td.path().join("debian")).unwrap();
        std::fs::write(
            td.path().join("debian/changelog"),
            r#"THIS IS NOT A PARSEABLE LINE
lintian-brush (0.35) UNRELEASED; urgency=medium

  * Support updating templated debian/control files that use cdbs
    template.

 -- Joe Example <joe@example.com>  Fri, 04 Oct 2019 02:36:13 +0000
"#,
        )
        .unwrap();
        tree.add(&[Path::new("debian"), Path::new("debian/changelog")])
            .unwrap();
        add_changelog_entry(
            &tree,
            Path::new("debian/changelog"),
            &["Add a foo", "+ Bar"],
            Some(&("Jane Example".to_string(), "joe@example.com".to_string())),
            None,
            None,
        );
        assert_eq!(
            r#"THIS IS NOT A PARSEABLE LINE
lintian-brush (0.35) UNRELEASED; urgency=medium

  [ Joe Example ]
  * Support updating templated debian/control files that use cdbs
    template.

  [ Jane Example ]
  * Add a foo
    + Bar

 -- Joe Example <joe@example.com>  Fri, 04 Oct 2019 02:36:13 +0000
"#,
            std::fs::read_to_string(td.path().join("debian/changelog")).unwrap()
        );
    }

    #[test]
    fn test_add_long_line() {
        let td = tempfile::tempdir().unwrap();
        let tree = make_branch_and_tree(td.path());
        std::fs::create_dir_all(td.path().join("debian")).unwrap();
        std::fs::write(
            td.path().join("debian/changelog"),
            r#"lintian-brush (0.35) UNRELEASED; urgency=medium

  * Support updating templated debian/control files that use cdbs
    template.

 -- Joe Example <joe@example.com>  Fri, 04 Oct 2019 02:36:13 +0000
"#
            .as_bytes(),
        )
        .unwrap();
        tree.add(&[Path::new("debian"), Path::new("debian/changelog")])
            .unwrap();
        add_changelog_entry(
            &tree,
            Path::new("debian/changelog"),
            &[
                "This is adding a very long sentence that is longer than would fit on a single line in a 80-character-wide line."
            ],
            Some(&("Joe Example".to_string(), "joe@example.com".to_string())),
            None,
            None,
        );
        assert_eq!(
            r#"lintian-brush (0.35) UNRELEASED; urgency=medium

  * Support updating templated debian/control files that use cdbs
    template.
  * This is adding a very long sentence that is longer than would fit on a
    single line in a 80-character-wide line.

 -- Joe Example <joe@example.com>  Fri, 04 Oct 2019 02:36:13 +0000
"#,
            std::fs::read_to_string(td.path().join("debian/changelog")).unwrap()
        );
    }

    #[test]
    fn test_add_long_subline() {
        let td = tempfile::tempdir().unwrap();
        let tree = make_branch_and_tree(td.path());
        std::fs::create_dir_all(td.path().join("debian")).unwrap();
        std::fs::write(
            td.path().join("debian/changelog"),
            r#"lintian-brush (0.35) UNRELEASED; urgency=medium

  * Support updating templated debian/control files that use cdbs
    template.

 -- Joe Example <joe@example.com>  Fri, 04 Oct 2019 02:36:13 +0000
"#
            .as_bytes(),
        )
        .unwrap();
        tree.add(&[Path::new("debian"), Path::new("debian/changelog")])
            .unwrap();
        add_changelog_entry(
            &tree,
            Path::new("debian/changelog"),
            &[
                "This is the main item.",
                "+ This is adding a very long sentence that is longer than would fit on a single line in a 80-character-wide line.",
            ],
            Some(&("Joe Example".to_string(), "joe@example.com".to_string())), None, None
        );
        assert_eq!(
            r#"lintian-brush (0.35) UNRELEASED; urgency=medium

  * Support updating templated debian/control files that use cdbs
    template.
  * This is the main item.
    + This is adding a very long sentence that is longer than would fit on a
      single line in a 80-character-wide line.

 -- Joe Example <joe@example.com>  Fri, 04 Oct 2019 02:36:13 +0000
"#,
            std::fs::read_to_string(td.path().join("debian/changelog")).unwrap()
        );
    }

    #[test]
    fn test_trailer_only() {
        let td = tempfile::tempdir().unwrap();
        let tree = make_branch_and_tree(td.path());
        std::fs::create_dir_all(td.path().join("debian")).unwrap();
        std::fs::write(
            td.path().join("debian/changelog"),
            r#"lintian-brush (0.35) unstable; urgency=medium

  * This line already existed.

 -- 
"#
            .as_bytes(),
        )
        .unwrap();
        tree.add(&[Path::new("debian"), Path::new("debian/changelog")])
            .unwrap();
        add_changelog_entry(
            &tree,
            Path::new("debian/changelog"),
            &["And this one is new."],
            Some(&("Jane Example".to_string(), "joe@example.com".to_string())),
            None,
            None,
        );
        assert_eq!(
            r#"lintian-brush (0.35) unstable; urgency=medium

  * This line already existed.
  * And this one is new.

 -- 
"#,
            std::fs::read_to_string(td.path().join("debian/changelog")).unwrap()
        );
    }

    #[test]
    fn test_trailer_only_existing_author() {
        let td = tempfile::tempdir().unwrap();
        let tree = make_branch_and_tree(td.path());
        std::fs::create_dir_all(td.path().join("debian")).unwrap();
        std::fs::write(
            td.path().join("debian/changelog"),
            r#"lintian-brush (0.35) unstable; urgency=medium

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
        add_changelog_entry(
            &tree,
            Path::new("debian/changelog"),
            &["And this one is new."],
            Some(&("Joe Example".to_string(), "joe@example.com".to_string())),
            None,
            None,
        );
        assert_eq!(
            r#"lintian-brush (0.35) unstable; urgency=medium

  * This line already existed.

  [ Jane Example ]
  * And this one has an existing author.

  [ Joe Example ]
  * And this one is new.

 -- 
"#,
            std::fs::read_to_string(td.path().join("debian/changelog")).unwrap()
        );
    }
}

pub fn install_built_package(
    local_tree: &WorkingTree,
    subpath: &Path,
    build_target_dir: &Path,
) -> Result<(), Box<dyn std::error::Error>> {
    let abspath = local_tree
        .abspath(subpath)
        .unwrap()
        .join("debian/changelog");

    let cl = ChangeLog::read_path(&abspath)?;

    let first_entry = cl.entries().next().unwrap();

    let package = first_entry.package().unwrap();
    let version = first_entry.version().unwrap();

    let mut non_epoch_version = version.upstream_version.clone();
    if let Some(debian_version) = &version.debian_revision {
        non_epoch_version.push_str(&format!("-{}", debian_version));
    }

    let re_pattern = format!(
        "{}_{}_.*\\.changes",
        regex::escape(&package),
        regex::escape(&non_epoch_version)
    );
    let c = regex::Regex::new(&re_pattern)?;

    for entry in std::fs::read_dir(build_target_dir)? {
        let entry = entry?;
        let file_name = entry.file_name().into_string().unwrap_or_default();
        if !c.is_match(&file_name) {
            continue;
        }

        let path = entry.path();
        let contents = std::fs::read(&path)?;

        let binary: Option<String> = Python::with_gil(|py| {
            let m = py.import("debian.deb822")?;
            let changes = m.getattr("Changes")?.call1((contents,))?;

            changes.call_method1("get", ("Binary",))?.extract()
        })?;

        if binary.is_some() {
            std::process::Command::new("debi")
                .arg(entry.path())
                .status()?;
        }
    }

    Ok(())
}

/// Build a debian package in a directory.
///
/// # Arguments
/// * `tree` - Working tree
/// * `subpath` - Subpath to build in
/// * `builder` - Builder command (e.g. 'sbuild', 'debuild')
/// * `result_dir` - Directory to copy results to
pub fn build(
    tree: &WorkingTree,
    subpath: &Path,
    builder: Option<&str>,
    result_dir: Option<&Path>,
) -> PyResult<()> {
    let builder = builder.unwrap_or(DEFAULT_BUILDER);

    let path = tree.abspath(subpath).unwrap();

    // TODO(jelmer): Refactor brz-debian so it's not necessary
    // to call out to cmd_builddeb, but to lower-level
    // functions instead.
    Python::with_gil(|py| {
        let m = py.import("breezy.plugins.debian.cmds")?;
        let cmd_builddeb = m.getattr("cmd_builddeb")?;
        let kwargs = PyDict::new(py);
        kwargs.set_item("builder", builder)?;
        kwargs.set_item("result_dir", result_dir)?;
        cmd_builddeb.call((path,), Some(kwargs))?;
        Ok(())
    })
}

pub fn gbp_dch(path: &std::path::Path) -> Result<(), std::io::Error> {
    let mut cmd = std::process::Command::new("gbp");
    cmd.arg("dch").arg("--ignore-branch");
    cmd.current_dir(path);
    let status = cmd.status()?;
    if !status.success() {
        return Err(std::io::Error::new(
            std::io::ErrorKind::Other,
            format!("gbp dch failed: {}", status),
        ));
    }
    Ok(())
}

pub fn find_last_release_revid(
    branch: &dyn breezyshim::branch::Branch,
    version: debversion::Version,
) -> PyResult<breezyshim::revisionid::RevisionId> {
    Python::with_gil(|py| {
        let m = py.import("breezy.plugins.debian.import_dsc")?;
        let db = m
            .getattr("DistributionBranch")?
            .call1((branch.to_object(py), py.None()))?;
        db.call_method1("revid_of_version", (version,))?.extract()
    })
}
