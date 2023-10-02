use debversion::Version;
use log::{debug, info};
use std::path::Path;
use trivialdb as tdb;

struct LastAttemptDatabase {
    db: tdb::Tdb,
}

impl LastAttemptDatabase {
    pub fn open(path: &Path) -> Self {
        Self {
            db: tdb::Tdb::open(
                path,
                None,
                tdb::Flags::empty(),
                libc::O_RDWR | libc::O_CREAT,
            )
            .unwrap(),
        }
    }
}

impl Default for LastAttemptDatabase {
    fn default() -> Self {
        let xdg_dirs = xdg::BaseDirectories::with_prefix("silver-platter").unwrap();
        let last_attempt_path = xdg_dirs.place_data_file("last-upload-attempt.tdb").unwrap();
        Self::open(last_attempt_path.as_path())
    }
}

pub fn debsign(path: &Path, keyid: Option<&str>) -> Result<(), std::io::Error> {
    let mut args = vec!["debsign".to_string()];
    if let Some(keyid) = keyid {
        args.push(format!("-k{}", keyid));
    }
    args.push(path.file_name().unwrap().to_string_lossy().to_string());
    let status = std::process::Command::new("debsign")
        .args(&args)
        .current_dir(path.parent().unwrap())
        .status()?;

    if !status.success() {
        return Err(std::io::Error::new(
            std::io::ErrorKind::Other,
            "debsign failed",
        ));
    } else {
        Ok(())
    }
}
