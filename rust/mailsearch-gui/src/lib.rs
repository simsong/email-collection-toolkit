// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
// Read existing ECT archives without importing or modifying their contents.
// SQLite supplies bounded search results and canonical MBOX locations.
// A selected record is decoded only after its original SHA-256 is verified.
// MIME and HTML conversion produce display text, never replacement source bytes.
// Each reader owns its connections; background workers keep database work off the UI.
// The native UI and headless smoke tests use this same request path.
pub mod bridge;
mod browse;
pub mod demo;
mod desktop;
pub mod documents;
pub mod engine;
mod mime;
pub mod preferences;
mod search;
mod selectors;
pub mod shell;
pub mod updater;
pub mod worker;

use anyhow::{bail, ensure, Context, Result};
use mailparse::{DispositionType, MailHeaderMap, ParsedMail};
use rusqlite::{Connection, OpenFlags};
use sha2::{Digest, Sha256};
use std::{
    fs::File,
    io::{Read, Seek, SeekFrom},
    path::{Component, Path, PathBuf},
    time::{Duration, Instant},
};

pub const PAGE_SIZE: usize = 100;
const MAX_RECORD: u64 = 16 * 1024 * 1024;
const MAX_DISPLAY: usize = 256 * 1024;

#[derive(Debug, Clone)]
pub struct Row {
    pub id: i64,
    pub subject: String,
    pub sender: String,
    pub date: String,
}

#[derive(Debug)]
pub struct Message {
    pub headers: String,
    pub body: String,
    pub sha256: String,
}

pub struct Archive {
    root: PathBuf,
    db: Connection,
}

impl Archive {
    pub fn open(path: &Path) -> Result<Self> {
        let root = path
            .canonicalize()
            .context("Archive directory does not exist")?;
        let catalog = within(&root, Path::new("archive.sqlite3"))?;
        let search = within(&root, Path::new("search.sqlite3"))?;
        for path in [&catalog, &search] {
            // Even a SQLite read-only connection may create WAL shared-memory files.
            // Current ECT uses rollback journals; refuse WAL before opening SQLite.
            let mut header = [0; 20];
            File::open(path)?.read_exact(&mut header)?;
            ensure!(
                &header[..16] == b"SQLite format 3\0",
                "Invalid SQLite header"
            );
            ensure!(
                header[18..] == [1, 1],
                "WAL-mode archives are not supported by this read-only experiment"
            );
        }
        let db = Connection::open_with_flags(catalog, OpenFlags::SQLITE_OPEN_READ_ONLY)?;
        db.busy_timeout(Duration::from_millis(250))?;
        let uri = url::Url::from_file_path(search)
            .map_err(|_| anyhow::anyhow!("Invalid archive path"))?;
        db.execute("ATTACH DATABASE ?1 AS search", [format!("{uri}?mode=ro")])?;
        // Header display names are derived evidence; this temp view never writes the archive.
        let has_names: bool = db.query_row(
            "SELECT EXISTS(SELECT 1 FROM search.sqlite_master WHERE name='address_suggestions')",
            [],
            |r| r.get(0),
        )?;
        let mut names = if has_names {
            "SELECT address, display_name AS name FROM search.address_suggestions".to_string()
        } else {
            "SELECT '' AS address, '' AS name WHERE 0".to_string()
        };
        if root.join("processing.sqlite3").exists() {
            let identities = within(&root, Path::new("processing.sqlite3"))?;
            let mut header = [0; 20];
            File::open(&identities)?.read_exact(&mut header)?;
            ensure!(
                &header[..16] == b"SQLite format 3\0" && header[18..] == [1, 1],
                "Unsupported processing database journal mode"
            );
            let uri = url::Url::from_file_path(identities)
                .map_err(|_| anyhow::anyhow!("Invalid processing database path"))?;
            db.execute(
                "ATTACH DATABASE ? AS identities",
                [format!("{uri}?mode=ro")],
            )?;
            names.push_str(" UNION SELECT a.address,p.canonical_name FROM identities.addresses a JOIN identities.person_addresses pa USING(address_id) JOIN identities.persons p USING(person_id) UNION SELECT a.address,n.name FROM identities.addresses a JOIN identities.person_addresses pa USING(address_id) JOIN identities.person_aliases n USING(person_id) UNION SELECT a.address,json_extract(e.value,'$.name') FROM identities.addresses a JOIN identities.evidence e USING(address_id) WHERE e.kind='header' UNION SELECT a.address,o.name FROM identities.addresses a JOIN identities.organization_domains d ON a.domain=d.domain OR a.domain LIKE '%.'||d.domain JOIN identities.organizations o USING(organization_id)");
            db.execute_batch("CREATE TEMP VIEW attached_origins AS SELECT DISTINCT child.catalog_message_pk AS child, parent.catalog_message_pk AS parent_message_pk,o.parent_message_id,o.part_path FROM identities.occurrences o JOIN identities.message_state child ON child.message_id=o.message_id LEFT JOIN identities.message_state parent ON parent.message_id=o.parent_message_id WHERE o.parent_message_id IS NOT NULL")?;
        }
        if !root.join("processing.sqlite3").exists() {
            db.execute_batch("CREATE TEMP VIEW attached_origins AS SELECT 0 AS child,0 AS parent_message_pk,'' AS parent_message_id,'[]' AS part_path WHERE 0")?;
        }
        db.execute_batch(&format!("CREATE TEMP VIEW address_search_names AS {names}"))?;
        db.execute_batch("PRAGMA query_only=ON")?;
        for schema in ["main", "search"] {
            let versions: Vec<i64> = db
                .prepare(&format!("SELECT version FROM {schema}.schema_info"))?
                .query_map([], |r| r.get(0))?
                .collect::<rusqlite::Result<_>>()?;
            ensure!(versions == [1], "Unsupported {schema} schema version");
        }
        // Prepare the real statements at Open so missing tables fail before browsing.
        db.prepare("SELECT m.subject,a.address,g.filename,l.byte_offset,l.byte_length FROM messages m JOIN email_addresses a ON m.sender_address_pk=a.address_pk JOIN locations l USING(message_pk) JOIN mbox_generations g USING(generation_pk) LIMIT 0")?;
        db.prepare("SELECT sha256 FROM search.message_fts WHERE message_fts MATCH ?1")?;
        Ok(Self { root, db })
    }

    pub fn search(&self, text: &str) -> Result<Vec<Row>> {
        ensure!(text.len() <= 4096, "Search is limited to 4096 bytes");
        let deadline = Instant::now() + Duration::from_secs(3);
        self.db
            .progress_handler(1000, Some(move || Instant::now() >= deadline));
        let query = text
            .split_whitespace()
            .map(|s| format!("\"{}\"", s.replace('"', "\"\"")))
            .collect::<Vec<_>>()
            .join(" AND ");
        let filter = if query.is_empty() {
            ""
        } else {
            "AND m.sha256 IN (SELECT sha256 FROM search.message_fts WHERE message_fts MATCH ?1)"
        };
        let sql = format!("SELECT m.message_pk,m.subject,a.address,m.date_utc FROM messages m JOIN email_addresses a ON a.address_pk=m.sender_address_pk WHERE m.category IN ('Archive','Sent') {filter} ORDER BY m.date_utc DESC,m.message_pk DESC LIMIT {PAGE_SIZE}");
        let mut statement = self.db.prepare(&sql)?;
        let values = if query.is_empty() {
            vec![]
        } else {
            vec![query]
        };
        let result = statement
            .query_map(rusqlite::params_from_iter(values), |r| {
                Ok(Row {
                    id: r.get(0)?,
                    subject: r.get(1)?,
                    sender: r.get(2)?,
                    date: r.get(3)?,
                })
            })?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        Ok(result)
    }

    pub(crate) fn raw_message(&self, id: i64) -> Result<Vec<u8>> {
        self.db.progress_handler(0, None::<fn() -> bool>);
        let (digest, filename, offset, length): (String, String, u64, u64) = self.db.query_row(
            "SELECT m.sha256,g.filename,l.byte_offset,l.byte_length FROM messages m JOIN locations l USING(message_pk) JOIN mbox_generations g USING(generation_pk) WHERE m.message_pk=?1", [id], |r| Ok((r.get(0)?,r.get(1)?,r.get(2)?,r.get(3)?)))?;
        let components: Vec<_> = Path::new(&filename).components().collect();
        ensure!(
            matches!(components.as_slice(), [Component::Normal(_)]),
            "Unsafe MBOX filename"
        );
        let path = within(&self.root, &Path::new("data/mbox").join(filename))?;
        ensure!(
            length <= MAX_RECORD,
            "Message exceeds the experiment's 16 MiB record limit"
        );
        let mut file = File::open(path)?;
        ensure!(file.metadata()?.is_file(), "MBOX must be a regular file");
        ensure!(
            offset
                .checked_add(length)
                .is_some_and(|end| end <= file.metadata().map(|m| m.len()).unwrap_or(0)),
            "MBOX location extends beyond the file"
        );
        file.seek(SeekFrom::Start(offset))?;
        let mut record = vec![0; length as usize];
        file.read_exact(&mut record)?;
        verified_bytes(&record, &digest)
    }

    pub fn message(&self, id: i64) -> Result<Message> {
        let raw = self.raw_message(id)?;
        let digest = format!("{:x}", Sha256::digest(&raw));
        let parsed = mailparse::parse_mail(&raw)
            .context("Message integrity passed, but MIME parsing failed")?;
        let headers = ["From", "To", "Cc", "Date", "Subject"]
            .iter()
            .filter_map(|name| {
                parsed
                    .headers
                    .get_first_value(name)
                    .map(|value| format!("{name}: {value}"))
            })
            .collect::<Vec<_>>()
            .join("\n");
        let body = body_text(&parsed)?.unwrap_or_else(|| {
            "No displayable text body. Attachment viewing is not implemented.".into()
        });
        Ok(Message {
            headers: limited(headers),
            body: limited(body),
            sha256: digest,
        })
    }
}

fn within(root: &Path, relative: &Path) -> Result<PathBuf> {
    let path = root
        .join(relative)
        .canonicalize()
        .with_context(|| format!("Missing {}", relative.display()))?;
    ensure!(
        path.starts_with(root),
        "Archive file points outside the archive"
    );
    ensure!(path.is_file(), "Archive component is not a regular file");
    Ok(path)
}

fn limited(mut value: String) -> String {
    if value.len() > MAX_DISPLAY {
        let mut end = MAX_DISPLAY;
        while !value.is_char_boundary(end) {
            end -= 1;
        }
        value.truncate(end);
        value.push_str("\n[Display truncated at 256 KiB; original message is unchanged.]");
    }
    value
}

fn body_text(mail: &ParsedMail<'_>) -> Result<Option<String>> {
    display_part(mail, true)
}

fn display_part(mail: &ParsedMail<'_>, fallback: bool) -> Result<Option<String>> {
    if mail.get_content_disposition().disposition == DispositionType::Attachment {
        return Ok(None);
    }
    if !mail.subparts.is_empty() {
        if mail.ctype.mimetype == "multipart/alternative" {
            let ordered = || {
                mail.subparts
                    .iter()
                    .filter(|p| p.ctype.mimetype == "text/plain")
                    .chain(
                        mail.subparts
                            .iter()
                            .filter(|p| p.ctype.mimetype != "text/plain"),
                    )
            };
            // Try usable alternatives before displaying damaged encoded text.
            for part in ordered() {
                if let Ok(Some(body)) = display_part(part, false) {
                    return Ok(Some(body));
                }
            }
            if fallback {
                for part in ordered() {
                    if let Ok(Some(body)) = display_part(part, true) {
                        return Ok(Some(body));
                    }
                }
            }
            return Ok(None);
        }
        let mut parts = Vec::new();
        for part in &mail.subparts {
            if let Some(body) = display_part(part, fallback)? {
                parts.push(body);
            }
        }
        return Ok((!parts.is_empty()).then(|| limited(parts.join("\n\n"))));
    }
    if !matches!(mail.ctype.mimetype.as_str(), "text/plain" | "text/html") {
        return Ok(None);
    }
    let body = match mail.get_body() {
        Ok(body) => body,
        Err(error) if fallback => {
            let raw = match mail.get_body_encoded() {
                mailparse::body::Body::Base64(body)
                | mailparse::body::Body::QuotedPrintable(body) => body.get_raw(),
                mailparse::body::Body::SevenBit(body) | mailparse::body::Body::EightBit(body) => {
                    body.get_raw()
                }
                mailparse::body::Body::Binary(body) => body.get_raw(),
            };
            return Ok(Some(format!(
                "[MIME decoding failed: {error}; showing encoded text.]\n{}",
                String::from_utf8_lossy(&raw[..raw.len().min(MAX_DISPLAY)])
            )));
        }
        Err(error) => return Err(error.into()),
    };
    if mail.ctype.mimetype == "text/html" {
        match html2text::from_read(body.as_bytes(), 100) {
            Ok(text) => Ok(Some(limited(text))),
            Err(_) if fallback => Ok(Some(limited(body))),
            Err(error) => Err(error.into()),
        }
    } else {
        Ok(Some(limited(body)))
    }
}

pub fn sha256(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn verified_bytes(record: &[u8], expected: &str) -> Result<Vec<u8>> {
    let split = record
        .iter()
        .position(|b| *b == b'\n')
        .context("Missing MBOX envelope")?
        + 1;
    ensure!(record.starts_with(b"From "), "Invalid MBOX envelope");
    let stored = &record[split..];
    let mut rd = Vec::with_capacity(stored.len());
    let mut legacy = Vec::with_capacity(stored.len());
    for line in stored.split_inclusive(|b| *b == b'\n') {
        let depth = line.iter().take_while(|b| **b == b'>').count();
        rd.extend_from_slice(if depth > 0 && line[depth..].starts_with(b"From ") {
            &line[1..]
        } else {
            line
        });
        legacy.extend_from_slice(if line.starts_with(b">From ") {
            &line[1..]
        } else {
            line
        });
    }
    // Hash distinguishes writer-added LF/CRLF and adopted source envelopes.
    for prefix in [&[][..], &record[..split]] {
        for content in [rd.as_slice(), legacy.as_slice(), stored] {
            let candidate = [prefix, content].concat();
            for trim in [0, 1, 2] {
                if trim == 1 && !candidate.ends_with(b"\n")
                    || trim == 2 && !candidate.ends_with(b"\r\n")
                {
                    continue;
                }
                let bytes = &candidate[..candidate.len() - trim];
                if sha256(bytes) == expected {
                    return Ok(bytes.to_vec());
                }
            }
        }
    }
    bail!(
        "SHA-256 mismatch or unsupported ambiguous legacy MBOX quoting; message was not displayed"
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::worker::{Content, Request, Worker};
    use std::{fs, sync::mpsc::RecvTimeoutError};

    fn fixture() -> (tempfile::TempDir, PathBuf) {
        let dir = tempfile::tempdir().unwrap();
        // Fresh-checkout demo destinations may have several missing parents.
        let path = dir.path().join("missing/parents/reader # café");
        demo::create(&path).unwrap();
        (dir, path)
    }

    #[test]
    fn search_select_decode_preserves_all_archive_bytes() {
        // Reader requirement: real SQLite search + verified MIME display, no writes.
        let (_dir, path) = fixture();
        let files = ["archive.sqlite3", "search.sqlite3", "data/mbox/DEMO.mbox"];
        let before: Vec<_> = files
            .iter()
            .map(|p| fs::read(path.join(p)).unwrap())
            .collect();
        let archive = Archive::open(&path).unwrap();
        assert_eq!(archive.search("observatory").unwrap().len(), 2);
        assert_eq!(archive.search("observatory Friday").unwrap()[0].id, 1);
        assert_eq!(archive.search("café").unwrap()[0].id, 2);
        assert!(archive.search("notpresent").unwrap().is_empty());
        assert!(archive.search("\" OR *").unwrap().is_empty());
        let message = archive.message(1).unwrap();
        assert!(message.body.contains("\r\nFrom the hill"));
        assert!(message.body.contains(">From an older note"));
        assert_eq!(message.sha256, sha256(demo::MESSAGES[0]));
        let message = archive.message(2).unwrap();
        assert!(message.headers.contains("Subject: Café notes"));
        assert!(message.body.contains("Café lunch"));
        let html = archive.message(3).unwrap();
        assert!(html.body.contains("roses are blooming"));
        assert!(!html.body.contains("never executed"));
        drop(archive);
        for (file, bytes) in files.iter().zip(before) {
            assert_eq!(fs::read(path.join(file)).unwrap(), bytes);
        }
        assert!(!path.join("archive.sqlite3-journal").exists());
        assert!(!path.join("search.sqlite3-journal").exists());
        assert!(demo::create(&path).is_err());
    }

    #[test]
    fn refuses_corruption_missing_files_and_escaping_locations() {
        // Canonical integrity and path safety must fail visibly before displaying data.
        let (_dir, path) = fixture();
        let archive = Archive::open(&path).unwrap();
        let file = path.join("data/mbox/DEMO.mbox");
        let mut bytes = fs::read(&file).unwrap();
        bytes[70] ^= 1;
        fs::write(&file, &bytes).unwrap();
        assert!(archive
            .message(1)
            .unwrap_err()
            .to_string()
            .contains("SHA-256"));
        let db = Connection::open(path.join("archive.sqlite3")).unwrap();
        db.execute("UPDATE mbox_generations SET filename='../outside'", [])
            .unwrap();
        assert!(archive
            .message(2)
            .unwrap_err()
            .to_string()
            .contains("Unsafe"));
        db.execute(
            "UPDATE locations SET byte_length=?1 WHERE message_pk=2",
            [MAX_RECORD + 1],
        )
        .unwrap();
        db.execute("UPDATE mbox_generations SET filename='DEMO.mbox'", [])
            .unwrap();
        assert!(archive
            .message(2)
            .unwrap_err()
            .to_string()
            .contains("16 MiB"));
        drop(archive);
        fs::remove_file(path.join("search.sqlite3")).unwrap();
        assert!(Archive::open(&path).is_err());
        assert!(!path.join("search.sqlite3").exists());
    }

    #[test]
    fn wal_archive_is_refused_without_creating_sidecars() {
        // Read-only means no WAL shared-memory creation as a side effect of Open.
        let (_dir, path) = fixture();
        let database = Connection::open(path.join("search.sqlite3")).unwrap();
        database.execute_batch("PRAGMA journal_mode=WAL").unwrap();
        drop(database);
        let before = fs::read(path.join("search.sqlite3")).unwrap();
        assert!(Archive::open(&path)
            .err()
            .unwrap()
            .to_string()
            .contains("WAL-mode"));
        assert_eq!(fs::read(path.join("search.sqlite3")).unwrap(), before);
        assert!(!path.join("search.sqlite3-wal").exists());
        assert!(!path.join("search.sqlite3-shm").exists());
    }

    #[test]
    fn worker_recovers_from_bad_open_and_selects_search_result() {
        // The same typed channel path as the UI must survive user mistakes.
        let (_dir, path) = fixture();
        let worker = Worker::start(|| {}).unwrap();
        let request = |r| {
            worker.requests.send(r).unwrap();
            worker.replies.recv_timeout(Duration::from_secs(5)).unwrap()
        };
        assert!(request(Request::Open(path.join("absent"))).is_err());
        assert!(request(Request::Search("roses".into())).is_err());
        assert!(matches!(request(Request::Open(path)), Ok(Content::Rows(_))));
        let Ok(Content::Rows(rows)) = request(Request::Search("roses".into())) else {
            panic!("expected rows")
        };
        assert_eq!(rows.len(), 1);
        let Ok(Content::Message(message)) = request(Request::Select(rows[0].id)) else {
            panic!("expected message")
        };
        assert!(message.body.contains("roses"));
        assert!(request(Request::Select(-1)).is_err());
        assert!(matches!(
            request(Request::Search("café".into())),
            Ok(Content::Rows(_))
        ));
        drop(worker.requests);
        assert_eq!(
            worker.replies.recv_timeout(Duration::from_secs(1)).err(),
            Some(RecvTimeoutError::Disconnected)
        );
    }

    #[test]
    fn mime_alternative_prefers_plain_and_skips_attachment() {
        // Derived display must not show attachments as the message body.
        let raw=b"Content-Type: multipart/mixed; boundary=m\n\n--m\nContent-Type: multipart/alternative; boundary=a\n\n--a\nContent-Type: text/html\n\n<b>html version</b>\n--a\nContent-Type: text/plain\n\nplain version\n--a--\n--m\nContent-Type: text/plain\nContent-Disposition: attachment; filename=secret.txt\n\nattachment content\n--m--\n";
        let parsed = mailparse::parse_mail(raw).unwrap();
        let body = body_text(&parsed).unwrap().unwrap();
        assert!(body.contains("plain version"));
        assert!(!body.contains("html version"));
        assert!(!body.contains("attachment content"));
    }

    #[test]
    fn malformed_mime_keeps_siblings_and_tries_usable_alternatives() {
        // Retained malformed mail must remain displayable without changing bytes.
        let bad = b"Content-Type: text/plain; charset=utf-8\nContent-Transfer-Encoding: base64\n\n%%%invalid%%%";
        let mixed = [
            b"Content-Type: multipart/mixed; boundary=m\n\n--m\n".as_slice(),
            bad,
            b"\n--m\nContent-Type: text/plain\n\nvalid sibling\n--m--\n",
        ]
        .concat();
        let before = sha256(&mixed);
        let shown = body_text(&mailparse::parse_mail(&mixed).unwrap())
            .unwrap()
            .unwrap();
        assert!(shown.contains("valid sibling"));
        assert!(shown.contains("MIME decoding failed"));
        assert_eq!(sha256(&mixed), before);
        let alternative = [
            b"Content-Type: multipart/alternative; boundary=m\n\n--m\n".as_slice(),
            bad,
            b"\n--m\nContent-Type: text/html\n\n<b>usable alternative</b>\n--m--\n",
        ]
        .concat();
        let shown = body_text(&mailparse::parse_mail(&alternative).unwrap())
            .unwrap()
            .unwrap();
        assert!(shown.contains("usable alternative"));
        assert!(!shown.contains("invalid"));
        for raw in [
            bad.as_slice(),
            b"Content-Type: text/plain; charset=unknown-charset\n\nretained \xff text".as_slice(),
        ] {
            let before = sha256(raw);
            assert!(!body_text(&mailparse::parse_mail(raw).unwrap())
                .unwrap()
                .unwrap()
                .is_empty());
            assert_eq!(sha256(raw), before);
        }
    }

    #[test]
    fn stored_record_variants_restore_only_hash_matching_bytes() {
        // Preserve missing final LF, CRLF, and nested From quoting exactly.
        for raw in [
            b"Subject: x\n\nFrom a\n>From b\n>>From c".as_slice(),
            b"Subject: x\r\n\r\nbody\r\n",
        ] {
            let mut record = b"From example Tue Jan 02 10:00:00 2024\n".to_vec();
            for line in raw.split_inclusive(|b| *b == b'\n') {
                if line
                    .iter()
                    .copied()
                    .skip_while(|b| *b == b'>')
                    .collect::<Vec<_>>()
                    .starts_with(b"From ")
                {
                    record.push(b'>');
                }
                record.extend_from_slice(line);
            }
            record.push(b'\n');
            assert_eq!(verified_bytes(&record, &sha256(raw)).unwrap(), raw);
            assert!(verified_bytes(&record, &"0".repeat(64)).is_err());
        }
    }
}
