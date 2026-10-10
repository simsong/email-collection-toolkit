// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
// Store reader presentation preferences separately from all email archives.
// Typed settings validate message text size and optional automatic update checks.
// User configuration directories follow the native platform convention.
// A stable OS lock protects reload, field merges and synced atomic replacement.
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
    #[serde(default)]
    pub update_channel: crate::update_policy::Channel,
}

impl Default for Preferences {
    fn default() -> Self {
        Self {
            message_font_size: 14,
            automatic_updates: option_env!("ECT_RELEASE_BUILD").is_some(),
            update_channel: crate::update_policy::Channel::default(),
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
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                #[derive(Deserialize)]
                struct Legacy {
                    update_channel: Option<crate::update_policy::Channel>,
                    #[serde(default = "legacy_automatic")]
                    automatic_update_checks: bool,
                }
                fn legacy_automatic() -> bool {
                    true
                }
                let parent = path
                    .parent()
                    .context("Preferences need a parent directory")?
                    .to_owned();
                #[cfg(target_os = "windows")]
                let parent = if settings_path().is_ok_and(|current| current == path) {
                    PathBuf::from(env::var_os("APPDATA").context("APPDATA is unavailable")?)
                        .join("Email Collection Toolkit")
                } else {
                    parent
                };
                let legacy = legacy_preferences_path(&parent)?;
                match fs::read(legacy) {
                    Ok(bytes) => {
                        let old: Legacy = serde_json::from_slice(&bytes)
                            .context("Invalid previous update preferences")?;
                        Ok(Self {
                            automatic_updates: old.automatic_update_checks,
                            update_channel: old.update_channel.unwrap_or_default(),
                            ..Self::default()
                        })
                    }
                    Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                        Ok(Self::default())
                    }
                    Err(error) => Err(error).context("Read previous update preferences"),
                }
            }
            Err(error) => Err(error).context("Read reader preferences"),
        }
    }

    fn save(&self, path: &Path) -> Result<()> {
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

    pub fn merge_save(&self, path: &Path, baseline: &Self) -> Result<Self> {
        self.validate()?;
        baseline.validate()?;
        // Native callbacks must not wait for another process's settings lock.
        let _lock = crate::documents::preferences_try_lock(path)?;
        let mut current = Self::load(path)?;
        if self.message_font_size != baseline.message_font_size {
            ensure!(
                current.message_font_size == baseline.message_font_size
                    || current.message_font_size == self.message_font_size,
                "Message text size changed in another window; reopen Preferences before saving"
            );
            current.message_font_size = self.message_font_size;
        }
        if self.automatic_updates != baseline.automatic_updates {
            current.automatic_updates = self.automatic_updates;
        }
        ensure!(
            current.update_channel == baseline.update_channel
                || self.update_channel == baseline.update_channel
                || self.update_channel == current.update_channel,
            "Update channel changed in another window; reopen Preferences"
        );
        if self.update_channel != baseline.update_channel {
            current.update_channel = self.update_channel;
        }
        current.save(path)?;
        Ok(current)
    }
}

fn legacy_preferences_path(parent: &Path) -> Result<PathBuf> {
    let current = parent.join("preferences.json");
    if !current.exists()
        && parent
            .file_name()
            .is_some_and(|name| name == "Email Collection Toolkit")
        && !parent.join("auth").exists()
    {
        return Ok(parent
            .parent()
            .context("Missing settings root")?
            .join("Mail Archiver/preferences.json"));
    }
    Ok(current)
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
    use std::{
        process::Command,
        thread,
        time::{Duration, Instant},
    };

    #[test]
    fn migration_retains_python_update_choices_and_old_rust_settings() {
        // Migration requirement: primary Rust delivery preserves an explicit
        // Python opt-out/channel, including settings under the previous name.
        let dir = tempfile::tempdir().unwrap();
        let current = dir.path().join("Email Collection Toolkit");
        let legacy = dir.path().join("Mail Archiver");
        std::fs::create_dir_all(&legacy).unwrap();
        let path = current.join("rust-reader.json");
        std::fs::write(legacy.join("preferences.json"), br#"{"automatic_update_checks":false,"update_channel":"release","unrelated":"retained"}"#).unwrap();
        let imported = Preferences::load(&path).unwrap();
        assert!(!imported.automatic_updates);
        assert_eq!(
            imported.update_channel,
            crate::update_policy::Channel::Release
        );
        assert!(!current.exists());
        // A current auth directory takes precedence over pre-rename settings,
        // matching Python discovery without modifying either directory.
        std::fs::create_dir_all(current.join("auth")).unwrap();
        assert_eq!(
            legacy_preferences_path(&current).unwrap(),
            current.join("preferences.json")
        );
        std::fs::remove_dir(current.join("auth")).unwrap();
        imported.save(&path).unwrap();
        std::fs::write(
            &path,
            br#"{"message_font_size":18,"automatic_updates":false}"#,
        )
        .unwrap();
        let old_rust = Preferences::load(&path).unwrap();
        assert_eq!(old_rust.message_font_size, 18);
        assert!(!old_rust.automatic_updates);
        assert_eq!(old_rust.update_channel, Default::default());
    }

    #[test]
    #[ignore = "fixture entry point invoked by concurrent_preference_dialogs_merge_under_process_lock"]
    fn preference_child() {
        let root = PathBuf::from(env::var_os("ECT_RUST_PREFERENCES_TEST_ROOT").unwrap());
        let edit = env::var("ECT_RUST_PREFERENCES_TEST_EDIT").unwrap();
        let baseline = Preferences::default();
        let desired = if edit == "font" {
            Preferences {
                message_font_size: 18,
                ..baseline.clone()
            }
        } else {
            Preferences {
                automatic_updates: true,
                ..baseline.clone()
            }
        };
        fs::write(root.join(format!("{edit}.ready")), []).unwrap();
        let deadline = Instant::now() + Duration::from_secs(15);
        loop {
            match desired.merge_save(&root.join("settings.json"), &baseline) {
                Ok(_) => break,
                Err(error)
                    if error.chain().any(|cause| {
                        matches!(
                            cause.downcast_ref::<std::fs::TryLockError>(),
                            Some(std::fs::TryLockError::WouldBlock)
                        )
                    }) =>
                {
                    assert!(
                        Instant::now() < deadline,
                        "Preferences lock was never released"
                    );
                    thread::sleep(Duration::from_millis(10));
                }
                Err(error) => panic!("{error:#}"),
            }
        }
        fs::write(root.join(format!("{edit}.done")), []).unwrap();
    }

    struct Child(std::process::Child);
    impl Drop for Child {
        fn drop(&mut self) {
            let _ = self.0.kill();
            let _ = self.0.wait();
        }
    }

    #[test]
    fn concurrent_preference_dialogs_merge_under_process_lock() {
        // Window-process requirement: two real stale dialogs must retain both
        // independent edits after encountering another process's stable lock.
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("settings.json");
        let lock = crate::documents::preferences_lock(&path).unwrap();
        let mut children: Vec<_> = ["font", "updates"]
            .into_iter()
            .map(|edit| {
                Child(
                    Command::new(std::env::current_exe().unwrap())
                        .args([
                            "--ignored",
                            "--exact",
                            "preferences::tests::preference_child",
                        ])
                        .env("ECT_RUST_PREFERENCES_TEST_ROOT", dir.path())
                        .env("ECT_RUST_PREFERENCES_TEST_EDIT", edit)
                        .spawn()
                        .unwrap(),
                )
            })
            .collect();
        let deadline = Instant::now() + Duration::from_secs(15);
        while ["font", "updates"]
            .iter()
            .any(|edit| !dir.path().join(format!("{edit}.ready")).exists())
        {
            assert!(
                Instant::now() < deadline,
                "Preference child never became ready"
            );
            thread::sleep(Duration::from_millis(10));
        }
        thread::sleep(Duration::from_millis(200));
        assert!(!path.exists());
        for child in &mut children {
            assert!(child.0.try_wait().unwrap().is_none());
        }
        drop(lock);
        while ["font", "updates"]
            .iter()
            .any(|edit| !dir.path().join(format!("{edit}.done")).exists())
        {
            assert!(
                Instant::now() < deadline,
                "Preference child did not complete"
            );
            thread::sleep(Duration::from_millis(10));
        }
        for child in &mut children {
            assert!(child.0.wait().unwrap().success());
        }
        assert_eq!(
            Preferences::load(&path).unwrap(),
            Preferences {
                message_font_size: 18,
                automatic_updates: true,
                update_channel: Default::default()
            }
        );
        assert!(path.with_extension("lock").exists());
    }

    #[test]
    fn stale_windows_merge_only_changed_fields_and_conflicts_preserve_disk() {
        // Native preferences requirement: separate stale dialogs retain unrelated
        // edits; competing changes to the same multi-valued field are explicit.
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("settings.json");
        let baseline = Preferences::default();
        let updates = Preferences {
            automatic_updates: true,
            ..baseline.clone()
        };
        updates.merge_save(&path, &baseline).unwrap();
        let font = Preferences {
            message_font_size: 18,
            ..baseline.clone()
        };
        assert_eq!(
            font.merge_save(&path, &baseline).unwrap(),
            Preferences {
                message_font_size: 18,
                automatic_updates: true,
                update_channel: Default::default()
            }
        );
        // Saving an unchanged stale dialog must retain every intervening edit.
        let merged = Preferences::load(&path).unwrap();
        assert_eq!(baseline.merge_save(&path, &baseline).unwrap(), merged);
        let before = fs::read(&path).unwrap();
        let conflicting = Preferences {
            message_font_size: 20,
            ..baseline.clone()
        };
        assert!(conflicting
            .merge_save(&path, &baseline)
            .unwrap_err()
            .to_string()
            .contains("changed in another window"));
        assert_eq!(fs::read(&path).unwrap(), before);
        let lock = crate::documents::preferences_lock(&path).unwrap();
        assert!(font.merge_save(&path, &baseline).is_err());
        assert_eq!(fs::read(&path).unwrap(), before);
        drop(lock);
        assert!(path.with_extension("lock").exists());
    }

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
            update_channel: Default::default(),
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
