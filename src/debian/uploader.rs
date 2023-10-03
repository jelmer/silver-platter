use std::path::Path;
use trivialdb as tdb;

pub struct LastAttemptDatabase {
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

    pub fn get(&self, package: &str) -> Option<chrono::DateTime<chrono::FixedOffset>> {
        let key = package.to_string().into_bytes();
        self.db.fetch(&key).unwrap().map(|value| {
            let value = String::from_utf8(value).unwrap();
            chrono::DateTime::parse_from_rfc3339(&value).unwrap()
        })
    }

    pub fn set(&mut self, package: &str, value: chrono::DateTime<chrono::FixedOffset>) {
        let key = package.to_string().into_bytes();
        let value = value.to_rfc3339();
        self.db.store(&key, value.as_bytes(), None).unwrap();
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
        Err(std::io::Error::new(
            std::io::ErrorKind::Other,
            "debsign failed",
        ))
    } else {
        Ok(())
    }
}

pub fn dput_changes(path: &Path) -> Result<(), std::io::Error> {
    let status = std::process::Command::new("dput")
        .arg(path.file_name().unwrap().to_string_lossy().to_string())
        .current_dir(path.parent().unwrap())
        .status()?;

    if !status.success() {
        Err(std::io::Error::new(
            std::io::ErrorKind::Other,
            "dput failed",
        ))
    } else {
        Ok(())
    }
}
