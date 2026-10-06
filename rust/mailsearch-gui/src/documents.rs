// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
// Remember Rust desktop archive paths outside the canonical archive.
// Recent entries are canonical identities, deduplicated and bounded to ten paths.
// Saving uses a synced temporary file and atomic replacement in the same folder.
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
        let mut value = Self::load(path)?;
        value.recent.retain(|p| p != &archive);
        value.recent.insert(0, archive);
        value.recent.truncate(10);
        let parent = path.parent().context("Missing preferences directory")?;
        std::fs::create_dir_all(parent)?;
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
