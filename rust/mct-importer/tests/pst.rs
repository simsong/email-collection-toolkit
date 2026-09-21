// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

//! Requirements: doc/PST_IMPORTER.md — real PST extraction, source fixity,
//! deterministic MIME/attachment recovery, partial runs, and process failures.
use chrono::{DateTime, NaiveDateTime, Utc};
use mailparse::{MailHeaderMap, ParsedMail};
use sha2::{Digest, Sha256};
use std::{
    fs,
    io::{Read, Write},
    path::{Path, PathBuf},
    process::{Command, Stdio},
};

fn fixture(name: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures")
        .join(name)
}
fn run(path: &Path) -> std::process::Output {
    Command::new(env!("CARGO_BIN_EXE_pst-importer"))
        .arg(path)
        .output()
        .unwrap()
}

#[test]
fn make_rejects_invalid_pst_before_building() {
    // doc/requirements.md: PST run targets reject invalid paths without stdout
    // or starting a build, including when parallel make is requested.
    let directory = tempfile::tempdir().unwrap();
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    for target in ["pst-import", "pst-smoke"] {
        for (input, diagnostic) in [
            (None, "usage: make pst-import PST="),
            (Some(String::new()), "usage: make pst-import PST="),
            (
                Some(
                    directory
                        .path()
                        .join("missing file.pst")
                        .display()
                        .to_string(),
                ),
                "PST must be a readable file:",
            ),
            (
                Some(directory.path().display().to_string()),
                "PST must be a readable file:",
            ),
        ] {
            let mut command = Command::new("make");
            command
                .current_dir(&root)
                .env_remove("PST")
                .env_remove("MAKEFLAGS")
                .env_remove("MFLAGS")
                .args(["--no-print-directory", "-j2", target])
                .arg(format!(
                    "RUST_TARGET_DIR={}",
                    directory.path().join("target").display()
                ));
            if let Some(input) = input {
                command.arg(format!("PST={input}"));
            }
            let result = command.output().unwrap();
            assert!(!result.status.success());
            assert!(result.stdout.is_empty());
            let stderr = String::from_utf8_lossy(&result.stderr);
            assert!(stderr.contains(diagnostic), "{target}: {stderr}");
            assert!(!directory.path().join("target").exists());
        }
    }
}

fn records(stream: &[u8]) -> Vec<Vec<u8>> {
    let mut messages: Vec<Vec<u8>> = Vec::new();
    for line in stream.split_inclusive(|b| *b == b'\n') {
        if line.starts_with(b"From ") {
            messages.push(Vec::new());
            continue;
        }
        let depth = line.iter().take_while(|b| **b == b'>').count();
        let line = if depth > 0 && line[depth..].starts_with(b"From ") {
            &line[1..]
        } else {
            line
        };
        messages.last_mut().unwrap().extend_from_slice(line);
    }
    for message in &mut messages {
        assert_eq!(message.pop(), Some(b'\n'));
    }
    messages
}
fn envelope_dates(stream: &[u8]) -> Vec<DateTime<Utc>> {
    stream
        .split_inclusive(|b| *b == b'\n')
        .filter_map(|line| {
            std::str::from_utf8(line)
                .ok()?
                .strip_prefix("From pst-importer ")
                .map(str::trim_end)
        })
        .map(|date| {
            NaiveDateTime::parse_from_str(date, "%a %b %e %H:%M:%S %Y")
                .unwrap()
                .and_utc()
        })
        .collect()
}
fn leaves<'a>(mail: &'a ParsedMail<'a>, result: &mut Vec<&'a ParsedMail<'a>>) {
    if mail.subparts.is_empty() {
        result.push(mail);
    }
    for part in &mail.subparts {
        leaves(part, result);
    }
}

#[test]
fn actual_pst_preserves_bodies_attachments_and_reports_partial_extraction() {
    let directory = tempfile::tempdir().unwrap();
    let source = directory.path().join("mail ü # percent%.pst");
    fs::copy(fixture("mail.pst"), &source).unwrap();
    let before = fs::read(&source).unwrap();
    let result = run(&source);
    assert_eq!(result.status.code(), Some(1)); // All rejected messages must prevent success.
    let diagnostics = String::from_utf8(result.stderr).unwrap();
    assert!(diagnostics.contains(
        "source-sha256=b11f87c5658a18adfbab2fe3de6ae359c6f65fc8bd6b3e45ac93d2da41ec13c3"
    ));
    assert!(
        diagnostics.contains("encountered=16 emitted=12 non-mail=2 errors=2"),
        "{diagnostics}"
    );
    assert!(diagnostics.contains("item=2097316: Missing PidTagAttachMethod"));
    assert!(diagnostics.contains("LIBRARY ERROR (outlook-pst 1.2.0) item=2097316:"));
    assert!(!diagnostics.contains("ERROR item=2097476"));
    assert_eq!(before, fs::read(&source).unwrap());
    assert_eq!(result.stdout, run(&source).stdout);
    let mut errors = Vec::new();
    let validation = mct_importer::validate(
        &mut result.stdout.as_slice(),
        mct_importer::DEFAULT_MAX_MESSAGE_BYTES,
        |e| errors.push(e),
    )
    .unwrap();
    assert_eq!(validation.complete, 12);
    assert!(errors.is_empty(), "{errors:?}");
    let messages = records(&result.stdout);
    let envelope_dates = envelope_dates(&result.stdout);
    assert_eq!(envelope_dates.len(), messages.len());
    let mut binary_attachments = 0;
    for (bytes, envelope_date) in messages.iter().zip(envelope_dates) {
        let mail = mailparse::parse_mail(bytes).unwrap();
        let header_date = mail
            .headers
            .get_first_value("Date")
            .and_then(|date| DateTime::parse_from_rfc2822(&date).ok())
            .unwrap()
            .with_timezone(&Utc);
        assert_eq!(envelope_date, header_date);
        let uri = mail.headers.get_first_value("X-Imported-URI").unwrap();
        let uri = url::Url::parse(&uri).unwrap();
        assert_eq!(
            uri.to_file_path().unwrap().canonicalize().unwrap(),
            source.canonicalize().unwrap()
        );
        assert_eq!(
            mail.headers.get_first_value("X-Importer-Name").as_deref(),
            Some("pst-importer")
        );
        let mut parts = Vec::new();
        leaves(&mail, &mut parts);
        binary_attachments += parts
            .iter()
            .filter(|p| {
                p.get_content_disposition().params.contains_key("filename")
                    && !p.get_content_disposition().params["filename"].starts_with("original-")
            })
            .count();
        match uri.fragment().unwrap() {
            "item=2097220" => {
                assert_eq!(
                    mail.headers.get_first_value("Subject").as_deref(),
                    Some("message 1")
                );
                assert_eq!(
                    mail.headers.get_first_value("To").as_deref(),
                    Some("saqib.razzaq@xp.local")
                );
                assert_eq!(
                    parts[0].get_body_raw().unwrap(),
                    b"This is first message\r\n\r\n"
                );
                assert_eq!(parts[1].ctype.mimetype, "text/html");
                assert_eq!(
                    format!("{:x}", Sha256::digest(parts[1].get_body_raw().unwrap())),
                    "3117567c43ee5fc87b36b3f9e9bfe055b185c9956f1324fcab64798de388e573"
                );
            }
            "item=2097444" => {
                let files: Vec<_> = parts
                    .iter()
                    .filter(|p| p.get_content_disposition().params.contains_key("filename"))
                    .collect();
                assert_eq!(files.len(), 3);
                let text = files
                    .iter()
                    .find(|p| p.get_content_disposition().params["filename"] == "text file.txt")
                    .unwrap();
                assert_eq!(
                    text.get_body_raw().unwrap(),
                    b"hello\r\n\r\nthis is a text file."
                );
                let image = files
                    .iter()
                    .find(|p| p.get_content_disposition().params["filename"] == "Sunset.jpg")
                    .unwrap();
                assert_eq!(
                    format!("{:x}", Sha256::digest(image.get_body_raw().unwrap())),
                    "ea3b1238a38f72b330aac53364bd0a0481946b93fc757dde7314ce3319f1840e"
                );
            }
            "item=2097476" => {
                assert!(mail.headers.get_first_value("Message-ID").is_none());
                let evidence = parts
                    .iter()
                    .find(|p| {
                        p.get_content_disposition()
                            .params
                            .get("filename")
                            .is_some_and(|v| v == "original-message-id.txt")
                    })
                    .unwrap();
                assert_eq!(
                    evidence.get_body_raw().unwrap(),
                    b"<003c01cc306e$17006760$45013620$@razzaq@xp.local>"
                );
            }
            "item=2097732" => {
                let original = parts
                    .iter()
                    .find(|p| {
                        p.get_content_disposition()
                            .params
                            .get("filename")
                            .is_some_and(|v| v == "original-transport-headers.txt")
                    })
                    .unwrap();
                assert_eq!(
                    format!("{:x}", Sha256::digest(original.get_body_raw().unwrap())),
                    "7211cd37845b7e3f365c87bd0de80cba995a1b004e985bbd7aed86ffc57ec7f2"
                );
            }
            _ => {}
        }
    }
    assert_eq!(binary_attachments, 30);
}

#[test]
fn empty_read_only_pst_succeeds_and_bad_inputs_emit_no_mail() {
    let directory = tempfile::tempdir().unwrap();
    let source = directory.path().join("empty.pst");
    fs::copy(fixture("empty.pst"), &source).unwrap();
    let original_permissions = fs::metadata(&source).unwrap().permissions();
    let mut read_only = original_permissions.clone();
    read_only.set_readonly(true);
    fs::set_permissions(&source, read_only).unwrap();
    let result = run(&source);
    assert!(
        result.status.success(),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
    assert!(result.stdout.is_empty());
    assert!(fs::metadata(&source).unwrap().permissions().readonly());
    assert_eq!(
        fs::read(&source).unwrap(),
        fs::read(fixture("empty.pst")).unwrap()
    );
    fs::set_permissions(&source, original_permissions).unwrap();
    for bytes in [
        b"not a PST".as_slice(),
        &fs::read(fixture("empty.pst")).unwrap()[..100],
    ] {
        fs::write(&source, bytes).unwrap();
        let bad = run(&source);
        assert!(!bad.status.success());
        assert!(bad.stdout.is_empty());
        assert_eq!(fs::read(&source).unwrap(), bytes);
    }
    let missing = run(&directory.path().join("missing.pst"));
    assert!(!missing.status.success());
    assert!(missing.stdout.is_empty());
    for arg in ["--api-version", "--version"] {
        assert!(Command::new(env!("CARGO_BIN_EXE_pst-importer"))
            .arg(arg)
            .output()
            .unwrap()
            .status
            .success());
    }
    assert_eq!(
        Command::new(env!("CARGO_BIN_EXE_pst-importer"))
            .output()
            .unwrap()
            .status
            .code(),
        Some(2)
    );
}

#[test]
fn invalid_only_exports_failed_items_as_diagnostic_evidence() {
    // doc/PST_IMPORTER.md: only-invalid excludes valid mail and keeps real
    // failure status, source fixity, item provenance, and available body evidence.
    let source = fixture("mail.pst");
    let before = fs::read(&source).unwrap();
    let result = Command::new(env!("CARGO_BIN_EXE_pst-importer"))
        .args(["--only-invalid", "--"])
        .arg(&source)
        .output()
        .unwrap();
    assert_eq!(result.status.code(), Some(1));
    assert_eq!(before, fs::read(&source).unwrap());
    let diagnostics = String::from_utf8(result.stderr).unwrap();
    assert!(diagnostics.contains("encountered=16 emitted=0 non-mail=2 errors=2"));
    assert!(diagnostics.contains("invalid-exported=2;"));
    let messages = records(&result.stdout);
    assert_eq!(messages.len(), 2);
    for (bytes, item) in messages.iter().zip([2097316, 2097540]) {
        let mail = mailparse::parse_mail(bytes).unwrap();
        assert_eq!(
            mail.headers.get_first_value("X-PST-Diagnostic").as_deref(),
            Some("invalid-message")
        );
        assert!(mail
            .headers
            .get_first_value("X-Imported-URI")
            .unwrap()
            .ends_with(&format!("#item={item}")));
        let summary = mail.subparts[0].get_body().unwrap();
        let headers = mail
            .subparts
            .iter()
            .find(|part| {
                part.get_content_disposition()
                    .params
                    .get("filename")
                    .is_some_and(|name| name == "reconstructed-headers.txt")
            })
            .unwrap()
            .get_body_raw()
            .unwrap();
        assert!(headers.starts_with(b"X-Imported-URI:"));
        assert!(headers.ends_with(b"\r\n\r\n"));
        assert!(!String::from_utf8_lossy(&headers).contains("X-PST-Diagnostic:"));
        assert!(summary.contains("LIBRARY ERROR (outlook-pst 1.2.0): Missing PidTagAttachMethod"));
        assert!(mail
            .subparts
            .iter()
            .any(|part| !part.get_body_raw().unwrap().is_empty()
                && part
                    .get_content_disposition()
                    .params
                    .get("filename")
                    .is_some_and(|name| name == "body")));
    }
    let mut failures = Vec::new();
    let validation = mct_importer::validate(
        &mut result.stdout.as_slice(),
        mct_importer::DEFAULT_MAX_MESSAGE_BYTES,
        |e| failures.push(e),
    )
    .unwrap();
    assert_eq!(validation.complete, 2);
    assert!(failures.is_empty(), "{failures:?}");
    let empty = Command::new(env!("CARGO_BIN_EXE_pst-importer"))
        .arg("--only-invalid")
        .arg(fixture("empty.pst"))
        .output()
        .unwrap();
    assert!(empty.status.success());
    assert!(empty.stdout.is_empty());
}

#[test]
fn real_pipeline_requires_producer_status_and_broken_pipe_is_failure() {
    let mut producer = Command::new(env!("CARGO_BIN_EXE_pst-importer"))
        .arg(fixture("mail.pst"))
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .unwrap();
    let validation = Command::new(env!("CARGO_BIN_EXE_mdti-validator"))
        .stdin(producer.stdout.take().unwrap())
        .output()
        .unwrap();
    assert!(validation.status.success());
    assert!(String::from_utf8(validation.stdout)
        .unwrap()
        .contains("Complete messages received: 12"));
    assert_eq!(producer.wait().unwrap().code(), Some(1));
    let mut producer = Command::new(env!("CARGO_BIN_EXE_pst-importer"))
        .arg(fixture("mail.pst"))
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .unwrap();
    let mut pipe = producer.stdout.take().unwrap();
    pipe.read_exact(&mut [0; 1]).unwrap();
    drop(pipe);
    assert!(!producer.wait().unwrap().success());
}

#[test]
fn source_change_during_output_is_reported_even_after_complete_records() {
    // A real file change at the output boundary, confined to a temporary fixture copy.
    struct MutatingOutput {
        file: PathBuf,
        changed: bool,
    }
    impl Write for MutatingOutput {
        fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
            if !self.changed {
                fs::OpenOptions::new()
                    .append(true)
                    .open(&self.file)?
                    .write_all(b"changed")?;
                self.changed = true;
            }
            Ok(bytes.len())
        }
        fn flush(&mut self) -> std::io::Result<()> {
            Ok(())
        }
    }
    let dir = tempfile::tempdir().unwrap();
    let file = dir.path().join("changed.pst");
    fs::copy(fixture("mail.pst"), &file).unwrap();
    let mut diagnostics = Vec::new();
    let report = mct_importer::pst::import(
        &file,
        &mut MutatingOutput {
            file: file.clone(),
            changed: false,
        },
        &mut diagnostics,
    )
    .unwrap();
    assert!(report.emitted > 0);
    assert!(String::from_utf8(diagnostics)
        .unwrap()
        .contains("source changed during extraction"));
}
