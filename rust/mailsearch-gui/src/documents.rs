// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
// Remember Rust desktop archive paths outside the canonical archive.
// Recent entries are canonical identities, deduplicated and bounded to ten paths.
// An OS lock serializes load/update/synced atomic replacement across processes.
// Its stable companion file remains outside archives and is never unlinked.
// Unreadable or unknown preferences are reported instead of silently reset.
// Opening still validates an archive through the normal reader before use.
// Tests use private settings files and do not touch the user's recent documents.
use anyhow::{ensure, Context, Result};
use serde::{Deserialize, Serialize};
use std::{
    io::Write,
    path::{Path, PathBuf},
};
#[derive(Deserialize, Serialize)]
pub struct Documents {
    pub version: u8,
    pub recent: Vec<PathBuf>,
}
impl Default for Documents {
    fn default() -> Self {
        Self {
            version: 1,
            recent: Vec::new(),
        }
    }
}
pub(crate) fn preferences_lock(path: &Path) -> Result<std::fs::File> {
    std::fs::create_dir_all(path.parent().context("Missing preferences directory")?)?;
    // Keep the companion inode stable across atomic JSON replacements.
    let lock = std::fs::File::options()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .open(path.with_extension("lock"))?;
    lock.lock()?;
    Ok(lock)
}
impl Documents {
    pub fn recent_path(&self, index: usize) -> Option<&Path> {
        self.recent.get(index).map(PathBuf::as_path)
    }
    pub fn path() -> Result<PathBuf> {
        Ok(crate::preferences::settings_path()?.with_file_name("recent-archives.json"))
    }
    pub fn load(path: &Path) -> Result<Self> {
        match std::fs::read(path) {
            Ok(bytes) => {
                let value: Self = serde_json::from_slice(&bytes)?;
                ensure!(value.version == 1, "Unsupported recent archives version");
                Ok(value)
            }
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(Self::default()),
            Err(e) => Err(e.into()),
        }
    }
    pub fn remember(path: &Path, archive: &Path) -> Result<()> {
        let archive = archive.canonicalize()?;
        let parent = path.parent().context("Missing preferences directory")?;
        let _lock = preferences_lock(path)?;
        let mut value = Self::load(path)?;
        value.recent.retain(|p| p != &archive);
        value.recent.insert(0, archive);
        value.recent.truncate(10);
        let mut file = tempfile::NamedTempFile::new_in(parent)?;
        serde_json::to_writer_pretty(&mut file, &value)?;
        file.flush()?;
        file.as_file().sync_all()?;
        file.persist(path)?;
        Ok(())
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        process::Command,
        thread,
        time::{Duration, Instant},
    };

    struct Child(std::process::Child);
    impl Drop for Child {
        fn drop(&mut self) {
            let _ = self.0.kill();
            let _ = self.0.wait();
        }
    }

    #[test]
    #[ignore = "fixture entry point invoked by concurrent_remembers_reload_under_process_lock"]
    fn recent_document_child() {
        let root = PathBuf::from(std::env::var_os("ECT_RUST_RECENT_TEST_ROOT").unwrap());
        let name = std::env::var("ECT_RUST_RECENT_TEST_NAME").unwrap();
        std::fs::write(root.join(format!("{name}.ready")), []).unwrap();
        Documents::remember(&root.join("recent.json"), &root.join(&name)).unwrap();
        std::fs::write(root.join(format!("{name}.done")), []).unwrap();
    }

    #[test]
    fn concurrent_remembers_reload_under_process_lock() {
        // Separate window processes must wait, reload after locking and retain
        // every update, including one published while they wait for ownership.
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("recent.json");
        let lock = std::fs::File::options()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(path.with_extension("lock"))
            .unwrap();
        lock.lock().unwrap();
        let mut children = Vec::new();
        for name in ["first", "second"] {
            std::fs::create_dir(dir.path().join(name)).unwrap();
            children.push(Child(
                Command::new(std::env::current_exe().unwrap())
                    .args([
                        "--ignored",
                        "--exact",
                        "documents::tests::recent_document_child",
                    ])
                    .env("ECT_RUST_RECENT_TEST_ROOT", dir.path())
                    .env("ECT_RUST_RECENT_TEST_NAME", name)
                    .spawn()
                    .unwrap(),
            ));
        }
        let deadline = Instant::now() + Duration::from_secs(15);
        while ["first", "second"]
            .iter()
            .any(|n| !dir.path().join(format!("{n}.ready")).exists())
        {
            assert!(
                Instant::now() < deadline,
                "Child failed to enter recent update"
            );
            thread::sleep(Duration::from_millis(10));
        }
        thread::sleep(Duration::from_millis(200));
        for (name, child) in ["first", "second"].iter().zip(&mut children) {
            assert!(!dir.path().join(format!("{name}.done")).exists());
            assert!(child.0.try_wait().unwrap().is_none());
        }
        let baseline = dir.path().join("baseline");
        std::fs::create_dir(&baseline).unwrap();
        std::fs::write(
            &path,
            serde_json::to_vec(&Documents {
                version: 1,
                recent: vec![baseline.canonicalize().unwrap()],
            })
            .unwrap(),
        )
        .unwrap();
        drop(lock);
        for child in &mut children {
            loop {
                if let Some(status) = child.0.try_wait().unwrap() {
                    assert!(status.success());
                    break;
                }
                assert!(
                    Instant::now() < deadline,
                    "Child failed to finish recent update"
                );
                thread::sleep(Duration::from_millis(10));
            }
        }
        let mut actual = Documents::load(&path).unwrap().recent;
        actual.sort();
        let mut expected =
            ["baseline", "first", "second"].map(|n| dir.path().join(n).canonicalize().unwrap());
        expected.sort();
        assert_eq!(actual, expected);
        for name in ["baseline", "first", "second"] {
            assert_eq!(std::fs::read_dir(dir.path().join(name)).unwrap().count(), 0);
        }
        assert!(path.with_extension("lock").is_file());
    }
    #[test]
    fn remembers_unique_paths_without_touching_archives() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("prefs/recent.json");
        for n in 0..12 {
            let archive = dir.path().join(n.to_string());
            std::fs::create_dir(&archive).unwrap();
            Documents::remember(&path, &archive).unwrap();
            assert_eq!(std::fs::read_dir(archive).unwrap().count(), 0);
        }
        Documents::remember(&path, &dir.path().join("5")).unwrap();
        let value = Documents::load(&path).unwrap();
        assert_eq!(value.recent.len(), 10);
        assert_eq!(
            value.recent[0],
            dir.path().join("5").canonicalize().unwrap()
        );
        let displayed = value.recent[0].clone();
        Documents::remember(&path, &dir.path().join("6")).unwrap();
        assert_eq!(value.recent_path(0), Some(displayed.as_path()));
        assert_eq!(
            Documents::load(&path).unwrap().recent_path(0),
            Some(dir.path().join("6").canonicalize().unwrap().as_path())
        );
    }
}
