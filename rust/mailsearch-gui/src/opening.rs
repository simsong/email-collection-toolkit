// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
// Gate native archive opening on read-only validation and explicit SQLite recovery.
// Only SQLite's rollback-required error permits the private Python writer helper.
// Recovery owns a foreground UI job while this worker keeps the event loop free.
// There is no elapsed-time cutoff; an atomic abort interrupts waiting for the peer.
// Dropping that peer closes its owner pipe and bounds shutdown of its process group.
// Readers and recent-document updates become available only after revalidation.
use crate::{bridge::Bridge, engine::Engine};
use anyhow::{ensure, Result};
use std::{
    path::Path,
    sync::atomic::{AtomicBool, Ordering},
};

pub fn open(path: &Path, abort: &AtomicBool, recovering: impl FnOnce()) -> Result<Bridge> {
    ensure!(
        !abort.load(Ordering::Acquire),
        "Opening aborted. The archive cannot be opened."
    );
    let bridge = match Bridge::open(path) {
        Ok(bridge) => bridge,
        Err(error) => {
            let rollback = error.chain().any(|cause| {
                matches!(
                    cause.downcast_ref::<rusqlite::Error>(),
                    Some(rusqlite::Error::SqliteFailure(code, _))
                        if code.extended_code == rusqlite::ffi::SQLITE_READONLY_ROLLBACK
                )
            });
            if !rollback {
                return Err(error);
            }
            crate::engine::require_archive_writing()?;
            recovering();
            let mut engine = Engine::for_recovery(path, abort)?;
            engine.recover(abort)?;
            // Do not keep a recovered-but-unvalidated helper or publish a reader.
            drop(engine);
            Bridge::open(path)?
        }
    };
    ensure!(
        !abort.load(Ordering::Acquire),
        "Opening aborted. The archive cannot be opened."
    );
    Ok(bridge)
}
