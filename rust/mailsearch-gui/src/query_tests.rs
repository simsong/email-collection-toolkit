// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
// Enforce the same search optimizer contract as the Python production compiler.
// Both languages consume the language-neutral case matrix in tests/fixtures.
// Real archive schemas contain one older match and twenty thousand unrelated rows.
// Every production header, ID and count statement is explained with its bindings.
// SQLite instruction budgets distinguish selective probes from catalog traversal.
// The fixture is disposable derived data; no personal mailbox or archive is used.
use super::*;
use crate::selectors::Selection;
use rusqlite::{params, Connection};
use std::sync::{
    atomic::{AtomicU64, Ordering},
    Arc,
};
#[derive(Deserialize)]
struct Case {
    query: String,
    attachments: bool,
    selections: Vec<Selection>,
    matches: i64,
    indexes: Vec<String>,
    subject_scan: bool,
}
struct Fixture {
    db: Connection,
    _directory: tempfile::TempDir,
}
fn fixture() -> Fixture {
    let mut db = Connection::open_in_memory().unwrap();
    db.execute_batch(include_str!(
        "../../../src/mailarchiver/sql/V1__archive.sql"
    ))
    .unwrap();
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("search.sqlite3");
    let search = Connection::open(&path).unwrap();
    search
        .execute_batch(include_str!("../../../src/mailarchiver/sql/V1__search.sql"))
        .unwrap();
    drop(search);
    db.execute("ATTACH ? AS search", [path.to_str().unwrap()])
        .unwrap();
    db.execute_batch(
        "CREATE TEMP VIEW address_search_names AS SELECT '' AS address,'' AS name WHERE 0",
    )
    .unwrap();
    let tx = db.transaction().unwrap();
    for (id, address) in [
        (1, "sender@example.net"),
        (2, "recipient@example.net"),
        (3, "copy@example.net"),
        (4, "blind@example.net"),
        (5, "unrelated@example.net"),
    ] {
        tx.execute(
            "INSERT INTO email_addresses VALUES(?1,?2)",
            params![id, address],
        )
        .unwrap();
    }
    tx.execute("INSERT INTO messages VALUES(1,'one','target',1,'planning meeting','2024-01-03T10:00:00+00:00','date','Archive')",[]).unwrap();
    for (id, role) in [(2, "to"), (3, "cc"), (4, "bcc")] {
        tx.execute("INSERT INTO recipients VALUES(1,?1,?2)", params![id, role])
            .unwrap();
    }
    for id in 2_i64..=20001 {
        tx.execute("INSERT INTO messages VALUES(?1,?2,?3,5,'','2025-01-01T00:00:00+00:00','date','Archive')",params![id,id.to_string(),crate::sha256(id.to_string().as_bytes())]).unwrap();
    }
    tx.execute_batch("INSERT INTO recipients SELECT message_pk,5,'to' FROM messages WHERE message_pk>1;
        INSERT INTO source_volumes VALUES(1,'target','{}','',''),(2,'noise','{}','','');
        INSERT INTO source_files(source_volume_pk,source_path,hierarchy_path,path_kind,source_kind)
        SELECT CASE WHEN message_pk=1 THEN 1 ELSE 2 END,CAST(message_pk AS TEXT),CASE WHEN message_pk=1 THEN 'mail/one' ELSE 'noise/'||message_pk END,'file','mbox' FROM messages;
        INSERT INTO ingest_runs(started_at) VALUES('2026-01-01');
        INSERT INTO observations(run_pk,message_pk,source_file_pk,disposition,detail) SELECT 1,message_pk,message_pk,'archived','' FROM messages;
        INSERT INTO search.message_fts(sha256,content) VALUES('target','Meeting agenda');
        INSERT INTO search.message_metadata(sha256,message_fts_rowid,attachment_count,preview) VALUES('target',1,0,'Meeting agenda');").unwrap();
    tx.commit().unwrap();
    Fixture {
        db,
        _directory: directory,
    }
}
#[test]
fn shared_python_optimizer_matrix_for_headers_ids_and_counts() {
    let cases: Vec<Case> =
        serde_json::from_str(include_str!("../../../tests/fixtures/search-contract.json")).unwrap();
    let fixture = fixture();
    let db = &fixture.db;
    let mut statements = 0;
    for case in cases {
        let tokens = case
            .selections
            .iter()
            .map(Selection::token)
            .collect::<Result<Vec<_>>>()
            .unwrap();
        for sort in ["date", "subject", "sender"] {
            for direction in ["ascending", "descending"] {
                let query = Query::parse(
                    &case.query,
                    sort,
                    direction,
                    case.attachments,
                    Some(&serde_json::json!(tokens)),
                )
                .unwrap();
                let mut operations = vec![
                    (query.headers(Some(10), 0, None).unwrap(), false),
                    (query.ids(None, None).unwrap(), false),
                    (query.id_batch(None).unwrap(), false),
                ];
                if direction == "ascending" {
                    operations.extend([
                        (query.count(None).unwrap(), true),
                        (query.count(Some(2)).unwrap(), true),
                    ]);
                }
                for (statement, count) in operations {
                    let plan: Vec<String> = db
                        .prepare(&format!("EXPLAIN QUERY PLAN {}", statement.sql))
                        .unwrap()
                        .query_map(rusqlite::params_from_iter(&statement.values), |r| r.get(3))
                        .unwrap()
                        .collect::<rusqlite::Result<_>>()
                        .unwrap();
                    assert!(
                        !plan
                            .iter()
                            .any(|p| p.starts_with("SCAN m ") || p.contains("CORRELATED")),
                        "{} {sort} {direction}: {plan:?}",
                        case.query
                    );
                    for index in &case.indexes {
                        assert!(
                            plan.iter()
                                .any(|p| p.starts_with("SEARCH ") && p.contains(index)),
                            "{}: missing {index} in {plan:?}",
                            case.query
                        );
                    }
                    let matched = |p: &&String| {
                        p.contains("VIRTUAL TABLE INDEX")
                            && p.rsplit(':').next().is_some_and(|v| v.contains('M'))
                    };
                    if !query.plan.terms.is_empty() && matches!(query.plan.filter, Filter::Hash) {
                        assert!(plan.iter().any(|p| matched(&p)), "{plan:?}");
                        if case.attachments {
                            assert!(
                                plan.iter()
                                    .filter(matched)
                                    .any(|p| p.contains("attachment_fts")),
                                "{plan:?}"
                            );
                        }
                    }
                    if case.subject_scan {
                        assert!(
                            plan.iter().any(|p| p.contains("SCAN subject_match")
                                && p.contains("messages_subject_message")),
                            "{plan:?}"
                        );
                    }
                    let steps = Arc::new(AtomicU64::new(0));
                    let counter = Arc::clone(&steps);
                    db.progress_handler(
                        100,
                        Some(move || {
                            counter.fetch_add(100, Ordering::Relaxed);
                            false
                        }),
                    );
                    let result: Vec<i64> = db
                        .prepare(&statement.sql)
                        .unwrap()
                        .query_map(rusqlite::params_from_iter(statement.values), |r| r.get(0))
                        .unwrap()
                        .collect::<rusqlite::Result<_>>()
                        .unwrap();
                    db.progress_handler(0, None::<fn() -> bool>);
                    assert_eq!(
                        result,
                        if count {
                            vec![case.matches]
                        } else if case.matches > 0 {
                            vec![1]
                        } else {
                            vec![]
                        },
                        "{} {sort} {direction}",
                        case.query
                    );
                    assert!(
                        steps.load(Ordering::Relaxed)
                            <= if case.subject_scan { 130000 } else { 5000 },
                        "{} {sort} {direction}: {} instructions {plan:?}",
                        case.query,
                        steps.load(Ordering::Relaxed)
                    );
                    statements += 1;
                }
            }
        }
    }
    assert_eq!(statements, 504);
}

#[test]
fn attachment_badges_use_indexed_hash_lookups_with_populated_processing() {
    // Search responsiveness: badge work scales with displayed IDs, not all states.
    let fixture = fixture();
    let path = fixture._directory.path().join("processing.sqlite3");
    let processing = Connection::open(&path).unwrap();
    processing
        .execute_batch(include_str!(
            "../../../src/mailarchiver/processing/sql/V2__processing.sql"
        ))
        .unwrap();
    drop(processing);
    let db = &fixture.db;
    db.execute("ATTACH ? AS identities", [path.to_str().unwrap()])
        .unwrap();
    db.execute_batch("INSERT INTO identities.messages SELECT sha256,message_id_normalized,sha256,'' FROM main.messages;
        INSERT INTO identities.message_state(message_id,root_item_json,catalog_message_pk) SELECT sha256,'{}',message_pk FROM main.messages;
        INSERT INTO identities.message_tags SELECT sha256,1 FROM main.messages WHERE message_pk IN(1,20001);").unwrap();
    db.execute_batch(ATTACHED_SEARCH_MESSAGES).unwrap();
    for ids in [vec![1, 2, 20001], (1..=500).collect()] {
        let statement = Query::attached_ids(&ids);
        let plan: Vec<String> = db
            .prepare(&format!("EXPLAIN QUERY PLAN {}", statement.sql))
            .unwrap()
            .query_map(rusqlite::params_from_iter(&statement.values), |row| {
                row.get(3)
            })
            .unwrap()
            .collect::<rusqlite::Result<_>>()
            .unwrap();
        for table in ["m", "s", "t", "mt"] {
            assert!(
                plan.iter()
                    .any(|p| p.starts_with(&format!("SEARCH {table} "))),
                "{plan:?}"
            );
        }
        let steps = Arc::new(AtomicU64::new(0));
        let counter = Arc::clone(&steps);
        db.progress_handler(
            100,
            Some(move || {
                counter.fetch_add(100, Ordering::Relaxed);
                false
            }),
        );
        let result: Vec<i64> = db
            .prepare(&statement.sql)
            .unwrap()
            .query_map(rusqlite::params_from_iter(statement.values), |row| {
                row.get(0)
            })
            .unwrap()
            .collect::<rusqlite::Result<_>>()
            .unwrap();
        db.progress_handler(0, None::<fn() -> bool>);
        assert_eq!(
            result,
            ids.iter()
                .copied()
                .filter(|id| [1, 20001].contains(id))
                .collect::<Vec<_>>()
        );
        assert!(
            steps.load(Ordering::Relaxed) <= 100 * ids.len() as u64 + 5000,
            "{plan:?}"
        );
    }
}

#[test]
fn institution_names_do_not_cross_join_all_addresses_and_domains() {
    // Name search must stay selective as independent identities/domains grow.
    let directory = tempfile::tempdir().unwrap();
    let root = directory.path().join("archive");
    crate::demo::create(&root).unwrap();
    let processing = Connection::open(root.join("processing.sqlite3")).unwrap();
    processing
        .execute_batch(include_str!(
            "../../../src/mailarchiver/processing/sql/V2__processing.sql"
        ))
        .unwrap();
    processing.execute_batch("WITH RECURSIVE n(x) AS(VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<3000) INSERT INTO organizations SELECT x,'Noise '||x,0 FROM n;
        INSERT INTO organization_domains SELECT 'noise'||organization_id||'.example.invalid',organization_id,0 FROM organizations;
        INSERT INTO addresses SELECT organization_id,'reader@noise'||organization_id||'.example.invalid','reader','noise'||organization_id||'.example.invalid' FROM organizations;
        INSERT INTO organizations VALUES(3001,'Target Institution',1);
        INSERT INTO organization_domains VALUES('test',3001,0),('example.test',3001,0);
        INSERT INTO addresses VALUES(3001,'alice@example.test','alice','example.test');").unwrap();
    drop(processing);
    let archive = crate::Archive::open(&root).unwrap();
    for (text, expected) in [
        ("from:alice", vec![1]),
        ("from:\"Target Institution\"", vec![1]),
        ("from:Noise", vec![]),
    ] {
        let query = Query::parse(text, "date", "descending", false, None).unwrap();
        let statement = query.ids(None, None).unwrap();
        let plan: Vec<String> = archive
            .db
            .prepare(&format!("EXPLAIN QUERY PLAN {}", statement.sql))
            .unwrap()
            .query_map(rusqlite::params_from_iter(&statement.values), |row| {
                row.get(3)
            })
            .unwrap()
            .collect::<rusqlite::Result<_>>()
            .unwrap();
        assert!(
            plan.iter()
                .any(|p| p.contains("AUTOMATIC") && p.contains("domain=?")),
            "{plan:?}"
        );
        let steps = Arc::new(AtomicU64::new(0));
        let counter = Arc::clone(&steps);
        archive.db.progress_handler(
            100,
            Some(move || counter.fetch_add(100, Ordering::Relaxed) > 1_000_000),
        );
        let result: Vec<i64> = archive
            .db
            .prepare(&statement.sql)
            .unwrap()
            .query_map(rusqlite::params_from_iter(statement.values), |row| {
                row.get(0)
            })
            .unwrap()
            .collect::<rusqlite::Result<_>>()
            .unwrap();
        archive.db.progress_handler(0, None::<fn() -> bool>);
        assert_eq!(result, expected, "{text}");
        assert!(
            steps.load(Ordering::Relaxed) <= 1_000_000,
            "{text}: {plan:?}"
        );
    }
}
