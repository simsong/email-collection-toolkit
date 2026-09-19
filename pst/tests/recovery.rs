// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

//! Requirements: pst/README.md — OS leases, interruption cleanup and verified restart reuse.
use sha2::{Digest, Sha256};
use std::{
    fs,
    io::{Read, Write},
    net::TcpListener,
    path::Path,
    process::{Child, Command, ExitStatus, Stdio},
    sync::{
        atomic::{AtomicBool, AtomicUsize, Ordering},
        Arc,
    },
    thread,
    time::{Duration, Instant},
};

struct Process(Child);
impl Drop for Process {
    /// Reap even a stuck child when an assertion fails.
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

/// Wait with a deadline so broken cancellation fails rather than hanging CI.
fn wait_until(mut condition: impl FnMut() -> bool) {
    let deadline = Instant::now() + Duration::from_secs(10);
    while !condition() {
        assert!(Instant::now() < deadline, "recovery test timed out");
        thread::sleep(Duration::from_millis(20));
    }
}

/// Start the actual executable against an isolated inventory and cache.
fn start(root: &Path) -> Process {
    Process(
        Command::new(env!("CARGO_BIN_EXE_pst-downloader"))
            .arg("--inventory-dir")
            .arg(root.join("inventory"))
            .arg("--output")
            .arg(root.join("cache"))
            .args(["--timeout-seconds", "120"])
            .stdout(Stdio::null())
            .spawn()
            .unwrap(),
    )
}

/// Collect an exit status under the test deadline.
fn finish(process: &mut Process) -> ExitStatus {
    let mut status = None;
    wait_until(|| {
        status = process.0.try_wait().unwrap();
        status.is_some()
    });
    status.unwrap()
}

struct Server {
    url: String,
    hits: Arc<AtomicUsize>,
    stop: Arc<AtomicBool>,
    release: Arc<AtomicBool>,
    worker: Option<thread::JoinHandle<()>>,
}
impl Server {
    /// Serve one completed fixture, stall the second request, then permit a full retry.
    fn new(first: Vec<u8>, second: Vec<u8>) -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        listener.set_nonblocking(true).unwrap();
        let url = format!("http://{}", listener.local_addr().unwrap());
        let hits = Arc::new(AtomicUsize::new(0));
        let stop = Arc::new(AtomicBool::new(false));
        let counter = hits.clone();
        let quit = stop.clone();
        let release = Arc::new(AtomicBool::new(false));
        let resume = release.clone();
        let worker = thread::spawn(move || {
            while !quit.load(Ordering::Relaxed) {
                let Ok((mut socket, _)) = listener.accept() else {
                    thread::sleep(Duration::from_millis(5));
                    continue;
                };
                // Accepted sockets can inherit O_NONBLOCK on macOS/BSD.
                socket.set_nonblocking(false).unwrap();
                socket
                    .set_read_timeout(Some(Duration::from_secs(5)))
                    .unwrap();
                socket
                    .set_write_timeout(Some(Duration::from_secs(5)))
                    .unwrap();
                let mut request = Vec::new();
                let mut byte = [0];
                while !request.ends_with(b"\r\n\r\n") {
                    if socket.read_exact(&mut byte).is_err() {
                        break;
                    }
                    request.push(byte[0]);
                }
                if request.is_empty() {
                    continue;
                }
                let text = String::from_utf8_lossy(&request);
                let body = if text.starts_with("GET /a.pst ") {
                    &first
                } else {
                    &second
                };
                let hit = counter.load(Ordering::Relaxed);
                if hit == 1 {
                    // Headers plus a partial body reproduce Ctrl+C while reqwest awaits data.
                    let _ = write!(
                        socket,
                        "HTTP/1.1 200 OK\r\nContent-Length: {}\r\n\r\n",
                        body.len()
                    );
                    let _ = socket.write_all(&body[..12]);
                    counter.fetch_add(1, Ordering::Relaxed);
                    // Hold the connection open until the test has observed process exit.
                    while !resume.load(Ordering::Relaxed) && !quit.load(Ordering::Relaxed) {
                        thread::sleep(Duration::from_millis(5));
                    }
                } else {
                    let _ = write!(
                        socket,
                        "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                        body.len()
                    );
                    let _ = socket.write_all(body);
                    counter.fetch_add(1, Ordering::Relaxed);
                }
            }
        });
        Self {
            url,
            hits,
            stop,
            release,
            worker: Some(worker),
        }
    }
}
impl Drop for Server {
    /// Stop the fixture server without leaving a background service.
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        self.worker.take().unwrap().join().unwrap();
    }
}

/// Exercise recovery after real process death; completed bytes must never be downloaded again.
fn recovery(interrupt: bool) {
    let root = tempfile::tempdir().unwrap();
    let first = include_bytes!("../../rust/mct-importer/tests/fixtures/empty.pst").to_vec();
    let mut second = first.clone();
    second.push(0); // Distinct hash prevents the first object satisfying both URLs.
    let server = Server::new(first.clone(), second.clone());
    fs::create_dir(root.path().join("inventory")).unwrap();
    let fixtures: Vec<_> = [("a", &first), ("b", &second)]
        .into_iter()
        .map(|(name, data)| {
            serde_json::json!({"download_url": format!("{}/{name}.pst", server.url),
            "size_bytes": data.len(), "sha256": format!("{:x}", Sha256::digest(data))})
        })
        .collect();
    fs::write(
        root.path().join("inventory/list.json"),
        serde_json::to_vec(&serde_json::json!({"fixtures": fixtures})).unwrap(),
    )
    .unwrap();
    let cache = root.path().join("cache");
    fs::create_dir(&cache).unwrap();
    // The original existence-only lock must be taken over without manual removal.
    fs::write(cache.join(".download.lock"), b"stale legacy lock").unwrap();
    let mut first_run = start(root.path());
    wait_until(|| server.hits.load(Ordering::Relaxed) == 2);
    let lock_handle = fs::OpenOptions::new()
        .read(true)
        .write(true)
        .open(cache.join(".download.lock"))
        .unwrap();
    assert!(matches!(
        lock_handle.try_lock(),
        Err(fs::TryLockError::WouldBlock)
    ));
    drop(lock_handle);
    let mut contender = start(root.path());
    assert_eq!(finish(&mut contender).code(), Some(1));
    assert!(cache.join(".download.lock").exists());
    if interrupt {
        #[cfg(unix)]
        assert!(Command::new("kill")
            .args(["-INT", &first_run.0.id().to_string()])
            .status()
            .unwrap()
            .success());
        #[cfg(not(unix))]
        unreachable!("console Ctrl+C needs a native Windows console test");
    } else {
        first_run.0.kill().unwrap();
    }
    let status = finish(&mut first_run);
    if interrupt {
        assert_eq!(status.code(), Some(130));
        assert!(!cache.join(".download.lock").exists());
        let incomplete_folder = cache.join("downloads").join(format!(
            "{:x}",
            Sha256::digest(format!("{}/b.pst", server.url))
        ));
        assert_eq!(fs::read_dir(incomplete_folder).unwrap().count(), 0);
    } else {
        assert!(!status.success());
        assert!(cache.join(".download.lock").exists());
    }
    server.release.store(true, Ordering::Relaxed);
    let mut restarted = start(root.path());
    assert!(finish(&mut restarted).success());
    assert_eq!(server.hits.load(Ordering::Relaxed), 3);
    assert!(!cache.join(".download.lock").exists());
    for data in [first, second] {
        let object = cache
            .join("objects")
            .join(format!("{:x}.pst", Sha256::digest(&data)));
        assert_eq!(fs::read(object).unwrap(), data);
    }
}

#[test]
#[cfg(unix)]
/// SIGINT promptly cleans up and a restart fetches only the unfinished fixture.
fn ctrl_c_cleans_lock_and_resumes() {
    recovery(true);
}

#[test]
/// OS locks disappear on forced death even though the diagnostic file survives.
fn killed_process_lock_is_automatically_reclaimed() {
    recovery(false);
}
