// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
// Run staged searches independently of message reads and the native event loop.
// Two bounded catalog windows are published and acknowledged before a full query.
// The worker retains ordered message IDs; the foreground fetches bounded pages.
// A generation counter interrupts obsolete SQLite work and rejects stale pages.
// One replaceable pending job bounds work even during rapid typing and sorting.
// Closing signals cancellation without joining a potentially blocked reader.
use crate::bridge::{query_parts, Bridge};
use anyhow::{ensure, Result};
use serde_json::{json, Value};
use std::{
    path::PathBuf,
    sync::{
        atomic::{AtomicU64, Ordering},
        Arc, Condvar, Mutex,
    },
    time::Duration,
};

struct Job {
    generation: u64,
    query: String,
    sort: String,
    direction: String,
}
#[derive(Default)]
struct State {
    pending: Option<Job>,
    stop: bool,
    generation: u64,
    ids: Vec<i64>,
    window: u8,
    acknowledged: u8,
    complete: bool,
    error: Option<String>,
}
struct Shared {
    state: Mutex<State>,
    wake: Condvar,
    generation: Arc<AtomicU64>,
}
pub struct Search {
    shared: Arc<Shared>,
}
impl Search {
    pub fn new(path: PathBuf) -> Result<Self> {
        let shared = Arc::new(Shared {
            state: Mutex::new(State::default()),
            wake: Condvar::new(),
            generation: Arc::new(AtomicU64::new(0)),
        });
        let worker = Arc::clone(&shared);
        std::thread::Builder::new()
            .name("archive-search".into())
            .spawn(move || {
                let mut bridge = Bridge::open(&path).map_err(|e| format!("{e:#}"));
                loop {
                    let job = {
                        let mut state = worker.state.lock().unwrap();
                        while state.pending.is_none() && !state.stop {
                            state = worker.wake.wait(state).unwrap();
                        }
                        if state.stop {
                            return;
                        }
                        state.pending.take().unwrap()
                    };
                    let result = match &mut bridge {
                        Ok(bridge) => run(bridge, &worker, &job),
                        Err(error) => Err(anyhow::anyhow!(error.clone())),
                    };
                    let mut state = worker.state.lock().unwrap();
                    if state.generation == job.generation {
                        if let Err(error) = result {
                            state.error = Some(format!("{error:#}"));
                        }
                        state.complete = true;
                    }
                }
            })?;
        Ok(Self { shared })
    }
    pub fn start(&self, query: &str, sort: &str, direction: &str) -> Result<Value> {
        // Cancel before parsing: an invalid replacement must also stop old work.
        self.cancel();
        ensure!(query.len() <= 4096, "Search is limited to 4096 bytes");
        let (_, _, terms) = query_parts(query)?;
        ensure!(
            ["date", "subject", "sender"].contains(&sort),
            "Unknown sort field"
        );
        ensure!(
            ["ascending", "descending"].contains(&direction),
            "Unknown sort direction"
        );
        let mut state = self.shared.state.lock().unwrap();
        let generation = state.generation;
        state.pending = Some(Job {
            generation,
            query: query.into(),
            sort: sort.into(),
            direction: direction.into(),
        });
        self.shared.wake.notify_one();
        Ok(json!({"generation":generation,"highlight_terms":terms}))
    }
    pub fn cancel(&self) {
        let generation = self.shared.generation.fetch_add(1, Ordering::SeqCst) + 1;
        let mut state = self.shared.state.lock().unwrap();
        *state = State {
            generation,
            ..State::default()
        };
        self.shared.wake.notify_one();
    }
    pub fn status(&self, generation: u64) -> Value {
        let state = self.shared.state.lock().unwrap();
        if state.generation != generation {
            return json!({"stale":true});
        }
        json!({"count":state.ids.len(),"window":state.window,"complete":state.complete,"error":state.error})
    }
    pub fn advance(&self, generation: u64, window: u8) {
        let mut state = self.shared.state.lock().unwrap();
        if state.generation == generation {
            state.acknowledged = state.acknowledged.max(window.min(state.window));
            self.shared.wake.notify_one();
        }
    }
    pub fn page(&self, generation: u64, offset: usize, limit: usize) -> Option<Vec<i64>> {
        let state = self.shared.state.lock().unwrap();
        if state.generation != generation {
            return None;
        }
        Some(
            state
                .ids
                .iter()
                .skip(offset)
                .take(limit.min(512))
                .copied()
                .collect(),
        )
    }
}
impl Drop for Search {
    fn drop(&mut self) {
        self.shared.generation.fetch_add(1, Ordering::SeqCst);
        self.shared.state.lock().unwrap().stop = true;
        self.shared.wake.notify_one();
    }
}
fn run(bridge: &mut Bridge, shared: &Shared, job: &Job) -> Result<()> {
    bridge.cancellation = Some((Arc::clone(&shared.generation), job.generation));
    let mut cursor = Value::Null;
    for window in 1..=2 {
        ensure!(
            shared.generation.load(Ordering::SeqCst) == job.generation,
            "Search cancelled"
        );
        let batch = bridge.search_batch(&[
            json!(job.query),
            cursor,
            json!(job.sort),
            json!(job.direction),
        ])?;
        let mut state = shared.state.lock().unwrap();
        if state.generation != job.generation {
            return Ok(());
        }
        state.ids.extend(
            batch["results"]
                .as_array()
                .unwrap()
                .iter()
                .map(|r| r["message_pk"].as_i64().unwrap()),
        );
        state.window = window;
        if batch["has_more"] == false {
            return Ok(());
        }
        cursor = batch["cursor"].clone();
        while state.generation == job.generation && state.acknowledged < window && !state.stop {
            state = shared.wake.wait(state).unwrap();
        }
        if state.generation != job.generation || state.stop {
            return Ok(());
        }
    }
    // A single FTS-driven query completes the remaining set. Only IDs are kept;
    // display columns and previews are fetched on the independent foreground DB.
    let sort = match job.sort.as_str() {
        "subject" => "lower(m.subject)",
        "sender" => "lower(a.address)",
        _ => "m.date_utc",
    };
    let (direction, comparison) = if job.direction == "ascending" {
        ("ASC", ">")
    } else {
        ("DESC", "<")
    };
    let (mut clauses, mut values, _) = query_parts(&job.query)?;
    for clause in &mut clauses {
        if clause.starts_with("m.sha256 IN(") {
            *clause = "mm.message_fts_rowid IN(SELECT rowid FROM search.message_fts WHERE message_fts MATCH ?)".into();
        }
    }
    clauses.push(format!("({sort},m.message_pk) {comparison} (?,?)"));
    values.push(cursor["key"].as_str().unwrap().to_string().into());
    values.push(cursor["id"].as_i64().unwrap().into());
    let sql = format!("SELECT m.message_pk FROM messages m JOIN email_addresses a ON a.address_pk=m.sender_address_pk LEFT JOIN search.message_metadata mm USING(sha256) WHERE {} ORDER BY {sort} {direction},m.message_pk {direction}", clauses.join(" AND "));
    bridge.search_guard(Duration::from_secs(120));
    let result = (|| -> Result<Vec<i64>> {
        let mut statement = bridge.archive.db.prepare(&sql)?;
        let rows = statement.query_map(rusqlite::params_from_iter(values), |row| row.get(0))?;
        Ok(rows.collect::<rusqlite::Result<_>>()?)
    })();
    bridge.archive.db.progress_handler(0, None::<fn() -> bool>);
    let ids = result.map_err(|error| {
        anyhow::anyhow!(
            "Background search interrupted or failed (120-second safety limit): {error}"
        )
    })?;
    let mut state = shared.state.lock().unwrap();
    if state.generation == job.generation {
        state.ids.extend(ids);
    }
    Ok(())
}
