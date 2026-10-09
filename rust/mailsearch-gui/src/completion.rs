// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
// Complete search selectors without blocking search pages or message reads.
// A dedicated read-only connection runs the same catalog/name queries as Python.
// One replaceable pending job bounds rapid typing; generations cancel stale SQL.
// The frontend polls exact results and rejects obsolete responses before painting.
// Closing signals cancellation without waiting for SQLite on the window thread.
use crate::bridge::Bridge;
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
    limit: usize,
}
#[derive(Default)]
struct State {
    pending: Option<Job>,
    stop: bool,
    generation: u64,
    result: Option<std::result::Result<Value, String>>,
}
struct Shared {
    state: Mutex<State>,
    wake: Condvar,
    generation: Arc<AtomicU64>,
}
pub(crate) struct Completion {
    shared: Arc<Shared>,
}
impl Completion {
    pub fn new(path: PathBuf) -> Result<Self> {
        let shared = Arc::new(Shared {
            state: Mutex::new(State::default()),
            wake: Condvar::new(),
            generation: Arc::new(AtomicU64::new(0)),
        });
        let worker = Arc::clone(&shared);
        std::thread::Builder::new()
            .name("archive-completion".into())
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
                    let result = (|| -> Result<Value> {
                        // Retain only successful opens; a temporary lock must
                        // not poison every later completion in this window.
                        if bridge.is_none() {
                            bridge = Some(Bridge::open(&path)?);
                        }
                        let bridge = bridge.as_mut().unwrap();
                        bridge.cancellation =
                            Some((Arc::clone(&worker.generation), job.generation));
                        bridge.suggestions(
                            &[json!(job.query), json!(job.limit)],
                            Duration::from_secs(120),
                        )
                    })()
                    .map_err(|error| format!("{error:#}"));
                    let mut state = worker.state.lock().unwrap();
                    if state.generation == job.generation {
                        state.result = Some(result);
                    }
                }
            })?;
        Ok(Self { shared })
    }
    pub fn start(&self, query: &str, limit: usize) -> Result<Value> {
        self.cancel();
        ensure!(query.len() <= 4096, "Completion is limited to 4096 bytes");
        ensure!(
            (1..=50).contains(&limit),
            "Suggestion limit must be between 1 and 50"
        );
        let mut state = self.shared.state.lock().unwrap();
        let generation = state.generation;
        state.pending = Some(Job {
            generation,
            query: query.into(),
            limit,
        });
        self.shared.wake.notify_one();
        Ok(json!({"generation":generation}))
    }
    pub fn status(&self, generation: u64) -> Value {
        let state = self.shared.state.lock().unwrap();
        if state.generation != generation {
            return json!({"stale":true});
        }
        match &state.result {
            None => json!({"complete":false}),
            Some(Ok(result)) => json!({"complete":true,"result":result}),
            Some(Err(error)) => json!({"complete":true,"error":error}),
        }
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
}
impl Drop for Completion {
    fn drop(&mut self) {
        self.shared.generation.fetch_add(1, Ordering::SeqCst);
        self.shared.state.lock().unwrap().stop = true;
        self.shared.wake.notify_one();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Instant;

    fn finished(bridge: &mut Bridge, generation: &Value) -> Value {
        let started = Instant::now();
        loop {
            let status = bridge
                .reply(crate::bridge::Request {
                    id: 1,
                    method: "suggestions_status".into(),
                    args: vec![generation.clone()],
                })
                .result
                .unwrap();
            if status["complete"] == true {
                return status;
            }
            assert!(started.elapsed() < Duration::from_secs(5));
            std::thread::sleep(Duration::from_millis(1));
        }
    }
    #[test]
    fn blocked_completion_does_not_block_message_rendering_and_replacement() {
        // Responsiveness: real SQLite contention stays off the foreground reader.
        let work = tempfile::tempdir().unwrap();
        let root = work.path().join("archive");
        crate::demo::create(&root).unwrap();
        let mut bridge = Bridge::open(&root).unwrap();
        let call = |bridge: &mut Bridge, method: &str, args| {
            let reply = bridge.reply(crate::bridge::Request {
                id: 1,
                method: method.into(),
                args,
            });
            assert!(reply.error.is_none(), "{:?}", reply.error);
            reply.result.unwrap()
        };
        let warm = call(&mut bridge, "suggestions_start", vec![json!("alice")]);
        assert!(
            !finished(&mut bridge, &warm["generation"])["result"]["items"]
                .as_array()
                .unwrap()
                .is_empty()
        );
        let message = call(&mut bridge, "message", vec![json!(1)]);
        let lock = rusqlite::Connection::open(root.join("search.sqlite3")).unwrap();
        lock.execute_batch("BEGIN EXCLUSIVE").unwrap();
        let old = call(&mut bridge, "suggestions_start", vec![json!("alice")]);
        assert_eq!(
            call(
                &mut bridge,
                "suggestions_status",
                vec![old["generation"].clone()]
            )["complete"],
            false
        );
        let part = call(
            &mut bridge,
            "part",
            vec![json!(1), message["preferred_part_id"].clone()],
        );
        assert!(!part["content"].as_str().unwrap().is_empty());
        call(&mut bridge, "suggestions_cancel", vec![]);
        assert_eq!(
            call(
                &mut bridge,
                "suggestions_status",
                vec![old["generation"].clone()]
            )["stale"],
            true
        );
        lock.execute_batch("ROLLBACK").unwrap();
        let new = call(
            &mut bridge,
            "suggestions_start",
            vec![json!("subject:Rust")],
        );
        let result = finished(&mut bridge, &new["generation"]);
        assert!(result["error"].is_null(), "{result}");
        assert_eq!(
            result["result"],
            crate::browse::suggestions(&bridge.archive.db, "subject:Rust", 20).unwrap()
        );
    }
}
