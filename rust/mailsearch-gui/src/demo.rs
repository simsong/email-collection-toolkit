// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
// Produce a tiny synthetic reader fixture without ingesting personal mail.
// Real repository schemas keep this fixture aligned with the archive format.
// Three UTF-8/MIME examples exercise search, selection, and message decoding.
// Canonical bytes are hashed before MBOXRD storage quoting is applied.
// Creation refuses any existing destination and never overwrites an archive.
// The fixture is for this reader experiment, not a complete BagIt export.
use crate::sha256;
use anyhow::Result;
use rusqlite::{params, Connection};
use std::{fs, path::Path};

pub const MESSAGES: [&[u8]; 3] = [
    b"From: Alice <alice@example.test>\r\nTo: Bob <bob@example.test>\r\nDate: Tue, 02 Jan 2024 10:00:00 +0000\r\nSubject: Observatory planning\r\nMessage-ID: <demo1@example.test>\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nMeet at the observatory on Friday.\r\nFrom the hill we can see Jupiter.\r\n>From an older note.\r\n",
    b"From: Bob <bob@example.test>\nTo: Alice <alice@example.test>\nSubject: =?UTF-8?Q?Caf=C3=A9_notes?=\nMessage-ID: <demo2@example.test>\nContent-Type: text/plain; charset=utf-8\nContent-Transfer-Encoding: quoted-printable\n\nCaf=C3=A9 lunch after the observatory visit.",
    b"From: Carol <carol@example.test>\nSubject: Garden update\nMessage-ID: <demo3@example.test>\nContent-Type: text/html; charset=utf-8\n\n<html><body><h1>Garden update</h1><p>The roses are blooming.</p><script>alert('never executed');</script><img src='https://example.test/tracker'></body></html>\n",
];

pub fn create(path: &Path) -> Result<()> {
    if let Some(parent) = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
    {
        fs::create_dir_all(parent)?;
    }
    fs::create_dir(path)?;
    fs::create_dir_all(path.join("data/mbox"))?;
    let db = Connection::open(path.join("archive.sqlite3"))?;
    db.execute_batch(include_str!(
        "../../../src/mailarchiver/sql/V1__archive.sql"
    ))?;
    let search = Connection::open(path.join("search.sqlite3"))?;
    search.execute_batch(include_str!("../../../src/mailarchiver/sql/V1__search.sql"))?;
    let mut mbox = Vec::new();
    let mut locations = Vec::new();
    for raw in MESSAGES {
        let offset = mbox.len();
        mbox.extend_from_slice(b"From demo@example.test Tue Jan 02 10:00:00 2024\n");
        for line in raw.split_inclusive(|b| *b == b'\n') {
            let depth = line.iter().take_while(|b| **b == b'>').count();
            if line[depth..].starts_with(b"From ") {
                mbox.push(b'>');
            }
            mbox.extend_from_slice(line);
        }
        if !mbox.ends_with(b"\n") {
            mbox.push(b'\n');
        }
        locations.push((offset as i64, (mbox.len() - offset) as i64));
    }
    fs::write(path.join("data/mbox/DEMO.mbox"), &mbox)?;
    db.execute(
        "INSERT INTO mbox_generations VALUES(1,'DEMO.mbox',?1,3,?2)",
        params![sha256(&mbox), mbox.len()],
    )?;
    let subjects = ["Observatory planning", "Café notes", "Garden update"];
    let contents = [
        "Alice Bob Observatory planning Meet Friday Jupiter hill",
        "Bob Alice Café notes lunch observatory visit",
        "Carol Garden update roses blooming",
    ];
    for (index, raw) in MESSAGES.iter().enumerate() {
        let id = index as i64 + 1;
        let digest = sha256(raw);
        db.execute(
            "INSERT INTO email_addresses VALUES(?1,?2)",
            params![
                id,
                format!("{}@example.test", ["alice", "bob", "carol"][index])
            ],
        )?;
        db.execute(
            "INSERT INTO messages VALUES(?1,?2,?3,?1,?4,'2024-01-02T10:00:00Z','header','Archive')",
            params![
                id,
                format!("demo{id}@example.test"),
                digest,
                subjects[index]
            ],
        )?;
        db.execute(
            "INSERT INTO locations VALUES(?1,1,?2,?3)",
            params![id, locations[index].0, locations[index].1],
        )?;
        search.execute(
            "INSERT INTO message_fts(sha256,content) VALUES(?1,?2)",
            params![digest, contents[index]],
        )?;
        search.execute("INSERT INTO message_metadata(sha256,message_fts_rowid,attachment_count,preview) VALUES(?1,?2,0,?3)", params![digest, search.last_insert_rowid(), contents[index]])?;
    }
    Ok(())
}
