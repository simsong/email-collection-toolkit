// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
//! Exercise the actual import-to-database path with Rust-owned assertions.
//! Requirements: complete source inventory, byte preservation, quarantine,
//! search membership, final status, idempotence and bounded execution.
//! Python runs only as the application and installed portable verifier.
//! Golden expectations are updated only by a separate explicit Make target.
//! Browser and native-window tests remain outside this first migration.

mod support;

use anyhow::{ensure, Context, Result};
use archive_verifier::{count, file_sha256, open_catalog, verify_archive};
use base64::{engine::general_purpose::STANDARD, Engine};
use rusqlite::Connection;
use sha2::{Digest, Sha256};
use std::collections::BTreeSet;
use std::fs::{self, File};
use std::path::Path;
use std::process::Command;
use std::time::Duration;
use support::*;

fn corpus(update: bool) -> Result<()> {
    scenario("corpus-", |work| {
        let source = root().join("tests/data");
        let original = fingerprint(&source)?;
        let archive = work.join("corpus.mailarchive");
        let owners = work.join("owners.txt");
        fs::write(&owners, "alice@example.org\n")?;
        ingest(&source, &archive, &owners, &work.join("import.log"))?;
        let actual = snapshot(&archive, &source, &original)?;
        let history = statuses(&archive)?;
        ensure!(
            history.len() == 1 && history[0].state == "completed" && history[0].percent == 100.0,
            "incomplete import status"
        );
        ensure!(
            history[0].processed_messages
                == actual.files.iter().map(|f| f.observations).sum::<u64>(),
            "status/observation accounting mismatch"
        );
        portable_verify(&archive, &work.join("verify.log"))?;
        ingest(&source, &archive, &owners, &work.join("reimport.log"))?;
        ensure!(
            snapshot(&archive, &source, &original)? == actual,
            "reimport changed message inventory or source observations"
        );
        let repeated = statuses(&archive)?;
        ensure!(
            repeated.len() == 2
                && repeated[1].state == "completed"
                && repeated[1].processed_messages == 0,
            "reimport did not skip completed sources"
        );
        ensure!(
            repeated[1].counts.unchanged_sources == history[0].files_total,
            "reimport source count mismatch"
        );
        ensure!(
            fingerprint(&source)? == original,
            "import changed source bytes"
        );

        let public = root().join("tests/expected-corpus.json");
        let private = root().join(".tmp/expected-corpus-private.json");
        if update {
            let tracked = Command::new("git")
                .args(["ls-files", "-z", "--", "tests/data"])
                .current_dir(root())
                .output()?;
            ensure!(tracked.status.success(), "git source inventory failed");
            let text = String::from_utf8(tracked.stdout)?;
            let tracked: BTreeSet<_> = text.split('\0').collect();
            for (target, is_private) in [(public, false), (private, true)] {
                let subset = CorpusExpectation {
                    version: 1,
                    files: actual
                        .files
                        .iter()
                        .filter(|file| {
                            !tracked.contains(format!("tests/data/{}", file.path).as_str())
                                == is_private
                        })
                        .cloned()
                        .collect(),
                };
                fs::create_dir_all(target.parent().context("expectation parent")?)?;
                fs::write(&target, serde_json::to_string_pretty(&subset)? + "\n")?;
                println!(
                    "Updated {}: {} files; review before accepting.",
                    target.display(),
                    subset.files.len()
                );
            }
        } else {
            let mut expected: CorpusExpectation = serde_json::from_reader(File::open(public)?)?;
            ensure!(
                expected.version == 1,
                "unsupported corpus expectation version"
            );
            if private.is_file() {
                let overlay: CorpusExpectation = serde_json::from_reader(File::open(private)?)?;
                ensure!(
                    overlay.version == 1,
                    "unsupported private expectation version"
                );
                expected.files.extend(overlay.files);
            }
            expected.files.sort_by(|a, b| a.path.cmp(&b.path));
            ensure!(actual == expected, "{}", difference(&expected, &actual));
        }
        Ok(())
    })
}

#[test]
#[ignore = "real application/ClamAV acceptance: make test-corpus-import"]
fn complete_corpus_import() -> Result<()> {
    corpus(false)
}

#[test]
#[ignore = "explicit golden maintenance only: make update-corpus-expectations"]
fn update_corpus_expectations() -> Result<()> {
    corpus(true)
}

#[test]
#[ignore = "real application/ClamAV acceptance: make test-import-e2e"]
fn synthetic_lifecycle() -> Result<()> {
    scenario("lifecycle-", |work| {
        let source = work.join("source");
        copy_tree(&root().join("e2e_tests/data/source"), &source)?;
        let original = fingerprint(&source)?;
        let owners = work.join("owners.txt");
        fs::write(&owners, "archive-owner@example.org\n")?;
        let infected = source.join("Professional/Quarantine/runtime-infected.emlx");
        // Never commit a complete EICAR signature; materialize only in disposable input.
        let trigger = [
            "X5O!P%@",
            "AP[4\\PZX54(P^)",
            "7CC)7}$EI",
            "CAR-STANDARD-",
            "ANTIVIRUS-TEST-",
            "FILE!$H+H*",
        ]
        .concat();
        let template = fs::read_to_string(root().join("e2e_tests/data/infected.emlx.template"))?;
        let raw = template.replace("{{EICAR_BASE64}}", &STANDARD.encode(trigger));
        fs::create_dir_all(infected.parent().context("infected parent")?)?;
        fs::write(&infected, format!("{}\n{raw}", raw.len()))?;
        let archive = work.join("archive");
        let ingested = ingest(&source, &archive, &owners, &work.join("import.log"));
        fs::remove_file(&infected)?;
        ingested?;
        ensure!(
            fingerprint(&source)? == original,
            "synthetic sources changed"
        );
        let log = fs::read_to_string(work.join("import.log"))?;
        for expected in [
            "completed:",
            "processed=210",
            "infected=1",
            "autosaved=1",
            "seen_skipped=1",
            "waiting for ClamAV startup:",
        ] {
            ensure!(
                log.contains(expected),
                "missing progress evidence: {expected}"
            );
        }
        let before = fingerprint(&archive)?;
        let report = verify_archive(&archive)?;
        ensure!(
            report.messages == 208 && report.observations == 210 && report.indexed_messages == 207,
            "unexpected lifecycle report: {report:?}"
        );
        command(
            Command::new(env!("CARGO_BIN_EXE_archive-verifier")).arg(&archive),
            &work.join("rust-verify.log"),
            Duration::from_secs(60),
        )?;
        ensure!(
            fingerprint(&archive)? == before,
            "Rust verification changed archive files"
        );
        let history = statuses(&archive)?;
        ensure!(
            history.len() == 1
                && history[0].state == "completed"
                && history[0].processed_messages == 210
                && history[0].counts.infected == 1
                && history[0].counts.autosaves == 1,
            "wrong final lifecycle status"
        );
        for name in [
            "manifest-sha256.txt",
            "tagmanifest-sha256.txt",
            "mailbag.csv",
        ] {
            ensure!(archive.join(name).is_file(), "missing {name}");
        }
        ensure!(
            !fs::read_to_string(archive.join("tagmanifest-sha256.txt"))?.contains("status/"),
            "operational status was manifested"
        );
        ensure!(fs::read_to_string(archive.join("bag-info.txt"))?.contains("Mailarchiver-Message-Newline-Policy: preserve-source; add-final-LF-for-MBOX-framing\n"), "missing newline policy");
        let catalog = open_catalog(&archive)?;
        for (sql, expected) in [
            ("SELECT count(*) FROM source_files", 7),
            ("SELECT count(*) FROM messages WHERE category='INFECTED'", 1),
            (
                "SELECT count(*) FROM observations WHERE disposition='duplicate'",
                1,
            ),
            (
                "SELECT count(*) FROM observations WHERE disposition='autosave-excluded'",
                1,
            ),
        ] {
            ensure!(
                count(&catalog, sql)? == expected,
                "unexpected accounting: {sql}"
            );
        }
        let date: (String,String) = catalog.query_row("SELECT date_utc, date_source FROM messages WHERE message_id_normalized='rich-e2e@example'", [], |row| Ok((row.get(0)?,row.get(1)?)))?;
        ensure!(
            date == ("2024-02-02T00:00:00+00:00".into(), "received-median".into()),
            "date resolution regression"
        );
        for (id, digest) in [
            (
                "infected-e2e@example",
                format!("{:x}", Sha256::digest(raw.as_bytes())),
            ),
            (
                "no-final-newline-e2e@example",
                file_sha256(&source.join("Professional/Projects/no-final-newline.eml"))?,
            ),
        ] {
            let stored: String = catalog.query_row(
                "SELECT sha256 FROM messages WHERE message_id_normalized=?1",
                [id],
                |row| row.get(0),
            )?;
            ensure!(stored == digest, "source bytes changed for {id}");
        }
        drop(catalog);
        let search = Connection::open_with_flags(
            archive.join("search.sqlite3"),
            rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY,
        )?;
        ensure!(
            count(&search, "SELECT count(*) FROM attachment_fts")? == 1,
            "attachment indexing count"
        );
        ensure!(
            count(
                &search,
                "SELECT count(*) FROM attachment_fts WHERE attachment_fts MATCH 'Appendixquartz'"
            )? == 1,
            "attachment content not searchable"
        );
        drop(search);
        portable_verify(&archive, &work.join("portable-verify.log"))?;
        let output = fs::read_to_string(work.join("portable-verify.log"))?;
        for expected in [
            "OK 2024-Archive1.mbox: 207 messages",
            "OK INFECTED1.mbox: 1 messages",
            "Archive integrity verified.",
        ] {
            ensure!(
                output.contains(expected),
                "portable verification missing {expected}"
            );
        }
        corruptions(&archive, work)?;
        Ok(())
    })
}

fn corruptions(archive: &Path, work: &Path) -> Result<()> {
    // Same row counts are insufficient: reject altered offsets/digests and FTS mappings.
    for (name, database, sql, expected) in [
        ("missing-location", "archive.sqlite3", "DELETE FROM locations WHERE message_pk=(SELECT min(message_pk) FROM messages)", "missing location"),
        ("bad-offset", "archive.sqlite3", "UPDATE locations SET byte_offset=byte_offset+1 WHERE message_pk=(SELECT min(message_pk) FROM messages)", "record gap"),
        ("bad-hash", "archive.sqlite3", "UPDATE messages SET sha256=printf('%064d',0) WHERE message_pk=(SELECT min(message_pk) FROM messages)", "publication observation"),
        ("raw-record-hash", "archive.sqlite3", "UPDATE observations SET raw_sha256=printf('%064d',0) WHERE message_pk=(SELECT min(message_pk) FROM messages); UPDATE messages SET sha256=printf('%064d',0) WHERE message_pk=(SELECT min(message_pk) FROM messages)", "record raw SHA-256 mismatch"),
        ("bad-observation", "archive.sqlite3", "UPDATE observations SET raw_sha256=printf('%064d',0) WHERE disposition='duplicate'", "observation digest"),
        ("bad-generation", "archive.sqlite3", "UPDATE mbox_generations SET message_count=message_count+1", "coverage/count"),
        ("path-escape", "archive.sqlite3", "UPDATE mbox_generations SET filename='../escape.mbox' WHERE generation_pk=(SELECT min(generation_pk) FROM mbox_generations)", "unsafe mailbox"),
        ("unfinished-run", "archive.sqlite3", "UPDATE ingest_runs SET completed_at=NULL", "unfinished ingest"),
        ("unsupported-schema", "archive.sqlite3", "UPDATE schema_info SET version=999", "unsupported main schema"),
        ("missing-index", "search.sqlite3", "DELETE FROM message_fts WHERE rowid=(SELECT min(rowid) FROM message_fts)", "FTS mapping"),
        ("extra-index", "search.sqlite3", "INSERT INTO message_fts(sha256,content) VALUES ('unregistered','extra')", "unmapped message"),
        ("attachment-map", "search.sqlite3", "UPDATE attachment_fts SET sha256='wrong'", "attachment FTS mapping"),
        ("attachment-count", "search.sqlite3", "UPDATE message_metadata SET attachment_count=attachment_count+1", "attachment metadata count"),
    ] {
        let copy = work.join(name);
        copy_tree(archive, &copy)?;
        Connection::open(copy.join(database))?.execute_batch(sql)?;
        let error = verify_archive(&copy).expect_err("corrupt database accepted");
        ensure!(format!("{error:#}").contains(expected), "{name}: wrong failure: {error:#}");
    }
    let copy = work.join("corrupt-mbox");
    copy_tree(archive, &copy)?;
    let path = copy.join("data/mbox/2024-Archive1.mbox");
    let mut bytes = fs::read(&path)?;
    bytes[0] = b'?';
    fs::write(path, bytes)?;
    let error = command(
        Command::new(env!("CARGO_BIN_EXE_archive-verifier")).arg(copy),
        &work.join("corrupt-cli.log"),
        Duration::from_secs(60),
    )
    .expect_err("corrupt MBOX accepted by CLI");
    ensure!(
        format!("{error:#}").contains("mailbox SHA-256 mismatch"),
        "wrong CLI corruption failure: {error:#}"
    );
    Ok(())
}

#[test]
fn difference_names_missing_and_unexpected_mail() {
    // Requirement: actionable golden failures identify both sides, not just counts.
    let file = |subject: &str, digest: char| CorpusExpectation {
        version: 1,
        files: vec![FileExpectation {
            path: "mail.eml".into(),
            sha256: "c".repeat(64),
            observations: 1,
            excluded: 0,
            messages: vec![MessageExpectation {
                subject: subject.into(),
                sha256: digest.to_string().repeat(64),
            }],
        }],
    };
    let report = difference(
        &file("Expected subject", 'a'),
        &file("Unexpected subject", 'b'),
    );
    assert!(report.contains(&format!(
        "Found but not expected (1):\n  {}  \"Unexpected subject\"",
        "b".repeat(64)
    )));
    assert!(report.contains(&format!(
        "Expected but not found (1):\n  {}  \"Expected subject\"",
        "a".repeat(64)
    )));
}

#[test]
#[cfg(unix)]
fn deadline_terminates_stuck_subprocess() -> Result<()> {
    // Requirement: a genuine hanging child fails promptly instead of hanging the gate.
    let work = tempfile::tempdir()?;
    let start = std::time::Instant::now();
    let error = command(
        Command::new("sleep").arg("60"),
        &work.path().join("stuck.log"),
        Duration::from_millis(100),
    )
    .expect_err("sleep must time out");
    ensure!(
        error.to_string().contains("did not terminate") && start.elapsed() < Duration::from_secs(5),
        "deadline failed: {error}"
    );
    Ok(())
}
