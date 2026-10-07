// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
// Prove native Rust reading and persisted workflow edits on a CLI-built archive.
// Synthetic EML inputs go through the real ingest CLI and portable verifier.
// The feature-enabled application drives its real DOM and native IPC, then snapshots WebKit.
// Each subprocess has a deadline and distinct stdout/stderr logs; failures retain fixtures.
// Preferences use a private HOME; read-only trials preserve the full archive.
// Explicit editor trials persist decisions while canonical mail/source hashes stay fixed.
// A real processor rerun and another app launch must retain the manual decisions.
// Run explicitly with make test-rust-gui-native on a logged-in macOS runner.
#![cfg(all(target_os = "macos", feature = "native-smoke"))]

use anyhow::{ensure, Context, Result};
use sha2::{Digest, Sha256};
use std::{
    fs::{self, File},
    io::Read,
    path::{Path, PathBuf},
    process::{Command, Stdio},
    thread,
    time::{Duration, Instant},
};

fn run(command: &mut Command, log: &Path) -> Result<()> {
    let output = File::create(log)?;
    let mut child = command
        .stdout(output.try_clone()?)
        .stderr(File::create(log.with_extension("stderr.log"))?)
        .stdin(Stdio::null())
        .spawn()?;
    let deadline = Instant::now() + Duration::from_secs(120);
    loop {
        if let Some(status) = child.try_wait()? {
            ensure!(
                status.success(),
                "{command:?} failed: {status}; see {} and its stderr.log",
                log.display()
            );
            return Ok(());
        }
        if Instant::now() >= deadline {
            child.kill()?;
            child.wait()?;
            anyhow::bail!("{command:?} timed out; see {}", log.display());
        }
        thread::sleep(Duration::from_millis(100));
    }
}

fn inventory(root: &Path) -> Result<Vec<(PathBuf, String)>> {
    fn visit(root: &Path, directory: &Path, entries: &mut Vec<(PathBuf, String)>) -> Result<()> {
        for entry in fs::read_dir(directory)? {
            let path = entry?.path();
            if path.is_dir() {
                visit(root, &path, entries)?;
            } else {
                let mut file = File::open(&path)?;
                let mut digest = Sha256::new();
                let mut buffer = [0; 65536];
                loop {
                    let count = file.read(&mut buffer)?;
                    if count == 0 {
                        break;
                    }
                    digest.update(&buffer[..count]);
                }
                entries.push((
                    path.strip_prefix(root)?.to_owned(),
                    format!("{:x}", digest.finalize()),
                ));
            }
        }
        Ok(())
    }
    let mut entries = Vec::new();
    visit(root, root, &mut entries)?;
    entries.sort();
    Ok(entries)
}

#[test]
#[ignore = "opens native WKWebView; run make test-rust-gui-native"]
fn cli_archive_native_search_and_screenshot() -> Result<()> {
    let repository = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .canonicalize()?;
    let artifacts = std::env::var_os("RUST_GUI_ARTIFACT_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| repository.join(".tmp/rust-gui-native"));
    fs::create_dir_all(&artifacts)?;
    let work = tempfile::Builder::new()
        .prefix("run-")
        .tempdir_in(artifacts)?
        .keep();
    println!("Native GUI evidence: {}", work.display());
    let source = work.join("source");
    fs::create_dir(&source)?;
    for (filename, subject, body) in [
        (
            "observatory",
            "Observatory planning",
            "Meet at the observatory on Friday.",
        ),
        ("garden", "Garden update", "The roses are blooming."),
    ] {
        let content = if filename == "observatory" {
            format!("MIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=native-fixture\r\n\r\n--native-fixture\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n{body}\r\n--native-fixture\r\nContent-Type: text/plain\r\nContent-Disposition: attachment; filename=native-acceptance.txt\r\n\r\nNative attachment fixture.\r\n--native-fixture--\r\n")
        } else {
            format!("Content-Type: text/plain; charset=utf-8\r\n\r\n{body}\r\n")
        };
        fs::write(source.join(format!("{filename}.eml")), format!(
            "From: Alice <alice@example.test>\r\nTo: Bob <bob@example.test>\r\nSubject: {subject}\r\nDate: Tue, 02 Jan 2024 10:00:00 +0000\r\nMessage-ID: <{filename}@example.test>\r\n{content}"
        ))?;
    }
    let source_before = inventory(&source)?;
    let owner = work.join("owner-names.txt");
    fs::write(&owner, "bob@example.test\n")?;
    let archive = work.join("Sample.mailarchive");
    run(
        Command::new("uv")
            .current_dir(&repository)
            .args(["run", "--locked", "mailarchiver", "--archive"])
            .arg(&archive)
            .args([
                "ingest",
                "--no-scan",
                "--workers",
                "1",
                "--owner-names-file",
            ])
            .arg(owner)
            .arg(&source),
        &work.join("ingest.log"),
    )?;
    run(
        Command::new("uv")
            .current_dir(&repository)
            .args(["run", "--locked", "verify-mail-archive"])
            .arg(&archive),
        &work.join("verify.log"),
    )?;
    let before = inventory(&archive)?;
    let startup_directory = work.join("startup-directory");
    fs::create_dir(&startup_directory)?;
    for (name, python, expected) in [
        ("available", None, "true"),
        ("missing", Some(work.join("missing-python")), "false"),
        ("failed", Some(PathBuf::from("/usr/bin/false")), "false"),
    ] {
        let mut command = Command::new(env!("CARGO_BIN_EXE_mailsearch-webview"));
        command
            .arg("--startup-smoke")
            .current_dir(&startup_directory);
        if let Some(python) = python {
            command.env("ECT_RUST_ENGINE_PYTHON", python);
        }
        let log = work.join(format!("startup-{name}.log"));
        run(&mut command, &log)?;
        ensure!(
            fs::read_to_string(log)?.trim() == expected,
            "Startup offered archive creation with the wrong helper capability"
        );
    }
    ensure!(
        fs::read_dir(startup_directory)?.next().is_none(),
        "Startup capability probe created archive files"
    );
    let screenshot = work.join("rust-gui.png");
    let home = work.join("home");
    fs::create_dir(&home)?;
    run(
        Command::new(env!("CARGO_BIN_EXE_mailsearch-webview"))
            .env("HOME", &home)
            .arg("--native-smoke")
            .arg(&archive)
            .arg(&screenshot),
        &work.join("native.log"),
    )?;
    let png = fs::read(screenshot).context("Native process did not produce a screenshot")?;
    ensure!(
        png.len() > 10000 && png.starts_with(b"\x89PNG\r\n\x1a\n"),
        "Missing or invalid PNG snapshot"
    );
    run(
        Command::new(env!("CARGO_BIN_EXE_mailsearch-webview"))
            .env("ECT_RUST_ENGINE_PYTHON", work.join("missing-python"))
            .env("HOME", &home)
            .arg("--native-smoke")
            .arg(&archive)
            .arg(work.join("rust-gui-no-python.png")),
        &work.join("native-no-python.log"),
    )?;
    let preferences = mailsearch_rust::preferences::Preferences::load(
        &home.join("Library/Application Support/Email Collection Toolkit/rust-reader.json"),
    )?;
    ensure!(
        preferences.message_font_size == 16 && !preferences.automatic_updates,
        "Native preference Save/Reopen did not persist the two real UI edits"
    );
    ensure!(
        inventory(&archive)? == before,
        "Native GUI changed archive files"
    );
    ensure!(
        inventory(&source)? == source_before,
        "CLI/native workflow changed source files"
    );
    fs::write(
        work.join("archive-sha256.json"),
        serde_json::to_vec_pretty(&before)?,
    )?;
    for phase in ["mutate", "verify"] {
        if phase == "verify" {
            run(
                Command::new("uv")
                    .current_dir(&repository)
                    .args(["run", "--locked", "mailarchiver", "--archive"])
                    .arg(&archive)
                    .args(["process", "--reprocess"]),
                &work.join("reprocess.log"),
            )?;
        }
        run(
            Command::new(env!("CARGO_BIN_EXE_mailsearch-webview"))
                .env("HOME", &home)
                .arg("--native-editor-smoke")
                .arg(&archive)
                .arg(phase)
                .arg(work.join(format!("editors-{phase}.png"))),
            &work.join(format!("editors-{phase}.log")),
        )?;
        let after = inventory(&archive)?;
        let stable = |items: &[(PathBuf, String)]| {
            items
                .iter()
                .filter(|(name, _)| {
                    !matches!(
                        name.to_str(),
                        Some("config.yaml" | "processing.sqlite3" | "status/archive-write.lock")
                    )
                })
                .cloned()
                .collect::<Vec<_>>()
        };
        if phase == "mutate" {
            ensure!(
                stable(&after) == stable(&before),
                "Editor changed unexpected archive files"
            );
        }
        let canonical = |items: &[(PathBuf, String)]| {
            items
                .iter()
                .filter(|(name, _)| {
                    name.starts_with("data/mbox")
                        || name.starts_with("integrity")
                        || name.starts_with("objects")
                        || name == Path::new("manifest-sha256.txt")
                        || name == Path::new("mailbag.csv")
                })
                .cloned()
                .collect::<Vec<_>>()
        };
        ensure!(
            canonical(&after) == canonical(&before),
            "Editor/processing changed canonical files"
        );
        ensure!(
            inventory(&source)? == source_before,
            "Editor/processing changed source files"
        );
        let database = rusqlite::Connection::open_with_flags(
            archive.join("processing.sqlite3"),
            rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY,
        )?;
        let identities: Vec<(String, String, i64, i64)> = database.prepare(
            "SELECT a.address,p.canonical_name,pa.manual,p.manual FROM addresses a \
             JOIN person_addresses pa USING(address_id) JOIN persons p USING(person_id) ORDER BY a.address"
        )?.query_map([], |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?)))?
          .collect::<rusqlite::Result<_>>()?;
        ensure!(
            identities
                == vec![
                    (
                        "alice@example.test".into(),
                        "Native Harness Alice".into(),
                        0,
                        1
                    ),
                    ("bob@example.test".into(), "bob@example.test".into(), 1, 1)
                ],
            "Manual identity decisions did not persist: {identities:?}"
        );
        let decisions: Vec<String> = database
            .prepare("SELECT operation FROM manual_decisions ORDER BY decision_id")?
            .query_map([], |row| row.get(0))?
            .collect::<rusqlite::Result<_>>()?;
        ensure!(
            decisions == ["rename-person", "move-address", "separate-address"],
            "Manual decision history changed: {decisions:?}"
        );
    }
    run(
        Command::new("uv")
            .current_dir(&repository)
            .args(["run", "--locked", "verify-mail-archive"])
            .arg(&archive),
        &work.join("verify-editors.log"),
    )?;
    fs::write(work.join("success.txt"), "Native WKWebView startup, search/read, About health, Preferences Save/Reopen with/without helper, isolated owner/identity/history loading, owner Save/Reopen, identity Rename/Move/Separate/Reopen, real processor rerun and independent app relaunch persistence, PNG snapshots and canonical mail/source fixity passed.\n")?;
    Ok(())
}
