// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
// Validate the real Windows WebView2 GUI using a purpose-made reader fixture.
// Exercise staged searches, paging, cancellation, dialogs and message display.
// Retain native PNG, diagnostics and SHA-256 inventory outside the archive.
// Settings are isolated under the test directory, never the user's profile.
// No Python writer guard is bypassed and no real mail is imported.
// The explicit feature and ignored test keep ordinary checks noninteractive.
#![cfg(all(target_os = "windows", feature = "native-smoke"))]
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
fn run(command: &mut Command, log: &Path, expected: i32) -> Result<()> {
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
                status.code() == Some(expected),
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
#[ignore = "opens native WebView2; run cargo reader-native-check"]
fn webview2_search_dialogs_and_fixity() -> Result<()> {
    let artifacts = std::env::var_os("RUST_GUI_ARTIFACT_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            Path::new(env!("CARGO_MANIFEST_DIR")).join("../../.tmp/rust-gui-native-windows")
        });
    fs::create_dir_all(&artifacts)?;
    let work = tempfile::Builder::new()
        .prefix("run-")
        .tempdir_in(artifacts)?
        .keep();
    let archive = work.join("Windows reader café");
    mailsearch_rust::demo::create(&archive)?;
    let db = rusqlite::Connection::open(archive.join("archive.sqlite3"))?;
    db.execute_batch("UPDATE messages SET category='INFECTED' WHERE message_pk=2;
        UPDATE messages SET category='MALFORMED' WHERE message_pk=3;
        WITH RECURSIVE seq(x) AS (VALUES(4) UNION ALL SELECT x+1 FROM seq WHERE x<1602)
        INSERT INTO messages SELECT x,printf('copy%d@example.test',x),sha256,sender_address_pk,subject,date_utc,date_source,category FROM seq CROSS JOIN messages WHERE message_pk=1;
        INSERT INTO locations SELECT message_pk,1,(SELECT byte_offset FROM locations WHERE message_pk=1),(SELECT byte_length FROM locations WHERE message_pk=1) FROM messages WHERE message_pk>3;")?;
    drop(db);
    let before = inventory(&archive)?;
    let screenshot = work.join("webview2.png");
    run(
        Command::new(env!("CARGO_BIN_EXE_mailsearch-webview"))
            .args(["--native-smoke"])
            .arg(&archive)
            .arg(&screenshot)
            .env("LOCALAPPDATA", work.join("profile"))
            .env("ECT_RUST_WEBVIEW_DIAGNOSTICS", "1"),
        &work.join("native.log"),
        0,
    )?;
    run(
        Command::new(env!("CARGO_BIN_EXE_mailsearch-webview"))
            .arg("--native-smoke")
            .arg(&archive)
            .arg(work.join("must-not-capture.png"))
            .env("LOCALAPPDATA", work.join("profile"))
            .env("ECT_RUST_NATIVE_CLOSE_SMOKE", "1"),
        &work.join("close.log"),
        1,
    )?;
    ensure!(
        !work.join("must-not-capture.png").exists(),
        "Close test completed instead of cancelling"
    );
    ensure!(
        fs::read_to_string(work.join("close.log"))?
            .contains("verified quit during unfinished search"),
        "Close exited without reaching the required unfinished-search state"
    );
    let png = fs::read(&screenshot).context("Missing native screenshot")?;
    ensure!(
        png.len() > 10000 && png.starts_with(b"\x89PNG\r\n\x1a\n"),
        "Invalid PNG"
    );
    ensure!(
        inventory(&archive)? == before,
        "Native GUI changed archive bytes or inventory"
    );
    fs::write(
        work.join("archive-sha256.json"),
        serde_json::to_vec_pretty(&before)?,
    )?;
    fs::write(work.join("success.txt"), "Native WebView2 search, both stages, six sorts, paging, cancellation, message display, dialogs and fixity passed.\n")?;
    println!("Windows evidence: {}", work.display());
    Ok(())
}
