// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
// Store reader presentation preferences separately from all email archives.
// Typed settings validate message text size and optional automatic update checks.
// User configuration directories follow the native platform convention.
// Atomic replacement prevents a partial JSON file during ordinary save failures.
// Invalid or unreadable settings are reported rather than silently overwritten.
// Synthetic tests use private temporary directories and never user preferences.
use anyhow::{ensure, Context, Result};
use serde::{Deserialize, Serialize};
use std::{
    env, fs,
    io::Write,
    path::{Path, PathBuf},
};

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Preferences {
    pub message_font_size: u16,
    pub automatic_updates: bool,
}

impl Default for Preferences {
    fn default() -> Self {
        Self {
            message_font_size: 14,
            automatic_updates: false,
        }
    }
}

impl Preferences {
    pub fn validate(&self) -> Result<()> {
        ensure!(
            (10..=28).contains(&self.message_font_size),
            "Message text size must be between 10 and 28"
        );
        Ok(())
    }

    pub fn load(path: &Path) -> Result<Self> {
        match fs::read(path) {
            Ok(bytes) => {
                let preferences: Self =
                    serde_json::from_slice(&bytes).context("Invalid reader preferences")?;
                preferences.validate()?;
                Ok(preferences)
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(Self::default()),
            Err(error) => Err(error).context("Read reader preferences"),
        }
    }

    pub fn save(&self, path: &Path) -> Result<()> {
        self.validate()?;
        let parent = path
            .parent()
            .context("Preferences need a parent directory")?;
        fs::create_dir_all(parent)?;
        let mut pending = tempfile::NamedTempFile::new_in(parent)?;
        pending.write_all(&serde_json::to_vec_pretty(self)?)?;
        pending.as_file().sync_all()?;
        pending.persist(path).context("Save reader preferences")?;
        Ok(())
    }
}

pub fn settings_path() -> Result<PathBuf> {
    #[cfg(target_os = "windows")]
    let base = PathBuf::from(env::var_os("LOCALAPPDATA").context("LOCALAPPDATA is unavailable")?);
    #[cfg(target_os = "macos")]
    let base = PathBuf::from(env::var_os("HOME").context("HOME is unavailable")?)
        .join("Library/Application Support");
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    let base = match env::var_os("XDG_CONFIG_HOME") {
        Some(path) => PathBuf::from(path),
        None => PathBuf::from(env::var_os("HOME").context("HOME is unavailable")?).join(".config"),
    };
    ensure!(
        base.is_absolute(),
        "Configuration directory must be absolute"
    );
    Ok(base
        .join("Email Collection Toolkit")
        .join("rust-reader.json"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn preferences_round_trip_and_invalid_save_preserves_existing_bytes() {
        // Preferences must survive restart without touching archive data; bad
        // values and malformed existing JSON must not silently erase settings.
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("settings/reader.json");
        assert_eq!(Preferences::load(&path).unwrap(), Preferences::default());
        let mut preferences = Preferences {
            message_font_size: 18,
            automatic_updates: true,
        };
        preferences.save(&path).unwrap();
        assert_eq!(Preferences::load(&path).unwrap(), preferences);
        preferences.message_font_size = 20;
        preferences.save(&path).unwrap();
        assert_eq!(Preferences::load(&path).unwrap(), preferences);
        let before = fs::read(&path).unwrap();
        preferences.message_font_size = 100;
        assert!(preferences.save(&path).is_err());
        assert_eq!(fs::read(&path).unwrap(), before);
        fs::write(&path, b"invalid JSON").unwrap();
        assert!(Preferences::load(&path).is_err());
        assert_eq!(fs::read(&path).unwrap(), b"invalid JSON");
    }
}
