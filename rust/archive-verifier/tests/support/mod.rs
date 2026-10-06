// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
//! Drive the real importer from Rust without importing Python test helpers.
//! Use disposable archives, bounded subprocesses and typed status snapshots.
//! Read source inventories and database observations independently in Rust.
//! Preserve the reviewed public/private corpus expectation split.
//! Retain failure artifacts locally while successful runs clean up their files.
//! Shared by the migrated corpus and synthetic lifecycle acceptance tests.

use anyhow::{bail, ensure, Context, Result};
use archive_verifier::{count, file_sha256, open_catalog, verify_archive};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;
use std::fs::{self, File};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

pub fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .canonicalize()
        .unwrap()
}

pub fn scenario(name: &str, run: impl FnOnce(&Path) -> Result<()>) -> Result<()> {
    let artifacts = root().join(".tmp/rust-import-tests");
    fs::create_dir_all(&artifacts)?;
    let temporary = tempfile::Builder::new()
        .prefix(name)
        .tempdir_in(artifacts)?;
    let result = run(temporary.path());
    if result.is_err() {
        eprintln!(
            "Retained Rust E2E artifacts: {}",
            temporary.keep().display()
        );
    }
    result
}

pub fn command(command: &mut Command, log: &Path, timeout: Duration) -> Result<()> {
    let output = File::create(log)?;
    command
        .current_dir(root())
        .stdout(Stdio::from(output.try_clone()?))
        .stderr(Stdio::from(output));
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        command.process_group(0);
    }
    let mut child = command
        .spawn()
        .with_context(|| format!("spawn {command:?}"))?;
    let deadline = Instant::now() + timeout;
    loop {
        if let Some(status) = child.try_wait()? {
            ensure!(
                status.success(),
                "command failed ({status}); {}\n{}",
                log.display(),
                fs::read_to_string(log)?
            );
            return Ok(());
        }
        if Instant::now() >= deadline {
            #[cfg(unix)]
            {
                // The child started a new process group owned by this test.
                unsafe {
                    libc::kill(-(child.id() as i32), libc::SIGKILL);
                }
            }
            #[cfg(windows)]
            {
                let _ = Command::new("taskkill")
                    .args(["/PID", &child.id().to_string(), "/T", "/F"])
                    .status();
            }
            let _ = child.kill();
            child.wait()?;
            bail!(
                "command did not terminate within {timeout:?}; log: {}",
                log.display()
            );
        }
        std::thread::sleep(Duration::from_millis(20));
    }
}

pub fn ingest(source: &Path, archive: &Path, owners: &Path, log: &Path) -> Result<()> {
    command(
        Command::new("python")
            .args(["-m", "mailarchiver", "--archive"])
            .arg(archive)
            .args([
                "ingest",
                "--clamav",
                "--index-attachments",
                "--workers",
                "2",
                "--owner-names-file",
            ])
            .arg(owners)
            .arg(source)
            .env("PYTHONUNBUFFERED", "1"),
        log,
        Duration::from_secs(600),
    )
}

pub fn portable_verify(archive: &Path, log: &Path) -> Result<()> {
    command(
        Command::new("python")
            .arg("-I")
            .arg(archive.join("verify_mail_archive.py"))
            .arg(archive),
        log,
        Duration::from_secs(600),
    )
}

pub fn copy_tree(source: &Path, destination: &Path) -> Result<()> {
    fs::create_dir_all(destination)?;
    for entry in fs::read_dir(source)? {
        let entry = entry?;
        if entry.file_type()?.is_dir() {
            copy_tree(&entry.path(), &destination.join(entry.file_name()))?;
        } else {
            fs::copy(entry.path(), destination.join(entry.file_name()))?;
        }
    }
    Ok(())
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq, PartialOrd, Ord)]
#[serde(deny_unknown_fields)]
pub struct MessageExpectation {
    pub subject: String,
    pub sha256: String,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct FileExpectation {
    pub path: String,
    pub sha256: String,
    pub observations: u64,
    pub excluded: u64,
    pub messages: Vec<MessageExpectation>,
}

#[derive(Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct CorpusExpectation {
    pub version: u32,
    pub files: Vec<FileExpectation>,
}

pub fn fingerprint(source: &Path) -> Result<Vec<FileExpectation>> {
    fn walk(root: &Path, directory: &Path, found: &mut Vec<FileExpectation>) -> Result<()> {
        for entry in fs::read_dir(directory)? {
            let path = entry?.path();
            if path.is_dir() {
                walk(root, &path, found)?;
            } else if path.is_file() && path.file_name().is_some_and(|name| name != ".DS_Store") {
                found.push(FileExpectation {
                    path: path
                        .strip_prefix(root)?
                        .to_string_lossy()
                        .replace('\\', "/"),
                    sha256: file_sha256(&path)?,
                    observations: 0,
                    excluded: 0,
                    messages: vec![],
                });
            }
        }
        Ok(())
    }
    let mut result = vec![];
    walk(source, source, &mut result)?;
    result.sort_by(|a, b| a.path.cmp(&b.path));
    Ok(result)
}

#[derive(Deserialize)]
struct Volume {
    current_mount_path: PathBuf,
}

pub fn snapshot(
    archive: &Path,
    source: &Path,
    files: &[FileExpectation],
) -> Result<CorpusExpectation> {
    let report = verify_archive(archive)?;
    let connection = open_catalog(archive)?;
    let mut result = CorpusExpectation {
        version: 1,
        files: files.to_vec(),
    };
    let mut sources = connection.prepare("SELECT source_file_pk, source_path, source_volumes.metadata_json FROM source_files JOIN source_volumes USING(source_volume_pk)")?;
    let mut rows = sources.query([])?;
    while let Some(row) = rows.next()? {
        let key: i64 = row.get(0)?;
        let path: String = row.get(1)?;
        let metadata: String = row.get(2)?;
        let volume: Volume = serde_json::from_str(&metadata)?;
        let full = volume.current_mount_path.join(path).canonicalize()?;
        let relative = full
            .strip_prefix(source.canonicalize()?)?
            .to_string_lossy()
            .replace('\\', "/");
        let entry = result
            .files
            .iter_mut()
            .find(|entry| entry.path == relative)
            .with_context(|| format!("unrecognized source file {relative}"))?;
        (entry.observations, entry.excluded) = connection.query_row(
            "SELECT COUNT(*), COALESCE(SUM(disposition IN ('autosave-excluded','source-metadata-excluded')),0) FROM observations WHERE source_file_pk=?1", [key], |row| Ok((row.get(0)?, row.get(1)?)))?;
        entry.messages = connection.prepare("SELECT DISTINCT subject, messages.sha256 FROM observations JOIN messages USING(message_pk) WHERE source_file_pk=?1 ORDER BY subject, messages.sha256")?
            .query_map([key], |row| Ok(MessageExpectation { subject: row.get(0)?, sha256: row.get(1)? }))?
            .collect::<rusqlite::Result<_>>()?;
    }
    let identities: BTreeSet<_> = result
        .files
        .iter()
        .flat_map(|file| &file.messages)
        .collect();
    ensure!(
        identities.len() as u64 == report.messages,
        "source membership does not cover all canonical messages"
    );
    ensure!(
        count(
            &connection,
            "SELECT count(*) FROM observations WHERE disposition='error'"
        )? == 0,
        "import recorded errors"
    );
    Ok(result)
}

pub fn difference(expected: &CorpusExpectation, actual: &CorpusExpectation) -> String {
    let wanted: BTreeSet<_> = expected.files.iter().flat_map(|f| &f.messages).collect();
    let found: BTreeSet<_> = actual.files.iter().flat_map(|f| &f.messages).collect();
    let mut lines = Vec::new();
    for (label, messages) in [
        (
            "Found but not expected",
            found.difference(&wanted).collect::<Vec<_>>(),
        ),
        (
            "Expected but not found",
            wanted.difference(&found).collect::<Vec<_>>(),
        ),
    ] {
        lines.push(format!("{label} ({}):", messages.len()));
        for message in messages {
            lines.push(format!("  {}  {:?}", message.sha256, message.subject));
        }
    }
    let paths: BTreeSet<_> = expected
        .files
        .iter()
        .chain(&actual.files)
        .map(|f| &f.path)
        .collect();
    for path in paths {
        if expected.files.iter().find(|f| &f.path == path)
            != actual.files.iter().find(|f| &f.path == path)
        {
            lines.push(format!(
                "Source inventory, message membership, or accounting changed: {path}"
            ));
        }
    }
    lines.join("\n")
}

#[derive(Deserialize)]
pub struct Counts {
    pub infected: u64,
    pub autosaves: u64,
    pub unchanged_sources: u64,
}

#[derive(Deserialize)]
pub struct Status {
    pub run_pk: u64,
    pub state: String,
    pub percent: f64,
    pub processed_messages: u64,
    pub files_total: u64,
    pub counts: Counts,
}

pub fn statuses(archive: &Path) -> Result<Vec<Status>> {
    let mut result: Vec<Status> = vec![];
    for entry in fs::read_dir(archive.join("status"))? {
        let path = entry?.path();
        if path.extension().is_some_and(|ext| ext == "json") {
            result.push(serde_json::from_reader(File::open(path)?)?);
        }
    }
    result.sort_by_key(|status| status.run_pk);
    Ok(result)
}
