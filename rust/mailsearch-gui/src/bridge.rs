// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
// Adapt the existing HTML interface to the Rust reader and archive service.
// The external JSON protocol matches the existing frontend's method signatures.
// A foreground worker owns message reads; a separate worker runs staged searches.
// Replies preserve request IDs so the frontend can discard stale search results.
// Native actions are gated; mutations use a supervised, lease-protected service.
// The same dispatcher runs in a native webview and in headless browser tests.
use crate::Archive;
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
#[derive(Deserialize, Serialize)]
pub struct Reply {
    pub id: u64,
    pub result: Option<Value>,
    pub error: Option<String>,
}
pub struct Bridge {
    pub(crate) archive: Archive,
    search: Option<crate::search::Search>,
    pub(crate) cancellation: Option<(std::sync::Arc<std::sync::atomic::AtomicU64>, u64)>,
    selected: Option<(i64, Vec<u8>)>,
    exports: Option<tempfile::TempDir>,
    desktop_enabled: bool,
    engine: Option<crate::engine::Engine>,
    engine_generation: u64,
}

impl Bridge {
    pub fn open(path: &Path) -> Result<Self> {
        Ok(Self {
            archive: Archive::open(path)?,
            selected: None,
            exports: None,
            desktop_enabled: false,
            engine: None,
            engine_generation: 0,
            search: None,
            cancellation: None,
        })
    }
    pub fn enable_desktop(&mut self) {
        self.desktop_enabled = true;
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
    fn engine_call(&mut self, method: &str, args: &[Value]) -> Result<Value> {
        if self.engine.is_none() {
            self.engine = Some(crate::engine::Engine::open(&self.archive.root)?);
        }
        let result = self.engine.as_mut().unwrap().call(method, args)?;
        if method == "job_status" && result["active"] == false {
            let generation = result["generation"].as_u64().unwrap_or(0);
            if generation != self.engine_generation {
                self.search = None;
                self.archive = Archive::open(&self.archive.root)?;
                self.selected = None;
                self.engine_generation = generation;
            }
        }
        Ok(result)
    }
    fn call(&mut self, method: &str, args: &[Value]) -> Result<Value> {
        if matches!(
            method,
            "prepare_import"
                | "start_import"
                | "new_archive"
                | "resume_processing"
                | "options_update"
                | "identity_update"
                | "refresh_definitions"
        ) {
            crate::engine::require_archive_writing()?;
        }
        // The previous query's progress deadline must never affect another method.
        self.archive.db.progress_handler(0, None::<fn() -> bool>);
        if matches!(
            method,
            "save_message"
                | "save_attachment"
                | "open_attachment"
                | "open_message_window"
                | "new_search_window"
                | "open_archive"
                | "open_recent"
                | "open_link"
                | "copy_source_path"
                | "copy_visible_text"
                | "copy_link"
        ) {
            ensure!(
                self.desktop_enabled,
                "This operation requires the native desktop window"
            );
        }
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
            "engine_status" => match self.engine_call("capabilities", &[]) {
                Ok(mut status) => {
                    if !crate::engine::ARCHIVE_WRITING_SUPPORTED {
                        status["write_available"] = json!(false);
                        status["write_detail"] = json!(crate::engine::WRITE_UNAVAILABLE);
                    }
                    Ok(status)
                }
                Err(error) => Ok(
                    json!({"available":false,"write_available":false,"detail":format!("{error:#}")}),
                ),
            },
            "processing_work"
            | "resume_processing"
            | "ingest_overview"
            | "history"
            | "antivirus"
            | "options_status"
            | "options_update"
            | "identity_query"
            | "identity_update"
            | "job_status"
            | "stop_import"
            | "refresh_definitions" => self.engine_call(method, args),
            "prepare_import" => {
                ensure!(
                    self.desktop_enabled,
                    "Import selection requires a native window"
                );
                if let Some(source) = rfd::FileDialog::new()
                    .set_title("Choose source mail folder (read only)")
                    .pick_folder()
                {
                    self.engine_call("import_defaults", &[json!(source)])
                } else {
                    Ok(Value::Null)
                }
            }
            "start_import" => {
                ensure!(
                    self.desktop_enabled,
                    "Import confirmation requires a native window"
                );
                self.engine_call(method, args)
            }
            "new_archive" => {
                ensure!(
                    self.desktop_enabled,
                    "Archive creation requires a native window"
                );
                if let Some(path) = rfd::FileDialog::new()
                    .set_title("Create new archive (choose an empty destination)")
                    .pick_folder()
                {
                    let mut engine = crate::engine::Engine::open(&path)?;
                    engine.call("create", &[])?;
                    crate::desktop::spawn(
                        std::process::Command::new(std::env::current_exe()?)
                            .arg("--archive")
                            .arg(path),
                    )?;
                }
                Ok(json!(true))
            }
            "saved_filter_sets" | "save_filter_set" | "rename_filter_set" | "delete_filter_set" => {
                let path = crate::browse::preferences_path()?;
                crate::browse::filters(&path, method, args)
            }
            "mailbox_tree" => {
                self.search_guard(Duration::from_secs(15));
                crate::browse::tree(
                    &self.archive.db,
                    args.first().and_then(Value::as_bool).unwrap_or(false),
                )
            }
            "suggestions" => {
                self.search_guard(Duration::from_secs(2));
                crate::browse::suggestions(
                    &self.archive.db,
                    text(args, 0, ""),
                    args.get(1).and_then(Value::as_u64).unwrap_or(20) as usize,
                )
            }
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
                    args.get(3).and_then(Value::as_bool).unwrap_or(false),
                    args.get(4).cloned().unwrap_or(json!([])),
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
                let raw = self.archive.raw_message(id)?;
                let mut result = crate::mime::describe(&raw)?;
                let (date_source,filename,date_utc,offset):(String,String,String,i64)=self.archive.db.query_row("SELECT m.date_source,g.filename,m.date_utc,l.byte_offset FROM messages m JOIN locations l USING(message_pk) JOIN mbox_generations g USING(generation_pk) WHERE message_pk=?1",[id],|r|Ok((r.get(0)?,r.get(1)?,r.get(2)?,r.get(3)?)))?;
                result["message_pk"] = json!(id);
                result["date_source"] = json!(date_source);
                if date_source == "received-median" {
                    let header = result["headers"]
                        .as_array()
                        .and_then(|headers| {
                            headers.iter().find(|h| {
                                h["name"]
                                    .as_str()
                                    .is_some_and(|v| v.eq_ignore_ascii_case("Date"))
                            })
                        })
                        .map(|h| h["value"].clone())
                        .unwrap_or(json!("(missing)"));
                    result["date_adjustment"] = json!({"date_header":header,"received_median_utc":date_utc,"archive_routing_utc":date_utc});
                }
                let mut origins=self.archive.db.prepare("SELECT parent_message_pk,parent_message_id,part_path FROM attached_origins WHERE child=? ORDER BY parent_message_id,part_path")?;
                result["attached_origins"]=json!(origins.query_map([id],|r| Ok(json!({"parent_message_pk":r.get::<_,Option<i64>>(0)?,"parent_message_id":r.get::<_,String>(1)?,"part_path":serde_json::from_str::<Value>(&r.get::<_,String>(2)?).unwrap_or(json!([]))})))?.collect::<rusqlite::Result<Vec<_>>>()?);
                result["archive_path"] = json!(format!(
                    "{}?offset={offset}",
                    self.archive.root.join("data/mbox").join(filename).display()
                ));
                result["source_locations"] = self.source_locations(id)?;
                self.selected = Some((id, raw));
                Ok(result)
            }
            "part" | "attachment" => {
                let id = number(args, 0)?;
                let part = number(args, 1)?;
                if self.selected.as_ref().map(|v| v.0) != Some(id) {
                    self.selected = Some((id, self.archive.raw_message(id)?));
                }
                let raw = &self.selected.as_ref().context("Message unavailable")?.1;
                if method == "attachment" {
                    crate::mime::attachment_content(raw, part)
                } else {
                    crate::mime::render(
                        raw,
                        part,
                        args.get(2).and_then(Value::as_bool).unwrap_or(false),
                    )
                }
            }
            "open_message_window" | "new_search_window" | "open_archive" | "open_recent" => {
                let path = if method == "open_recent" {
                    let path = std::path::PathBuf::from(
                        args.first()
                            .and_then(Value::as_str)
                            .context("Missing recent archive path")?,
                    );
                    path
                } else if method == "open_archive" {
                    let Some(path) = rfd::FileDialog::new()
                        .set_title("Open email archive")
                        .pick_folder()
                    else {
                        return Ok(json!(false));
                    };
                    path
                } else {
                    self.archive.root.clone()
                };
                let mut child = std::process::Command::new(std::env::current_exe()?);
                child.arg("--archive").arg(path);
                if method == "open_message_window" {
                    let id = number(args, 0)?;
                    self.archive.raw_message(id)?;
                    child
                        .arg("--message")
                        .arg(id.to_string())
                        .arg("--highlights")
                        .arg(serde_json::to_string(args.get(1).unwrap_or(&json!([])))?);
                }
                crate::desktop::spawn(&mut child)?;
                Ok(json!(true))
            }
            "copy_visible_text" | "copy_link" => {
                crate::desktop::copy(text(args, 0, ""))?;
                Ok(json!(true))
            }
            "copy_source_path" => {
                let locations = self.source_locations(number(args, 0)?)?;
                let path = locations
                    .get(number(args, 1)? as usize)
                    .and_then(|v| v["copy_path"].as_str())
                    .context("No local path is recorded for this source")?;
                crate::desktop::copy(path)?;
                Ok(json!(path))
            }
            "open_link" => {
                crate::desktop::open_link(text(args, 0, ""))?;
                Ok(json!(true))
            }
            "save_message" | "save_attachment" => {
                let raw = self.archive.raw_message(number(args, 0)?)?;
                let (name, bytes) = if method == "save_message" {
                    ("Message.eml".to_string(), raw)
                } else {
                    let (name, _, bytes) = crate::mime::payload(&raw, number(args, 1)?)?;
                    (name, bytes)
                };
                let Some(destination) = rfd::FileDialog::new().set_file_name(&name).save_file()
                else {
                    return Ok(Value::Null);
                };
                crate::desktop::export(&self.archive.root, &destination, &bytes)?;
                Ok(json!(destination))
            }
            "open_attachment" => {
                let raw = self.archive.raw_message(number(args, 0)?)?;
                let (name, _, bytes) = crate::mime::payload(&raw, number(args, 1)?)?;
                if !args.get(2).and_then(Value::as_bool).unwrap_or(false) {
                    return Ok(json!({"requires_confirmation":true}));
                }
                if self.exports.is_none() {
                    self.exports = Some(tempfile::tempdir()?);
                }
                let directory = tempfile::Builder::new()
                    .prefix("attachment-")
                    .tempdir_in(self.exports.as_ref().unwrap().path())?;
                let destination =
                    directory
                        .path()
                        .join(if name.is_empty() { "attachment" } else { &name });
                crate::desktop::export(&self.archive.root, &destination, &bytes)?;
                let _retained = directory.keep();
                crate::desktop::open(
                    destination
                        .to_str()
                        .context("Invalid attachment pathname")?,
                )?;
                Ok(json!({"requires_confirmation":false,"path":destination}))
            }
            _ => bail!("This operation is not yet available in the Rust desktop: {method}"),
        }
    }

    fn source_locations(&self, id: i64) -> Result<Value> {
        let mut statement=self.archive.db.prepare("SELECT v.metadata_json,s.metadata_json,s.source_path,s.path_kind,o.source_offset,o.raw_sha256,o.semantic_sha256,s.source_plugin FROM observations o JOIN source_files s USING(source_file_pk) JOIN source_volumes v USING(source_volume_pk) WHERE o.message_pk=? ORDER BY o.observation_pk")?;
        let mut locations=statement.query_map([id],|r| {
            let volume:Value=serde_json::from_str(&r.get::<_,String>(0)?).unwrap_or(Value::Null);
            let metadata:Value=serde_json::from_str(&r.get::<_,String>(1)?).unwrap_or(Value::Null);
            let path=r.get::<_,String>(2)?;
            let kind=r.get::<_,String>(3)?;
            let plugin=r.get::<_,String>(7)?;
            let cached=metadata["relationship"]["role"]=="cache";
            let preferred=plugin!="file-folder"&&!cached;
            let origin=if preferred {format!("Direct {plugin} source")}else if cached{format!("Local cache of {}",metadata["relationship"]["upstream_plugin_kind"].as_str().unwrap_or("upstream account"))}else{"Local source".into()};
            let copy=if plugin=="file-folder"&&kind!="provider"{volume["current_mount_path"].as_str().filter(|v|Path::new(v).is_absolute()).map(|mount|Path::new(mount).join(&path))}else{None};
            let display=if kind=="provider"{metadata["display_name"].as_str().unwrap_or(&path).to_string()}else if kind=="file"&&path.starts_with("Users/"){format!("/{path}")}else{path};
            Ok(json!({"volume":volume["volume_label"].as_str().or(volume["current_mount_path"].as_str()).unwrap_or("Unknown source volume"),"path":display,"offset":r.get::<_,Option<i64>>(4)?,"raw_sha256":r.get::<_,String>(5)?,"semantic_sha256":r.get::<_,Option<String>>(6)?,"origin":origin,"preferred":preferred,"copy_path":copy}))
        })?.collect::<rusqlite::Result<Vec<_>>>()?;
        locations.sort_by_key(|v| {
            (
                !v["preferred"].as_bool().unwrap_or(false),
                v["origin"].as_str().unwrap_or("").to_string(),
                v["volume"].as_str().unwrap_or("").to_string(),
                v["path"].as_str().unwrap_or("").to_string(),
            )
        });
        Ok(json!(locations))
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
            format!("WHERE m.category IN ('Archive','Sent') AND ({sort},m.message_pk) {comparison} (?,?)")
        } else {
            "WHERE m.category IN ('Archive','Sent')".into()
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
            let (mut clauses, mut values, terms) = crate::selectors::plan(
                query,
                args.get(4).and_then(Value::as_bool).unwrap_or(false),
                args.get(5),
            )?;
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
        let limit = args.get(6).and_then(Value::as_u64).unwrap_or(2000);
        let limit = if limit == 0 { 100_000 } else { limit.min(2000) };
        let (clauses, values, terms) = crate::selectors::plan(
            query,
            args.get(4).and_then(Value::as_bool).unwrap_or(false),
            args.get(5),
        )?;
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

pub const SCRIPT: &str = include_str!("../bridge.js");
pub fn native_asset(
    path: &str,
    windows: bool,
) -> Option<(&'static str, std::borrow::Cow<'static, [u8]>)> {
    let (mime, bytes) = asset(path)?;
    let (scripts, style) = match path {
        "/identity.html" => (
            &[
                "rust-panel.js",
                "matcher.js",
                "matcher-types.js",
                "identity.js",
            ][..],
            "matcher.css",
        ),
        "/options.html" => (&["rust-panel.js", "options.js"][..], "options.css"),
        "/ingests.html" => (&["rust-panel.js", "ingests.js"][..], "ingests.css"),
        _ => return Some((mime, std::borrow::Cow::Borrowed(bytes))),
    };
    // Opaque editor origins cannot use CSP 'self' in WKWebView. Permit only the
    // selected page's bundled files, retaining sandbox/port/native IPC isolation.
    let origin = if windows {
        "http://ect.localhost"
    } else {
        "ect://localhost"
    };
    let scripts = scripts
        .iter()
        .map(|file| format!("{origin}/{file}"))
        .collect::<Vec<_>>()
        .join(" ");
    let html = std::str::from_utf8(bytes)
        .expect("Embedded editor HTML is UTF-8")
        .replace("default-src 'self'", "default-src 'none'")
        .replace(
            "script-src 'self' 'unsafe-eval'",
            &format!("script-src {scripts}"),
        )
        .replace("style-src 'self'", &format!("style-src {origin}/{style}"))
        .replace("connect-src 'self'", "connect-src 'none'");
    Some((mime, std::borrow::Cow::Owned(html.into_bytes())))
}
pub fn asset(path: &str) -> Option<(&'static str, &'static [u8])> {
    Some(match path {
        "/" | "/index.html" => ("text/html", include_bytes!("../../../gui/index.html")),
        "/opening.html" => ("text/html", include_bytes!("../opening.html")),
        "/opening.js" => ("text/javascript", include_bytes!("../opening.js")),
        "/identity.html" => ("text/html", include_bytes!("../../../gui/identity.html")),
        "/identity.js" => (
            "text/javascript",
            include_bytes!("../../../gui/identity.js"),
        ),
        "/matcher.css" => ("text/css", include_bytes!("../../../gui/matcher.css")),
        "/matcher.js" => ("text/javascript", include_bytes!("../../../gui/matcher.js")),
        "/matcher-types.js" => (
            "text/javascript",
            include_bytes!("../../../gui/matcher-types.js"),
        ),
        "/options.html" => ("text/html", include_bytes!("../../../gui/options.html")),
        "/options.css" => ("text/css", include_bytes!("../../../gui/options.css")),
        "/rust-panel.js" => (
            "text/javascript",
            include_bytes!("../../../gui/rust-panel.js"),
        ),
        "/options.js" => ("text/javascript", include_bytes!("../../../gui/options.js")),
        "/ingests.html" => ("text/html", include_bytes!("../../../gui/ingests.html")),
        "/ingests.css" => ("text/css", include_bytes!("../../../gui/ingests.css")),
        "/ingests.js" => ("text/javascript", include_bytes!("../../../gui/ingests.js")),
        "/rust-workflow.css" => ("text/css", include_bytes!("../../../gui/rust-workflow.css")),
        "/app.js" => ("text/javascript", include_bytes!("../../../gui/app.js")),
        "/style.css" => ("text/css", include_bytes!("../../../gui/style.css")),
        "/rust-shell.css" => ("text/css", include_bytes!("../shell.css")),
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
    fn small_and_empty_searches_wait_for_both_preview_acknowledgements() {
        // Two-window requirement applies even when the first scan exhausts input.
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("small");
        crate::demo::create(&path).unwrap();
        let mut bridge = Bridge::open(&path).unwrap();
        for (query, count) in [("observatory", 2), ("notpresent", 0)] {
            let generation =
                bridge.call("search_start", &[json!(query)]).unwrap()["generation"].clone();
            let deadline = Instant::now() + Duration::from_secs(5);
            for window in 1..=2 {
                loop {
                    let status = bridge
                        .call("search_status", std::slice::from_ref(&generation))
                        .unwrap();
                    assert!(status["error"].is_null(), "{status}");
                    assert_eq!(status["complete"], false);
                    if status["window"] == window {
                        assert_eq!(status["count"], count);
                        break;
                    }
                    assert!(Instant::now() < deadline);
                    std::thread::sleep(Duration::from_millis(1));
                }
                bridge
                    .call("search_advance", &[generation.clone(), json!(window)])
                    .unwrap();
            }
            loop {
                let status = bridge
                    .call("search_status", std::slice::from_ref(&generation))
                    .unwrap();
                if status["complete"] == true {
                    break;
                }
                assert!(Instant::now() < deadline);
                std::thread::sleep(Duration::from_millis(1));
            }
        }
    }
    #[cfg(windows)]
    #[test]
    fn windows_rejects_archive_writes_before_picker_or_engine_start() {
        // Windows reader contract: reject before user input, retaining catalog bytes.
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("readonly");
        crate::demo::create(&path).unwrap();
        let before = std::fs::read(path.join("archive.sqlite3")).unwrap();
        let mut bridge = Bridge::open(&path).unwrap();
        bridge.enable_desktop();
        for method in [
            "prepare_import",
            "start_import",
            "new_archive",
            "resume_processing",
            "options_update",
            "identity_update",
            "refresh_definitions",
        ] {
            assert!(bridge
                .call(method, &[])
                .unwrap_err()
                .to_string()
                .contains("Windows archive writing is not supported"));
            assert!(bridge.engine.is_none());
        }
        assert!(
            !bridge.call("search", &[json!("observatory")]).unwrap()["results"]
                .as_array()
                .unwrap()
                .is_empty()
        );
        drop(bridge);
        assert_eq!(std::fs::read(path.join("archive.sqlite3")).unwrap(), before);
    }
    #[test]
    fn quarantine_is_excluded_from_every_search_path() {
        // Ordinary reader searches expose only Archive/Sent, even if FTS contains quarantine.
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("categories");
        crate::demo::create(&path).unwrap();
        let db = rusqlite::Connection::open(path.join("archive.sqlite3")).unwrap();
        db.execute_batch("UPDATE messages SET category=CASE message_pk WHEN 1 THEN 'INFECTED' WHEN 2 THEN 'MALFORMED' ELSE 'Sent' END").unwrap();
        drop(db);
        let before = std::fs::read(path.join("archive.sqlite3")).unwrap();
        let archive = Archive::open(&path).unwrap();
        assert_eq!(
            archive
                .search("")
                .unwrap()
                .iter()
                .map(|r| r.id)
                .collect::<Vec<_>>(),
            [3]
        );
        assert!(archive.search("observatory").unwrap().is_empty());
        let mut bridge = Bridge::open(&path).unwrap();
        for query in ["", "subject:planning", "from:alice", "observatory", "roses"] {
            let expected = if matches!(query, "" | "roses") {
                vec![3]
            } else {
                vec![]
            };
            for method in ["search", "search_batch"] {
                let reply = bridge.call(method, &[json!(query)]).unwrap();
                let ids: Vec<i64> = reply["results"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|r| r["message_pk"].as_i64().unwrap())
                    .collect();
                assert_eq!(ids, expected, "{method} {query}");
            }
            let generation =
                bridge.call("search_start", &[json!(query)]).unwrap()["generation"].clone();
            let deadline = Instant::now() + Duration::from_secs(5);
            loop {
                let status = bridge
                    .call("search_status", std::slice::from_ref(&generation))
                    .unwrap();
                assert!(status["error"].is_null(), "{status}");
                if status["complete"] == true {
                    break;
                }
                bridge
                    .call(
                        "search_advance",
                        &[generation.clone(), status["window"].clone()],
                    )
                    .unwrap();
                assert!(Instant::now() < deadline);
                std::thread::sleep(Duration::from_millis(1));
            }
            let page = bridge
                .call("search_page", &[generation, json!(0), json!(512)])
                .unwrap();
            let ids: Vec<i64> = page["results"]
                .as_array()
                .unwrap()
                .iter()
                .map(|r| r["message_pk"].as_i64().unwrap())
                .collect();
            assert_eq!(ids, expected);
        }
        assert_eq!(std::fs::read(path.join("archive.sqlite3")).unwrap(), before);
    }

    #[test]
    fn incremental_search_returns_bounded_ordered_complete_batches() {
        // Broad FTS matches must return before the full result set is visited.
        // Sparse matches must continue through empty batches; ties cannot duplicate.
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("bulk");
        crate::demo::create(&path).unwrap();
        let db = rusqlite::Connection::open(path.join("archive.sqlite3")).unwrap();
        db.execute_batch("WITH RECURSIVE seq(x) AS(VALUES(4) UNION ALL SELECT x+1 FROM seq WHERE x<5002) INSERT INTO messages SELECT x,printf('bulk%d',x),printf('%064d',x),1,printf('Subject %05d',x),'2025-01-01','header',CASE x WHEN 5001 THEN 'INFECTED' WHEN 5002 THEN 'MALFORMED' ELSE 'Archive' END FROM seq;").unwrap();
        drop(db);
        let mut db = rusqlite::Connection::open(path.join("search.sqlite3")).unwrap();
        let transaction = db.transaction().unwrap();
        for id in 4..=5002 {
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
                    assert_eq!(bridge.call("status", &[]).unwrap()["message_count"], 5002);
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
        assert!(bridge
            .call("search_start", &[json!("date:not-a-date")])
            .is_err());
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
        assert!(bridge.call("search", &[json!("date:2024-01-01")]).is_ok());
        assert!(bridge.call("search", &[json!("date:2024-02-31")]).is_err());
        assert!(bridge.call("search", &[json!("\"unfinished")]).is_err());
        assert!(bridge.call("save_message", &[json!(1)]).is_err());
        assert!(bridge.call("part", &[json!(1), json!(99)]).is_err());
        // A failed request must not poison the next request on the same connection.
        assert_eq!(bridge.call("status", &[]).unwrap()["message_count"], 3);
    }
}
