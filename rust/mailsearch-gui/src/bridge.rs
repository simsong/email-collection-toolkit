// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
// Adapt the existing HTML search interface to the read-only Rust archive core.
// The external JSON protocol matches the existing frontend's method signatures.
// A foreground worker owns message reads; a separate worker runs staged searches.
// Replies preserve request IDs so the frontend can discard stale search results.
// Unsupported write/import operations fail explicitly instead of pretending success.
// The same dispatcher runs in a native webview and in headless browser tests.
use crate::{Archive, Message};
use anyhow::{bail, ensure, Context, Result};
use rusqlite::types::Value as SqlValue;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    path::Path,
    time::{Duration, Instant},
};

#[derive(Deserialize)]
pub struct Request {
    pub id: u64,
    pub method: String,
    #[serde(default)]
    pub args: Vec<Value>,
}
#[derive(Serialize)]
pub struct Reply {
    pub id: u64,
    pub result: Option<Value>,
    pub error: Option<String>,
}
pub struct Bridge {
    pub(crate) archive: Archive,
    search: Option<crate::search::Search>,
    pub(crate) cancellation: Option<(std::sync::Arc<std::sync::atomic::AtomicU64>, u64)>,
    selected: Option<(i64, Message)>,
}

impl Bridge {
    pub fn open(path: &Path) -> Result<Self> {
        Ok(Self {
            archive: Archive::open(path)?,
            selected: None,
            search: None,
            cancellation: None,
        })
    }
    pub fn reply(&mut self, request: Request) -> Reply {
        match self.call(&request.method, &request.args) {
            Ok(value) => Reply {
                id: request.id,
                result: Some(value),
                error: None,
            },
            Err(error) => Reply {
                id: request.id,
                result: None,
                error: Some(format!("{error:#}")),
            },
        }
    }
    fn call(&mut self, method: &str, args: &[Value]) -> Result<Value> {
        // The previous query's progress deadline must never affect another method.
        self.archive.db.progress_handler(0, None::<fn() -> bool>);
        match method {
            "status" => {
                let count: i64 =
                    self.archive
                        .db
                        .query_row("SELECT count(*) FROM messages", [], |r| r.get(0))?;
                Ok(
                    json!({"archive":self.archive.root,"ready":true,"message_count":count,"file_drag_supported":false,"configuration":{"search_highlight_background":"#fff0a6"}}),
                )
            }
            "activate" | "request_previews" => Ok(json!(true)),
            "ingest_overview" => Ok(json!({"status":null})),
            "saved_filter_sets" => Ok(json!({"filter_sets":[]})),
            "suggestions" => Ok(json!({"items":[],"prefix":""})),
            "search" => self.search(args),
            "search_batch" => self.search_batch(args),
            "search_start" => {
                if self.search.is_none() {
                    self.search = Some(crate::search::Search::new(self.archive.root.clone())?);
                }
                self.search.as_ref().unwrap().start(
                    text(args, 0, ""),
                    text(args, 1, "date"),
                    text(args, 2, "descending"),
                )
            }
            "search_cancel" => {
                if let Some(search) = &self.search {
                    search.cancel();
                }
                Ok(json!(true))
            }
            "search_status" | "search_advance" | "search_page" => {
                let generation = args
                    .first()
                    .and_then(Value::as_u64)
                    .context("Missing search generation")?;
                let search = self.search.as_ref().context("No search started")?;
                if method == "search_status" {
                    return Ok(search.status(generation));
                }
                if method == "search_advance" {
                    search.advance(
                        generation,
                        args.get(1).and_then(Value::as_u64).unwrap_or(0).min(2) as u8,
                    );
                    return Ok(json!(true));
                }
                let offset = args
                    .get(1)
                    .and_then(Value::as_u64)
                    .context("Missing page offset")?;
                let limit = args
                    .get(2)
                    .and_then(Value::as_u64)
                    .unwrap_or(512)
                    .clamp(1, 512) as usize;
                let Some(ids) = search.page(generation, usize::try_from(offset)?, limit) else {
                    return Ok(json!({"stale":true}));
                };
                let mut statement = self.archive.db.prepare("SELECT m.message_pk,a.address,m.subject,m.date_utc,coalesce(mm.attachment_count,0),(SELECT group_concat(e.address, ', ') FROM recipients r JOIN email_addresses e USING(address_pk) WHERE r.message_pk=m.message_pk) FROM messages m JOIN email_addresses a ON a.address_pk=m.sender_address_pk LEFT JOIN search.message_metadata mm USING(sha256) WHERE m.message_pk=?1")?;
                let rows = ids.into_iter().map(|id| statement.query_row([id], |r| Ok(json!({"message_pk":r.get::<_,i64>(0)?,"sender":r.get::<_,String>(1)?,"subject":r.get::<_,String>(2)?,"date_utc":r.get::<_,String>(3)?,"attachment_count":r.get::<_,i64>(4)?,"recipients":r.get::<_,Option<String>>(5)?.unwrap_or_default(),"attached_message":false})))).collect::<rusqlite::Result<Vec<_>>>()?;
                Ok(json!({"results":rows}))
            }
            "take_previews" => {
                let ids: Vec<i64> =
                    serde_json::from_value(args.first().cloned().unwrap_or(json!([])))?;
                ensure!(ids.len() <= 2000, "Too many previews");
                let mut previews = Vec::new();
                for id in ids {
                    let value=self.archive.db.query_row("SELECT coalesce(mm.preview,'') FROM messages m LEFT JOIN search.message_metadata mm USING(sha256) WHERE message_pk=?1",[id],|r|r.get::<_,String>(0))?;
                    previews.push(json!({"message_pk":id,"preview":value.chars().take(240).collect::<String>()}));
                }
                Ok(json!({"previews":previews,"pending":false,"error":null}))
            }
            "message" => {
                let id = number(args, 0)?;
                let message = self.archive.message(id)?;
                let (subject,date_source,filename):(String,String,String)=self.archive.db.query_row("SELECT m.subject,m.date_source,g.filename FROM messages m JOIN locations l USING(message_pk) JOIN mbox_generations g USING(generation_pk) WHERE message_pk=?1",[id],|r|Ok((r.get(0)?,r.get(1)?,r.get(2)?)))?;
                let headers: Vec<_> = message
                    .headers
                    .lines()
                    .filter_map(|line| line.split_once(": "))
                    .map(|(name, value)| json!({"name":name,"value":value}))
                    .collect();
                let result = json!({"message_pk":id,"subject":subject,"date_source":date_source,"headers":headers,
                    "body_parts":[{"part_id":0,"content_type":"text/plain","label":"Message text"},{"part_id":1,"content_type":"text/plain","label":"Headers"}],
                    "preferred_part_id":0,"attachments":[],"archive_path":self.archive.root.join("data/mbox").join(filename),"source_locations":[],"attached_origins":[]});
                self.selected = Some((id, message));
                Ok(result)
            }
            "part" => {
                let id = number(args, 0)?;
                let part = number(args, 1)?;
                ensure!(part == 0 || part == 1, "Unsupported message part");
                if self.selected.as_ref().map(|v| v.0) != Some(id) {
                    self.selected = Some((id, self.archive.message(id)?));
                }
                let message = &self.selected.as_ref().context("Message unavailable")?.1;
                Ok(
                    json!({"part_id":part,"kind":"text","content_type":"text/plain","content":if part==0 {&message.body} else {&message.headers},"remote_content_blocked":false}),
                )
            }
            _ => bail!(
                "This operation is not yet available in the read-only Rust experiment: {method}"
            ),
        }
    }

    pub(crate) fn search_guard(&self, duration: Duration) {
        let deadline = Instant::now() + duration;
        let cancellation = self.cancellation.clone();
        self.archive.db.progress_handler(
            1000,
            Some(move || {
                Instant::now() >= deadline
                    || cancellation.as_ref().is_some_and(|(generation, expected)| {
                        generation.load(std::sync::atomic::Ordering::SeqCst) != *expected
                    })
            }),
        );
    }

    pub(crate) fn search_batch(&self, args: &[Value]) -> Result<Value> {
        const SCAN_SIZE: usize = 512;
        let query = text(args, 0, "");
        ensure!(query.len() <= 4096, "Search is limited to 4096 bytes");
        let (sort, source) = match text(args, 2, "date") {
            "date" => ("m.date_utc", "messages m INDEXED BY messages_date_message JOIN email_addresses a ON a.address_pk=m.sender_address_pk"),
            "subject" => ("lower(m.subject)", "messages m INDEXED BY messages_subject_message JOIN email_addresses a ON a.address_pk=m.sender_address_pk"),
            "sender" => ("lower(a.address)", "email_addresses a INDEXED BY email_addresses_lower_address CROSS JOIN messages m INDEXED BY messages_sender_address_pk ON m.sender_address_pk=a.address_pk"),
            _ => bail!("Unknown sort field"),
        };
        let (direction, comparison) = match text(args, 3, "descending") {
            "ascending" => ("ASC", ">"),
            "descending" => ("DESC", "<"),
            _ => bail!("Unknown sort direction"),
        };
        let cursor = args.get(1).filter(|v| !v.is_null());
        let mut parameters = Vec::<SqlValue>::new();
        let condition = if let Some(cursor) = cursor {
            parameters.push(
                cursor
                    .get("key")
                    .and_then(Value::as_str)
                    .context("Invalid search cursor")?
                    .to_string()
                    .into(),
            );
            parameters.push(
                cursor
                    .get("id")
                    .and_then(Value::as_i64)
                    .context("Invalid search cursor")?
                    .into(),
            );
            format!("WHERE ({sort},m.message_pk) {comparison} (?,?)")
        } else {
            String::new()
        };
        // Scan a bounded ordered catalog slice before testing FTS row IDs. Never
        // materialize and sort the complete FTS match set for the first response.
        let candidates_sql = format!("SELECT m.message_pk,{sort} FROM {source} {condition} ORDER BY {sort} {direction},m.message_pk {direction} LIMIT {}", SCAN_SIZE + 1);
        self.search_guard(Duration::from_secs(15));
        let result = (|| -> Result<Value> {
            let mut statement = self.archive.db.prepare(&candidates_sql)?;
            let mut candidates = statement
                .query_map(rusqlite::params_from_iter(parameters), |r| {
                    Ok((r.get::<_, i64>(0)?, r.get::<_, String>(1)?))
                })?
                .collect::<rusqlite::Result<Vec<_>>>()?;
            let more = candidates.len() > SCAN_SIZE;
            candidates.truncate(SCAN_SIZE);
            let next = candidates
                .last()
                .map(|(id, key)| json!({"id":id,"key":key}));
            let (mut clauses, mut values, terms) = query_parts(query)?;
            for clause in &mut clauses {
                if clause.starts_with("m.sha256 IN(") {
                    *clause = "EXISTS(SELECT 1 FROM search.message_fts WHERE rowid=mm.message_fts_rowid AND message_fts MATCH ?)".into();
                }
            }
            let ids = candidates
                .iter()
                .map(|(id, _)| id.to_string())
                .collect::<Vec<_>>()
                .join(",");
            if ids.is_empty() {
                return Ok(
                    json!({"results":[],"has_more":false,"cursor":null,"highlight_terms":terms,"scanned":0}),
                );
            }
            clauses.insert(0, format!("m.message_pk IN ({ids})"));
            let sql = format!("SELECT m.message_pk,a.address,m.subject,m.date_utc,coalesce(mm.attachment_count,0),(SELECT group_concat(e.address, ', ') FROM recipients r JOIN email_addresses e USING(address_pk) WHERE r.message_pk=m.message_pk) FROM messages m JOIN email_addresses a ON a.address_pk=m.sender_address_pk LEFT JOIN search.message_metadata mm USING(sha256) WHERE {} ORDER BY {sort} {direction},m.message_pk {direction}",clauses.join(" AND "));
            let mut statement = self.archive.db.prepare(&sql)?;
            let rows = statement.query_map(rusqlite::params_from_iter(values.drain(..)), |r| Ok(json!({"message_pk":r.get::<_,i64>(0)?,"sender":r.get::<_,String>(1)?,"subject":r.get::<_,String>(2)?,"date_utc":r.get::<_,String>(3)?,"attachment_count":r.get::<_,i64>(4)?,"recipients":r.get::<_,Option<String>>(5)?.unwrap_or_default(),"attached_message":false})))?.collect::<rusqlite::Result<Vec<_>>>()?;
            Ok(
                json!({"results":rows,"has_more":more,"cursor":next,"highlight_terms":terms,"scanned":candidates.len()}),
            )
        })();
        self.archive.db.progress_handler(0, None::<fn() -> bool>);
        match result {
            Err(error) if error.downcast_ref::<rusqlite::Error>().is_some_and(|e|e.sqlite_error_code()==Some(rusqlite::ErrorCode::OperationInterrupted)) => bail!("Search batch timed out after 15 seconds. Results already displayed remain available."),
            other => other,
        }
    }

    fn search(&self, args: &[Value]) -> Result<Value> {
        let query = text(args, 0, "");
        ensure!(query.len() <= 4096, "Search is limited to 4096 bytes");
        let offset = args
            .get(1)
            .and_then(Value::as_u64)
            .unwrap_or(0)
            .min(100_000);
        let sort = match text(args, 2, "date") {
            "date" => "m.date_utc",
            "subject" => "lower(m.subject)",
            "sender" => "lower(a.address)",
            _ => bail!("Unknown sort field"),
        };
        let direction = match text(args, 3, "descending") {
            "ascending" => "ASC",
            "descending" => "DESC",
            _ => bail!("Unknown sort direction"),
        };
        ensure!(
            !args.get(4).and_then(Value::as_bool).unwrap_or(false),
            "Attachment search has not yet been ported"
        );
        ensure!(
            args.get(5)
                .and_then(Value::as_array)
                .is_none_or(Vec::is_empty),
            "Mailbox filtering has not yet been ported"
        );
        let limit = args.get(6).and_then(Value::as_u64).unwrap_or(2000);
        let limit = if limit == 0 { 100_000 } else { limit.min(2000) };
        let (clauses, values, terms) = query_parts(query)?;
        let sql=format!("SELECT m.message_pk,a.address,m.subject,m.date_utc,coalesce(mm.attachment_count,0),(SELECT group_concat(e.address, ', ') FROM recipients r JOIN email_addresses e USING(address_pk) WHERE r.message_pk=m.message_pk) FROM messages m JOIN email_addresses a ON a.address_pk=m.sender_address_pk LEFT JOIN search.message_metadata mm USING(sha256) WHERE {} ORDER BY {sort} {direction},m.message_pk {direction} LIMIT {} OFFSET {offset}",if clauses.is_empty(){"1".into()}else{clauses.join(" AND ")},limit+1);
        let deadline = Instant::now() + Duration::from_secs(15);
        self.archive
            .db
            .progress_handler(1000, Some(move || Instant::now() >= deadline));
        let result = (|| -> Result<Vec<Value>> {
            let mut statement = self.archive.db.prepare(&sql)?;
            let rows=statement.query_map(rusqlite::params_from_iter(values),|r| Ok(json!({"message_pk":r.get::<_,i64>(0)?,"sender":r.get::<_,String>(1)?,"subject":r.get::<_,String>(2)?,"date_utc":r.get::<_,String>(3)?,"attachment_count":r.get::<_,i64>(4)?,"recipients":r.get::<_,Option<String>>(5)?.unwrap_or_default(),"attached_message":false})))?;
            Ok(rows.collect::<rusqlite::Result<_>>()?)
        })();
        self.archive.db.progress_handler(0, None::<fn() -> bool>);
        let mut rows = match result {
            Ok(rows) => rows,
            Err(error)
                if error.downcast_ref::<rusqlite::Error>().is_some_and(|e| {
                    e.sqlite_error_code() == Some(rusqlite::ErrorCode::OperationInterrupted)
                }) =>
            {
                bail!("Search timed out after 15 seconds. Try more specific words.")
            }
            Err(error) => return Err(error),
        };
        let more = rows.len() > limit as usize;
        ensure!(
            !(more && offset > 0),
            "More than 100,000 additional results; narrow the search."
        );
        rows.truncate(limit as usize);
        Ok(
            json!({"results":rows,"offset":offset,"has_more":more,"highlight_terms":terms,"error":null}),
        )
    }
}

fn text<'a>(args: &'a [Value], index: usize, default: &'a str) -> &'a str {
    args.get(index).and_then(Value::as_str).unwrap_or(default)
}
fn number(args: &[Value], index: usize) -> Result<i64> {
    args.get(index)
        .and_then(Value::as_i64)
        .context("Missing message/part ID")
}

pub(crate) fn query_parts(query: &str) -> Result<(Vec<String>, Vec<SqlValue>, Vec<String>)> {
    let mut tokens = Vec::new();
    let mut token = String::new();
    let mut quoted = false;
    for ch in query.chars() {
        if ch == '"' {
            quoted = !quoted;
        } else if ch.is_whitespace() && !quoted {
            if !token.is_empty() {
                tokens.push(std::mem::take(&mut token));
            }
        } else {
            token.push(ch);
        }
    }
    ensure!(!quoted, "Close the quoted search phrase");
    if !token.is_empty() {
        tokens.push(token);
    }
    let mut clauses = Vec::new();
    let mut values = Vec::new();
    let mut terms = Vec::new();
    let mut fulltext = Vec::new();
    for token in tokens {
        if let Some((field, value)) = token.split_once(':') {
            ensure!(!value.is_empty(), "Enter a value after {field}:");
            let pattern = format!(
                "%{}%",
                value
                    .replace('\\', "\\\\")
                    .replace('%', "\\%")
                    .replace('_', "\\_")
            );
            match field {
                "subject"=>{clauses.push("m.subject LIKE ? ESCAPE '\\'".into());values.push(pattern.into());},
                "from"=>{clauses.push("a.address LIKE ? ESCAPE '\\'".into());values.push(pattern.into());},
                "to"|"cc"|"bcc"=>{clauses.push("EXISTS(SELECT 1 FROM recipients r JOIN email_addresses e USING(address_pk) WHERE r.message_pk=m.message_pk AND r.role=? AND e.address LIKE ? ESCAPE '\\')".into());values.push(field.to_string().into());values.push(pattern.into());},
                "any"=>{clauses.push("(a.address LIKE ? ESCAPE '\\' OR EXISTS(SELECT 1 FROM recipients r JOIN email_addresses e USING(address_pk) WHERE r.message_pk=m.message_pk AND e.address LIKE ? ESCAPE '\\'))".into());values.push(pattern.clone().into());values.push(pattern.into());},
                _=>bail!("The Rust experiment supports words, phrases, subject:, from:, to:, cc:, bcc:, and any:. {field}: is not yet supported."),
            }
            terms.push(value.to_string());
        } else {
            fulltext.push(format!("\"{}\"", token.replace('"', "\"\"")));
            terms.push(token);
        }
    }
    if !fulltext.is_empty() {
        clauses.push(
            "m.sha256 IN(SELECT sha256 FROM search.message_fts WHERE message_fts MATCH ?)".into(),
        );
        values.push(fulltext.join(" AND ").into());
    }
    Ok((clauses, values, terms))
}

pub const SCRIPT: &str = include_str!("../bridge.js");
pub fn asset(path: &str) -> Option<(&'static str, &'static [u8])> {
    Some(match path {
        "/" | "/index.html" => ("text/html", include_bytes!("../../../gui/index.html")),
        "/app.js" => ("text/javascript", include_bytes!("../../../gui/app.js")),
        "/style.css" => ("text/css", include_bytes!("../../../gui/style.css")),
        "/processing.js" => (
            "text/javascript",
            include_bytes!("../../../gui/processing.js"),
        ),
        "/vendor/tabulator/dist/css/tabulator.min.css" => (
            "text/css",
            include_bytes!("../../../gui/vendor/tabulator/dist/css/tabulator.min.css"),
        ),
        "/vendor/tabulator/dist/js/tabulator.min.js" => (
            "text/javascript",
            include_bytes!("../../../gui/vendor/tabulator/dist/js/tabulator.min.js"),
        ),
        _ => return None,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn incremental_search_returns_bounded_ordered_complete_batches() {
        // Broad FTS matches must return before the full result set is visited.
        // Sparse matches must continue through empty batches; ties cannot duplicate.
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("bulk");
        crate::demo::create(&path).unwrap();
        let db = rusqlite::Connection::open(path.join("archive.sqlite3")).unwrap();
        db.execute_batch("WITH RECURSIVE seq(x) AS(VALUES(4) UNION ALL SELECT x+1 FROM seq WHERE x<5000) INSERT INTO messages SELECT x,printf('bulk%d',x),printf('%064d',x),1,printf('Subject %05d',x),'2025-01-01','header','normal' FROM seq;").unwrap();
        drop(db);
        let mut db = rusqlite::Connection::open(path.join("search.sqlite3")).unwrap();
        let transaction = db.transaction().unwrap();
        for id in 4..=5000 {
            let digest = format!("{id:064}");
            let content = if id == 4 { "simson needle" } else { "simson" };
            transaction
                .execute(
                    "INSERT INTO message_fts(sha256,content) VALUES(?1,?2)",
                    rusqlite::params![digest, content],
                )
                .unwrap();
            transaction.execute("INSERT INTO message_metadata(sha256,message_fts_rowid,attachment_count,preview) VALUES(?1,?2,0,'')",rusqlite::params![digest,transaction.last_insert_rowid()]).unwrap();
        }
        transaction.commit().unwrap();
        drop(db);
        let mut bridge = Bridge::open(&path).unwrap();
        for sort in ["date", "subject", "sender"] {
            for direction in ["ascending", "descending"] {
                let mut cursor = Value::Null;
                let mut ids = Vec::new();
                let mut batches = 0;
                loop {
                    let batch = bridge
                        .call(
                            "search_batch",
                            &[json!("simson"), cursor, json!(sort), json!(direction)],
                        )
                        .unwrap();
                    assert!(batch["scanned"].as_u64().unwrap() <= 512);
                    if batches == 0 {
                        assert_eq!(batch["has_more"], true);
                        assert!(!batch["results"].as_array().unwrap().is_empty());
                    }
                    ids.extend(
                        batch["results"]
                            .as_array()
                            .unwrap()
                            .iter()
                            .map(|r| r["message_pk"].as_i64().unwrap()),
                    );
                    batches += 1;
                    if batch["has_more"] == false {
                        break;
                    }
                    cursor = batch["cursor"].clone();
                    // Selection/status work can run between continuation batches.
                    assert_eq!(bridge.call("status", &[]).unwrap()["message_count"], 5000);
                }
                let mut expected: Vec<i64> = (4..=5000).collect();
                if direction == "descending" {
                    expected.reverse();
                }
                assert_eq!(ids, expected, "{sort} {direction}");
                assert!(batches > 1);
                let started = bridge
                    .call(
                        "search_start",
                        &[json!("simson"), json!(sort), json!(direction)],
                    )
                    .unwrap();
                let generation = started["generation"].clone();
                let mut stages = Vec::new();
                let deadline = Instant::now() + Duration::from_secs(10);
                loop {
                    let status = bridge
                        .call("search_status", std::slice::from_ref(&generation))
                        .unwrap();
                    assert!(status["error"].is_null(), "{status}");
                    let window = status["window"].as_u64().unwrap();
                    if window > stages.len() as u64 {
                        stages.push(window);
                        assert!(status["count"].as_u64().unwrap() <= 1024);
                        // Selection works while the search owns its separate connection.
                        assert_eq!(
                            bridge.call("message", &[json!(1)]).unwrap()["message_pk"],
                            1
                        );
                        bridge
                            .call("search_advance", &[generation.clone(), json!(window)])
                            .unwrap();
                    }
                    if status["complete"] == true {
                        break;
                    }
                    assert!(Instant::now() < deadline);
                    std::thread::sleep(Duration::from_millis(1));
                }
                assert_eq!(stages, [1, 2]);
                let mut complete_ids = Vec::new();
                loop {
                    let page = bridge
                        .call(
                            "search_page",
                            &[
                                generation.clone(),
                                json!(complete_ids.len()),
                                json!(if complete_ids.is_empty() { 17 } else { 512 }),
                            ],
                        )
                        .unwrap();
                    let rows = page["results"].as_array().unwrap();
                    assert!(rows.len() <= 512);
                    if complete_ids.is_empty() {
                        assert_eq!(rows.len(), 17);
                    }
                    if rows.is_empty() {
                        break;
                    }
                    complete_ids.extend(rows.iter().map(|r| r["message_pk"].as_i64().unwrap()));
                }
                assert_eq!(complete_ids, expected, "background {sort} {direction}");
            }
        }
        let first = bridge
            .call("search_batch", &[json!("needle"), Value::Null])
            .unwrap();
        assert!(first["results"].as_array().unwrap().is_empty());
        assert_eq!(first["has_more"], true);
        let old = bridge.call("search_start", &[json!("simson")]).unwrap()["generation"].clone();
        let new = bridge.call("search_start", &[json!("needle")]).unwrap()["generation"].clone();
        assert_eq!(
            bridge
                .call("search_status", std::slice::from_ref(&old))
                .unwrap()["stale"],
            true
        );
        assert_eq!(
            bridge.call("search_page", &[old, json!(0)]).unwrap()["stale"],
            true
        );
        let deadline = Instant::now() + Duration::from_secs(10);
        loop {
            let status = bridge
                .call("search_status", std::slice::from_ref(&new))
                .unwrap();
            assert!(status["error"].is_null(), "{status}");
            if status["complete"] == true {
                assert_eq!(status["count"], 1);
                break;
            }
            if status["window"].as_u64().unwrap() > 0 {
                // Both windows are empty; the comprehensive query must still run.
                assert_eq!(status["count"], 0);
                bridge
                    .call("search_advance", &[new.clone(), status["window"].clone()])
                    .unwrap();
            }
            assert!(Instant::now() < deadline);
            std::thread::sleep(Duration::from_millis(1));
        }
        assert_eq!(
            bridge
                .call("search_page", &[new.clone(), json!(0)])
                .unwrap()["results"][0]["message_pk"],
            4
        );
        assert!(bridge.call("search_start", &[json!("bad:query")]).is_err());
        assert_eq!(bridge.call("search_status", &[new]).unwrap()["stale"], true);
    }

    #[test]
    fn cancellation_interrupts_running_sql_and_leaves_reader_usable() {
        // New queries must interrupt real SQL, not merely hide its eventual reply.
        use std::sync::{
            atomic::{AtomicU64, Ordering},
            mpsc, Arc,
        };
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("cancel");
        crate::demo::create(&path).unwrap();
        let generation = Arc::new(AtomicU64::new(1));
        let worker_generation = Arc::clone(&generation);
        let (tx, rx) = mpsc::channel();
        let reader = std::thread::spawn(move || {
            let mut bridge = Bridge::open(&path).unwrap();
            bridge.cancellation = Some((worker_generation, 1));
            bridge.search_guard(Duration::from_secs(120));
            tx.send(()).unwrap();
            let result = bridge.archive.db.query_row("WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<1000000000) SELECT sum(x) FROM n", [], |r| r.get::<_,i64>(0));
            assert_eq!(
                result.unwrap_err().sqlite_error_code(),
                Some(rusqlite::ErrorCode::OperationInterrupted)
            );
            assert_eq!(
                bridge.call("message", &[json!(1)]).unwrap()["message_pk"],
                1
            );
        });
        rx.recv_timeout(Duration::from_secs(5)).unwrap();
        std::thread::sleep(Duration::from_millis(20));
        let start = Instant::now();
        generation.store(2, Ordering::SeqCst);
        reader.join().unwrap();
        assert!(start.elapsed() < Duration::from_secs(2));
    }

    #[test]
    fn frontend_contract_search_sort_parts_and_errors() {
        // Existing UI contract: selectors, sorting, pagination, verified selection.
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("demo");
        crate::demo::create(&path).unwrap();
        let mut bridge = Bridge::open(&path).unwrap();
        assert_eq!(bridge.call("status", &[]).unwrap()["message_count"], 3);
        let page = bridge
            .call(
                "search",
                &[
                    json!("observatory"),
                    json!(0),
                    json!("subject"),
                    json!("ascending"),
                    json!(false),
                    json!([]),
                    json!(1),
                ],
            )
            .unwrap();
        assert_eq!(page["results"][0]["subject"], "Café notes");
        assert_eq!(page["has_more"], true);
        let page = bridge
            .call(
                "search",
                &[json!(
                    "from:alice@example.test subject:\"Observatory planning\""
                )],
            )
            .unwrap();
        assert_eq!(page["results"].as_array().unwrap().len(), 1);
        let view = bridge.call("message", &[json!(1)]).unwrap();
        assert_eq!(view["preferred_part_id"], 0);
        let part = bridge
            .call("part", &[json!(1), json!(0), json!(false)])
            .unwrap();
        assert!(part["content"].as_str().unwrap().contains("From the hill"));
        assert!(bridge.call("search", &[json!("date:2024-01-01")]).is_err());
        assert!(bridge.call("search", &[json!("\"unfinished")]).is_err());
        assert!(bridge.call("save_message", &[json!(1)]).is_err());
        assert!(bridge.call("part", &[json!(1), json!(99)]).is_err());
        // A failed request must not poison the next request on the same connection.
        assert_eq!(bridge.call("status", &[]).unwrap()["message_count"], 3);
    }
}
