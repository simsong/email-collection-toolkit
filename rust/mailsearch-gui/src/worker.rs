// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
// Serialize archive access on one worker instead of sharing database locks.
// The UI submits one typed operation and polls for its completed response.
// A bounded channel rejects excess work without ever blocking the UI thread.
// Connections are created, used, and dropped entirely on the worker.
// Dropping the UI closes its channels; it never waits for a read to finish.
// This worker is read-only, so process exit has no pending archive writes.
use crate::{Archive, Message, Row};
use std::{
    path::PathBuf,
    sync::mpsc::{self, Receiver, SyncSender},
    thread,
};

pub enum Request {
    Open(PathBuf),
    Search(String),
    Select(i64),
}
pub enum Content {
    Rows(Vec<Row>),
    Message(Message),
}
pub type Reply = Result<Content, String>;

pub struct Worker {
    pub requests: SyncSender<Request>,
    pub replies: Receiver<Reply>,
}
impl Worker {
    pub fn start(wake: impl Fn() + Send + 'static) -> std::io::Result<Self> {
        let (requests, incoming) = mpsc::sync_channel(1);
        let (outgoing, replies) = mpsc::sync_channel(1);
        thread::Builder::new()
            .name("archive-reader".into())
            .spawn(move || {
                let mut archive: Option<Archive> = None;
                while let Ok(request) = incoming.recv() {
                    let result = (|| -> anyhow::Result<Content> {
                        match request {
                            Request::Open(path) => {
                                archive = None;
                                let opened = Archive::open(&path)?;
                                let rows = opened.search("")?;
                                archive = Some(opened);
                                Ok(Content::Rows(rows))
                            }
                            Request::Search(query) => Ok(Content::Rows(
                                archive
                                    .as_ref()
                                    .ok_or_else(|| anyhow::anyhow!("Open an archive first"))?
                                    .search(&query)?,
                            )),
                            Request::Select(id) => Ok(Content::Message(
                                archive
                                    .as_ref()
                                    .ok_or_else(|| anyhow::anyhow!("Open an archive first"))?
                                    .message(id)?,
                            )),
                        }
                    })()
                    .map_err(|err| format!("{err:#}"));
                    if outgoing.send(result).is_err() {
                        break;
                    }
                    wake();
                }
            })?;
        Ok(Self { requests, replies })
    }
}
