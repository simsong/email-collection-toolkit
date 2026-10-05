// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
// Perform explicit desktop exports and OS actions for the Rust reader.
// Dialog choices are the only source of user-selected export destinations.
// Canonical archive paths and existing files are never overwritten by exports.
// Temporary attachment files live until the owning reader closes.
// External links admit only HTTP, HTTPS and mailto, never shell fragments.
// Subprocess arguments are passed directly without invoking a command shell.
use anyhow::{ensure, Context, Result};
use std::{io::Write, path::Path, process::Command};

pub(crate) fn spawn(command: &mut Command) -> Result<()> {
    let mut child = command.spawn()?;
    std::thread::spawn(move || {
        let _ = child.wait();
    });
    Ok(())
}

pub(crate) fn export(root: &Path, destination: &Path, bytes: &[u8]) -> Result<()> {
    let parent = destination
        .parent()
        .context("Missing destination directory")?
        .canonicalize()?;
    ensure!(
        !parent.starts_with(root.canonicalize()?),
        "Choose an export destination outside this archive"
    );
    ensure!(
        !destination.exists(),
        "Destination already exists; choose a new filename to preserve existing files"
    );
    let mut output = tempfile::NamedTempFile::new_in(parent)?;
    output.write_all(bytes)?;
    output.as_file().sync_all()?;
    output
        .persist_noclobber(destination)
        .context("Save export without overwriting an existing file")?;
    Ok(())
}
pub(crate) fn open_link(value: &str) -> Result<()> {
    let url = url::Url::parse(value)?;
    ensure!(
        ["https", "http", "mailto"].contains(&url.scheme()),
        "Unsupported link scheme"
    );
    open(value)
}
pub(crate) fn open(value: &str) -> Result<()> {
    #[cfg(target_os = "macos")]
    {
        spawn(Command::new("/usr/bin/open").arg("--").arg(value))?;
    }
    #[cfg(target_os = "windows")]
    {
        open::that_detached(value)?;
    }
    #[cfg(not(any(target_os = "windows", target_os = "macos")))]
    {
        spawn(Command::new("xdg-open").arg(value))?;
    }
    Ok(())
}
pub(crate) fn copy(value: &str) -> Result<()> {
    arboard::Clipboard::new()?.set_text(value)?;
    Ok(())
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn exports_preserve_bytes_and_refuse_archive_and_existing_destinations() {
        // Export requirement: an explicit copy must never rewrite canonical or source bytes.
        let directory = tempfile::tempdir().unwrap();
        let archive = directory.path().join("archive");
        std::fs::create_dir(&archive).unwrap();
        let target = directory.path().join("saved.eml");
        let bytes = b"Subject: original\r\n\r\nFrom literal\x00\xff";
        export(&archive, &target, bytes).unwrap();
        assert_eq!(std::fs::read(&target).unwrap(), bytes);
        assert!(export(&archive, &target, b"changed").is_err());
        assert!(export(&archive, &archive.join("new.eml"), bytes).is_err());
        assert!(open_link("file:///etc/passwd").is_err());
    }
}
