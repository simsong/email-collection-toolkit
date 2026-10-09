// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
// Stream indexed matching IDs independently of reads and the native event loop.
// The first two full result batches are painted before streaming continues.
// The worker retains ordered message IDs; the foreground fetches bounded pages.
// A generation counter interrupts obsolete SQLite work and rejects stale pages.
// One replaceable pending job bounds work even during rapid typing and sorting.
// Closing signals cancellation without joining a potentially blocked reader.
use crate::bridge::Bridge;
use anyhow::{ensure, Result};
use serde_json::{json, Value};
use std::{
    path::PathBuf,
    sync::{
        atomic::{AtomicU64, Ordering},
        Arc, Condvar, Mutex,
    },
    time::{Duration, Instant},
};

struct Job {
    generation: u64,
    query: String,
    sort: String,
    direction: String,
    attachments: bool,
    selections: Value,
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
                let mut bridge: Option<Bridge> = None;
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
                    let result = (|| -> Result<()> {
                        // Cache successful connections, not transient opening
                        // errors; replacement searches must be able to retry.
                        if bridge.is_none() {
                            bridge = Some(Bridge::open(&path)?);
                        }
                        run(bridge.as_mut().unwrap(), &worker, &job)
                    })();
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
    pub fn start(
        &self,
        query: &str,
        sort: &str,
        direction: &str,
        attachments: bool,
        selections: Value,
    ) -> Result<Value> {
        // Cancel before parsing: an invalid replacement must also stop old work.
        self.cancel();
        ensure!(query.len() <= 4096, "Search is limited to 4096 bytes");
        let parsed =
            crate::query::Query::parse(query, sort, direction, attachments, Some(&selections))?;
        let terms = parsed.terms().to_vec();
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
            attachments,
            selections,
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
    run_with_budget(bridge, shared, job, Duration::from_secs(120))
}
fn run_with_budget(
    bridge: &mut Bridge,
    shared: &Shared,
    job: &Job,
    budget: Duration,
) -> Result<()> {
    bridge.cancellation = Some((Arc::clone(&shared.generation), job.generation));
    let query = crate::query::Query::parse(
        &job.query,
        &job.sort,
        &job.direction,
        job.attachments,
        Some(&job.selections),
    )?;
    let started = Instant::now();
    let paused = Arc::new(AtomicU64::new(0));
    let pause_counter = Arc::clone(&paused);
    let generation = Arc::clone(&shared.generation);
    let expected = job.generation;
    bridge.archive.db.progress_handler(
        1000,
        Some(move || {
            generation.load(Ordering::SeqCst) != expected
                || started
                    .elapsed()
                    .saturating_sub(Duration::from_nanos(pause_counter.load(Ordering::Relaxed)))
                    >= budget
        }),
    );
    let result = (|| -> Result<()> {
        let mut window = 0;
        let mut cursor = Value::Null;
        for _ in 0..2 {
            let sql = query.id_batch(Some(&cursor))?;
            let mut batch: Vec<(i64, String)> = {
                let mut statement = bridge.archive.db.prepare(&sql.sql)?;
                let rows = statement.query_map(rusqlite::params_from_iter(sql.values), |row| {
                    Ok((row.get(0)?, row.get(1)?))
                })?;
                rows.collect::<rusqlite::Result<_>>()?
            };
            // Finalize the read statement before waiting for frontend painting.
            let more = batch.len() > 512;
            batch.truncate(512);
            if let Some((id, key)) = batch.last() {
                cursor = json!({"key":key,"id":id});
            }
            if !publish(
                shared,
                job,
                batch.into_iter().map(|(id, _)| id).collect(),
                &mut window,
                more,
                &paused,
            ) || !more
            {
                return Ok(());
            }
        }
        let (sql, values) = remainder_query(job, &cursor)?;
        let mut statement = bridge.archive.db.prepare(&sql)?;
        let mut rows = statement.query(rusqlite::params_from_iter(values))?;
        let mut batch = Vec::with_capacity(512);
        while let Some(row) = rows.next()? {
            batch.push(row.get::<_, i64>(0)?);
            if batch.len() == 512
                && !publish(
                    shared,
                    job,
                    std::mem::take(&mut batch),
                    &mut window,
                    true,
                    &paused,
                )
            {
                return Ok(());
            }
        }
        publish(shared, job, batch, &mut window, false, &paused);
        Ok(())
    })();
    bridge.archive.db.progress_handler(0, None::<fn() -> bool>);
    result.map_err(|error| {
        anyhow::anyhow!("Search interrupted or failed (120-second safety limit): {error}")
    })
}
fn publish(
    shared: &Shared,
    job: &Job,
    ids: Vec<i64>,
    window: &mut u8,
    full: bool,
    paused: &AtomicU64,
) -> bool {
    let mut state = shared.state.lock().unwrap();
    if state.generation != job.generation || state.stop {
        return false;
    }
    state.ids.extend(ids);
    if full && *window < 2 {
        *window += 1;
        state.window = *window;
        let started = Instant::now();
        while state.generation == job.generation && state.acknowledged < *window && !state.stop {
            state = shared.wake.wait(state).unwrap();
        }
        paused.fetch_add(
            u64::try_from(started.elapsed().as_nanos()).unwrap_or(u64::MAX),
            Ordering::Relaxed,
        );
    }
    state.generation == job.generation && !state.stop
}
fn remainder_query(job: &Job, cursor: &Value) -> Result<(String, Vec<rusqlite::types::Value>)> {
    let query = crate::query::Query::parse(
        &job.query,
        &job.sort,
        &job.direction,
        job.attachments,
        Some(&job.selections),
    )?;
    let statement = query.ids(Some(cursor), None)?;
    Ok((statement.sql, statement.values))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn painting_waits_do_not_consume_the_sql_safety_budget() {
        // Responsiveness contract: time waiting for UI painting is not SQLite work.
        let work = tempfile::tempdir().unwrap();
        let path = work.path().join("archive");
        let catalog = path.join("archive.sqlite3");
        crate::demo::create(&path).unwrap();
        let db = rusqlite::Connection::open(path.join("archive.sqlite3")).unwrap();
        db.execute_batch("WITH RECURSIVE n(x) AS(VALUES(4) UNION ALL SELECT x+1 FROM n WHERE x<3000) INSERT INTO messages SELECT x,printf('bulk%d',x),printf('%064d',x),1,'Paint fixture','2025-01-01','header','Archive' FROM n;").unwrap();
        drop(db);
        let shared = Arc::new(Shared {
            state: Mutex::new(State {
                generation: 1,
                ..State::default()
            }),
            wake: Condvar::new(),
            generation: Arc::new(AtomicU64::new(1)),
        });
        let worker = Arc::clone(&shared);
        let thread = std::thread::spawn(move || {
            let mut bridge = Bridge::open(&path).unwrap();
            let job = Job {
                generation: 1,
                query: String::new(),
                sort: "date".into(),
                direction: "descending".into(),
                attachments: false,
                selections: json!([]),
            };
            run_with_budget(&mut bridge, &worker, &job, Duration::from_millis(250))
        });
        for window in 1..=2 {
            let deadline = Instant::now() + Duration::from_secs(5);
            loop {
                let state = shared.state.lock().unwrap();
                if state.window == window {
                    break;
                }
                drop(state);
                assert!(
                    !thread.is_finished(),
                    "Search failed before publishing a matching batch"
                );
                assert!(Instant::now() < deadline);
                std::thread::sleep(Duration::from_millis(1));
            }
            // A real writer must commit while the UI has not acknowledged this batch.
            let writer = rusqlite::Connection::open(&catalog).unwrap();
            writer.busy_timeout(Duration::from_millis(100)).unwrap();
            writer.execute_batch("BEGIN IMMEDIATE; UPDATE messages SET date_source='paint writer' WHERE message_pk=1; COMMIT;").unwrap();
            drop(writer);
            std::thread::sleep(Duration::from_millis(500));
            shared.state.lock().unwrap().acknowledged = window;
            shared.wake.notify_one();
        }
        thread.join().unwrap().unwrap();
        assert_eq!(shared.state.lock().unwrap().ids.len(), 3000);
    }
    #[test]
    fn sparse_fts_search_visits_matches_instead_of_the_whole_catalog() {
        // Sparse-search requirement: complete ordered results scale with FTS hits.
        let work = tempfile::tempdir().unwrap();
        let root = work.path().join("archive");
        crate::demo::create(&root).unwrap();
        let db = rusqlite::Connection::open(root.join("archive.sqlite3")).unwrap();
        db.execute_batch("WITH RECURSIVE n(x) AS(VALUES(4) UNION ALL SELECT x+1 FROM n WHERE x<50003) INSERT INTO messages SELECT x,printf('bulk%d',x),printf('%064d',x),1,'Sparse fixture','2025-01-01','header','Archive' FROM n;").unwrap();
        let mut fts = rusqlite::Connection::open(root.join("search.sqlite3")).unwrap();
        let transaction = fts.transaction().unwrap();
        for id in 4..514 {
            transaction
                .execute(
                    "INSERT INTO message_fts(sha256,content) VALUES(?1,'mushrooms')",
                    [format!("{id:064}")],
                )
                .unwrap();
        }
        // Duplicate FTS observations cannot duplicate a canonical catalog result.
        transaction
            .execute(
                "INSERT INTO message_fts(sha256,content) VALUES(?1,'mushrooms')",
                [format!("{:064}", 4)],
            )
            .unwrap();
        transaction.commit().unwrap();
        drop(db);
        let bridge = Bridge::open(&root).unwrap();
        let job = Job {
            generation: 1,
            query: "mushrooms".into(),
            sort: "date".into(),
            direction: "descending".into(),
            attachments: false,
            selections: json!([]),
        };
        let (sql, values) = remainder_query(&job, &json!({"key":"2025-01-01","id":50004})).unwrap();
        let ticks = Arc::new(AtomicU64::new(0));
        let counter = Arc::clone(&ticks);
        bridge.archive.db.progress_handler(
            100,
            Some(move || {
                counter.fetch_add(1, Ordering::Relaxed);
                false
            }),
        );
        let ids: Vec<i64> = bridge
            .archive
            .db
            .prepare(&sql)
            .unwrap()
            .query_map(rusqlite::params_from_iter(values), |row| row.get(0))
            .unwrap()
            .collect::<rusqlite::Result<_>>()
            .unwrap();
        assert_eq!(ids, (4..514).rev().collect::<Vec<_>>());
        assert!(
            ticks.load(Ordering::Relaxed) < 1000,
            "Sparse lookup scanned the catalog: {} VM instructions",
            ticks.load(Ordering::Relaxed) * 100
        );
    }
}
