// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
//! Verify imported database records against canonical MBOX bytes independently.
//! Open existing SQLite catalogs read-only and check relational integrity.
//! Stream every mailbox and hash-select each catalogued original message.
//! Check complete record coverage and the search index's digest membership.
//! Require a quiescent archive; never repair data or run application Python.
//! This first Rust migration leaves semantic and full BagIt checks separate.

mod records;
pub use records::{recover_bytes, recover_owned};

use anyhow::{ensure, Context, Result};
use rusqlite::{Connection, OpenFlags};
use sha2::{Digest, Sha256};
use std::collections::BTreeSet;
use std::fs::{self, File};
use std::io::{Read, Seek, SeekFrom};
use std::path::{Component, Path, PathBuf};

#[derive(Debug, PartialEq, Eq)]
pub struct Report {
    pub messages: u64,
    pub mailboxes: u64,
    pub observations: u64,
    pub indexed_messages: u64,
}

pub fn file_sha256(path: &Path) -> Result<String> {
    let mut file = File::open(path).with_context(|| format!("open {}", path.display()))?;
    let mut hash = Sha256::new();
    let mut buffer = [0; 65536];
    loop {
        let count = file.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        hash.update(&buffer[..count]);
    }
    Ok(format!("{:x}", hash.finalize()))
}

fn contained_file(root: &Path, path: &Path) -> Result<PathBuf> {
    let resolved = path
        .canonicalize()
        .with_context(|| format!("resolve {}", path.display()))?;
    ensure!(
        resolved.starts_with(root) && resolved.is_file(),
        "file escapes archive or is not regular: {}",
        path.display()
    );
    Ok(resolved)
}

pub fn open_catalog(root: &Path) -> Result<Connection> {
    let root = root.canonicalize()?;
    let path = contained_file(&root, &root.join("archive.sqlite3"))?;
    require_quiescent_database(&path)?;
    let connection = Connection::open_with_flags(
        path,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_URI,
    )?;
    connection.execute_batch("PRAGMA query_only=ON; BEGIN;")?;
    Ok(connection)
}

fn require_quiescent_database(path: &Path) -> Result<()> {
    // Read-only SQLite can still create WAL sidecars; inspect before opening it.
    let mut header = [0; 20];
    File::open(path)?.read_exact(&mut header)?;
    ensure!(
        &header[..16] == b"SQLite format 3\0",
        "invalid SQLite header"
    );
    ensure!(
        header[18..] == [1, 1],
        "WAL-mode database; checkpoint before verifying"
    );
    for suffix in ["-journal", "-wal", "-shm"] {
        let mut sidecar = path.as_os_str().to_os_string();
        sidecar.push(suffix);
        ensure!(
            !Path::new(&sidecar).try_exists()?,
            "database journal present; stop writers and recover before verifying"
        );
    }
    Ok(())
}

pub fn count(connection: &Connection, sql: &str) -> Result<u64> {
    Ok(connection.query_row(sql, [], |row| row.get(0))?)
}

fn require_zero(connection: &Connection, sql: &str, description: &str) -> Result<()> {
    let count = count(connection, sql)?;
    ensure!(count == 0, "{description}: {count} invalid rows");
    Ok(())
}

pub fn verify_archive(root: &Path) -> Result<Report> {
    let root = root.canonicalize().context("archive directory")?;
    ensure!(
        !root.join(".mailarchiver-pending.json").exists(),
        "pending publication; recover before verifying"
    );
    let connection = open_catalog(&root)?;
    let search_path = contained_file(&root, &root.join("search.sqlite3"))?;
    require_quiescent_database(&search_path)?;
    let mut search_uri = url::Url::from_file_path(search_path)
        .map_err(|()| anyhow::anyhow!("invalid search path"))?;
    search_uri.set_query(Some("mode=ro"));
    connection.execute("ATTACH DATABASE ?1 AS search", [search_uri.as_str()])?;
    for schema in ["main", "search"] {
        let versions: Vec<i64> = connection
            .prepare(&format!("SELECT version FROM {schema}.schema_info"))?
            .query_map([], |row| row.get(0))?
            .collect::<rusqlite::Result<_>>()?;
        ensure!(
            versions == [1],
            "unsupported {schema} schema version: {versions:?}"
        );
        let checks: Vec<String> = connection
            .prepare(&format!("PRAGMA {schema}.integrity_check"))?
            .query_map([], |row| row.get(0))?
            .collect::<rusqlite::Result<_>>()?;
        ensure!(checks == ["ok"], "{schema} SQLite integrity: {checks:?}");
        ensure!(
            connection
                .prepare(&format!("PRAGMA {schema}.foreign_key_check"))?
                .query([])?
                .next()?
                .is_none(),
            "{schema} foreign key violation"
        );
    }
    require_zero(
        &connection,
        "SELECT count(*) FROM ingest_runs WHERE completed_at IS NULL",
        "unfinished ingest",
    )?;
    require_zero(&connection, "SELECT count(*) FROM messages m LEFT JOIN locations l USING(message_pk) WHERE l.message_pk IS NULL", "message missing location")?;
    require_zero(&connection, "SELECT count(*) FROM messages WHERE category NOT IN ('Archive','Sent','INFECTED','MALFORMED')", "unknown message category")?;
    require_zero(&connection, "SELECT count(*) FROM messages m WHERE NOT EXISTS (SELECT 1 FROM observations o WHERE o.message_pk=m.message_pk AND o.disposition='archived' AND o.raw_sha256=m.sha256)", "message missing publication observation")?;
    require_zero(&connection, "SELECT count(*) FROM observations o LEFT JOIN messages m USING(message_pk) WHERE o.disposition IN ('archived','duplicate') AND (m.message_pk IS NULL OR o.raw_sha256<>m.sha256)", "observation digest/link mismatch")?;

    let mut names = BTreeSet::new();
    let mut verified = 0_u64;
    let mut generations = connection.prepare("SELECT generation_pk, filename, sha256, message_count, byte_count FROM mbox_generations ORDER BY filename")?;
    let mut rows = generations.query([])?;
    while let Some(row) = rows.next()? {
        let key: i64 = row.get(0)?;
        let filename: String = row.get(1)?;
        let digest: String = row.get(2)?;
        let expected_count: u64 = row.get(3)?;
        let expected_size: u64 = row.get(4)?;
        let mut components = Path::new(&filename).components();
        ensure!(
            matches!(components.next(), Some(Component::Normal(_)))
                && components.next().is_none()
                && !filename.contains('\\')
                && filename.ends_with(".mbox"),
            "unsafe mailbox filename: {filename}"
        );
        let path = contained_file(&root, &root.join("data/mbox").join(&filename))?;
        ensure!(
            fs::metadata(&path)?.len() == expected_size,
            "{filename}: mailbox byte count mismatch"
        );
        ensure!(
            file_sha256(&path)? == digest,
            "{filename}: mailbox SHA-256 mismatch"
        );
        let mut file = File::open(&path)?;
        let mut locations = connection.prepare("SELECT m.message_pk, m.sha256, l.byte_offset, l.byte_length, m.category FROM locations l JOIN messages m USING(message_pk) WHERE generation_pk=?1 ORDER BY byte_offset")?;
        let mut locations = locations.query([key])?;
        let mut next = 0_u64;
        let mut found = 0_u64;
        while let Some(location) = locations.next()? {
            let message: i64 = location.get(0)?;
            let digest: String = location.get(1)?;
            let offset: u64 = location.get(2)?;
            let length: u64 = location.get(3)?;
            let category: String = location.get(4)?;
            let end = offset
                .checked_add(length)
                .context("record offset overflow")?;
            ensure!(
                offset == next && end < expected_size,
                "{filename} message {message}: record gap, overlap or out-of-bounds location"
            );
            let quarantined = filename.starts_with("INFECTED") || filename.starts_with("MALFORMED");
            ensure!(
                quarantined == matches!(category.as_str(), "INFECTED" | "MALFORMED"),
                "{filename} message {message}: quarantine/category mismatch"
            );
            records::verify_record(&mut file, offset, length, &digest)
                .with_context(|| format!("{filename} message {message} at {offset}+{length}"))?;
            file.seek(SeekFrom::Start(end))?;
            let mut separator = [0];
            file.read_exact(&mut separator)?;
            ensure!(
                separator == *b"\n",
                "{filename} message {message}: invalid record separator"
            );
            next = end + 1;
            found += 1;
        }
        ensure!(
            next == expected_size && found == expected_count,
            "{filename}: record coverage/count mismatch"
        );
        verified += found;
        names.insert(filename);
    }
    let mut disk_names = BTreeSet::new();
    for entry in fs::read_dir(root.join("data/mbox"))? {
        let entry = entry?;
        if entry.path().extension().is_some_and(|ext| ext == "mbox") {
            disk_names.insert(
                entry
                    .file_name()
                    .into_string()
                    .map_err(|_| anyhow::anyhow!("non-UTF8 mailbox name"))?,
            );
        }
    }
    ensure!(disk_names == names, "unregistered or missing MBOX files");
    let messages = count(&connection, "SELECT count(*) FROM messages")?;
    ensure!(
        verified == messages,
        "canonical message/location coverage mismatch"
    );
    verify_search(&connection)?;
    Ok(Report {
        messages,
        mailboxes: names.len() as u64,
        observations: count(&connection, "SELECT count(*) FROM observations")?,
        indexed_messages: count(&connection, "SELECT count(*) FROM search.message_metadata")?,
    })
}

fn verify_search(connection: &Connection) -> Result<()> {
    for (sql, description) in [
        ("SELECT count(*) FROM (SELECT sha256 FROM messages WHERE category IN ('Archive','Sent') EXCEPT SELECT sha256 FROM search.message_metadata)", "search missing catalog digest"),
        ("SELECT count(*) FROM (SELECT sha256 FROM search.message_metadata EXCEPT SELECT sha256 FROM messages WHERE category IN ('Archive','Sent'))", "search contains unknown/quarantined digest"),
        ("SELECT count(*) FROM search.message_metadata m LEFT JOIN search.message_fts f ON f.rowid=m.message_fts_rowid WHERE f.rowid IS NULL OR f.sha256<>m.sha256", "message FTS mapping mismatch"),
        ("SELECT count(*) FROM search.message_fts f LEFT JOIN search.message_metadata m ON m.message_fts_rowid=f.rowid WHERE m.sha256 IS NULL", "unmapped message FTS row"),
        ("SELECT count(*) FROM search.message_metadata m LEFT JOIN search.attachment_fts f ON f.rowid=m.attachment_fts_rowid WHERE m.attachment_fts_rowid IS NOT NULL AND (f.rowid IS NULL OR f.sha256<>m.sha256)", "attachment FTS mapping mismatch"),
        ("SELECT count(*) FROM search.attachment_fts f LEFT JOIN search.message_metadata m ON m.attachment_fts_rowid=f.rowid WHERE m.sha256 IS NULL", "unmapped attachment FTS row"),
        ("SELECT count(*) FROM search.message_metadata m WHERE attachment_count<>(SELECT count(*) FROM search.message_attachments a WHERE a.sha256=m.sha256)", "attachment metadata count mismatch"),
    ] { require_zero(connection, sql, description)?; }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn wal_and_journals_are_rejected_without_archive_writes() -> Result<()> {
        // Independent verification must fail before SQLite creates WAL sidecars.
        for name in ["archive.sqlite3", "search.sqlite3"] {
            let directory = tempfile::tempdir()?;
            for database in ["archive.sqlite3", "search.sqlite3"] {
                let connection = Connection::open(directory.path().join(database))?;
                connection.execute_batch("CREATE TABLE fixture(value);")?;
            }
            let path = directory.path().join(name);
            {
                let connection = Connection::open(&path)?;
                connection.execute_batch("PRAGMA journal_mode=WAL;")?;
            }
            let before = file_sha256(&path)?;
            let error = verify_archive(directory.path()).expect_err("WAL database accepted");
            ensure!(error.to_string().contains("WAL-mode"), "{error:#}");
            ensure!(
                file_sha256(&path)? == before,
                "verification changed database"
            );
            ensure!(
                fs::read_dir(directory.path())?.count() == 2,
                "verification created sidecars"
            );
        }
        let directory = tempfile::tempdir()?;
        let path = directory.path().join("archive.sqlite3");
        Connection::open(&path)?.execute_batch("CREATE TABLE fixture(value);")?;
        fs::write(directory.path().join("archive.sqlite3-journal"), b"pending")?;
        let before = file_sha256(&path)?;
        let error = open_catalog(directory.path())
            .err()
            .context("journal database accepted")?;
        ensure!(error.to_string().contains("journal present"), "{error:#}");
        ensure!(
            file_sha256(&path)? == before,
            "verification changed database"
        );
        ensure!(
            fs::read(directory.path().join("archive.sqlite3-journal"))? == b"pending",
            "verification changed journal"
        );
        Ok(())
    }
}
