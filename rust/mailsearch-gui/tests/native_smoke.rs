// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
// Prove native Rust GUI startup, search and verified display on a CLI-built archive.
// Synthetic EML inputs go through the real ingest CLI and portable verifier.
// The feature-enabled application drives its real DOM and native IPC, then snapshots WebKit.
// Every subprocess has a deadline and retained logs; failures preserve the fixture.
// Archive and source hashes must remain unchanged throughout native reading.
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
        .stderr(output)
        .stdin(Stdio::null())
        .spawn()?;
    let deadline = Instant::now() + Duration::from_secs(120);
    loop {
        if let Some(status) = child.try_wait()? {
            ensure!(
                status.success(),
                "{command:?} failed: {status}; see {}",
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
        fs::write(source.join(format!("{filename}.eml")), format!(
            "From: Alice <alice@example.test>\r\nTo: Bob <bob@example.test>\r\nSubject: {subject}\r\nDate: Tue, 02 Jan 2024 10:00:00 +0000\r\nMessage-ID: <{filename}@example.test>\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n{body}\r\n"
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
    let screenshot = work.join("rust-gui.png");
    run(
        Command::new(env!("CARGO_BIN_EXE_mailsearch-webview"))
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
    fs::write(work.join("success.txt"), "Native WKWebView startup, one-result search, selected message body, PNG snapshot and source/archive fixity passed.\n")?;
    Ok(())
}
