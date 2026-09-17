// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

//! Requirements: pst/README.md — real HTTP, cache verification and safe bounded extraction.
use super::*;
use std::{
    io::Cursor,
    net::TcpListener,
    sync::{
        atomic::{AtomicBool, AtomicUsize, Ordering},
        Arc,
    },
    thread,
};

struct Server {
    url: String,
    stop: Arc<AtomicBool>,
    hits: Arc<AtomicUsize>,
    worker: Option<thread::JoinHandle<()>>,
}
impl Server {
    /// Start a loopback HTTP server serving fixture bytes and deliberate failure responses.
    fn new(files: BTreeMap<String, Vec<u8>>) -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        listener.set_nonblocking(true).unwrap();
        let url = format!("http://{}", listener.local_addr().unwrap());
        let stop = Arc::new(AtomicBool::new(false));
        let quit = stop.clone();
        let hits = Arc::new(AtomicUsize::new(0));
        let counter = hits.clone();
        let worker = thread::spawn(move || {
            while !quit.load(Ordering::Relaxed) {
                let Ok((mut socket, _)) = listener.accept() else {
                    thread::sleep(Duration::from_millis(5));
                    continue;
                };
                socket
                    .set_read_timeout(Some(Duration::from_secs(5)))
                    .unwrap();
                let mut input = Vec::new();
                let mut byte = [0];
                while !input.ends_with(b"\r\n\r\n") {
                    if socket.read(&mut byte).unwrap_or(0) == 0 {
                        break;
                    }
                    input.push(byte[0]);
                }
                let request = String::from_utf8_lossy(&input);
                let path = request.split_whitespace().nth(1).unwrap_or("");
                counter.fetch_add(1, Ordering::Relaxed);
                if let Some(body) = files.get(path) {
                    let advertised_length = body.len()
                        + if path.ends_with("truncated.pst") {
                            100
                        } else {
                            0
                        };
                    let _ = write!(
                        socket,
                        "HTTP/1.1 200 OK\r\n\
                        Content-Length: {advertised_length}\r\nConnection: close\r\n\r\n"
                    );
                    let _ = socket.write_all(body);
                } else {
                    let _ = socket.write_all(
                        b"HTTP/1.1 404 Not Found\r\n\
                        Content-Length: 0\r\nConnection: close\r\n\r\n",
                    );
                }
            }
        });
        Self {
            url,
            stop,
            hits,
            worker: Some(worker),
        }
    }
}
impl Drop for Server {
    /// Stop and join the local server so no service survives its test.
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        self.worker.take().unwrap().join().unwrap();
    }
}

/// Return the small, checked-in PST used to verify exact downloaded bytes.
fn pst() -> Vec<u8> {
    include_bytes!("../rust/mct-importer/tests/fixtures/empty.pst").to_vec()
}

/// Configure an isolated cache and bounded downloads for a test.
fn args(root: &Path) -> Args {
    Args {
        inventory_dir: root.join("inventory"),
        output: root.join("cache"),
        scope: Scope::All,
        dry_run: false,
        limit: None,
        max_download_bytes: 16 * 1024 * 1024,
        max_expanded_bytes: 16 * 1024 * 1024,
        timeout_seconds: 10,
    }
}

/// Write a test inventory using the external JSON schema.
fn inventory(root: &Path, value: &serde_json::Value) {
    fs::create_dir_all(root.join("inventory")).unwrap();
    fs::write(
        root.join("inventory/list.json"),
        serde_json::to_vec(value).unwrap(),
    )
    .unwrap();
}

/// Read the latest acquisition report from a test cache.
fn report(root: &Path) -> Report {
    serde_json::from_reader(File::open(root.join("cache/download-report.json")).unwrap()).unwrap()
}

/// Describe a direct PST URL with its expected size and SHA-256.
fn fixture(url: &str, bytes: &[u8]) -> serde_json::Value {
    serde_json::json!({"download_url": url, "size_bytes": bytes.len(), "sha256": sha256_hex(bytes)})
}

/// Build a real ZIP containing one named member for extraction tests.
fn zip(name: &str, bytes: &[u8]) -> Vec<u8> {
    let mut writer = zip::ZipWriter::new(Cursor::new(Vec::new()));
    writer
        .start_file(name, zip::write::SimpleFileOptions::default())
        .unwrap();
    writer.write_all(bytes).unwrap();
    writer.finish().unwrap().into_inner()
}

#[test]
/// Verify exact bytes, hash-based reuse and preservation of a corrupted cached object.
fn direct_http_dedup_resume_and_corrupt_cache_preservation() {
    let dir = tempfile::tempdir().unwrap();
    let data = pst();
    let server = Server::new(BTreeMap::from([
        ("/a.pst".into(), data.clone()),
        ("/b.pst".into(), data.clone()),
    ]));
    inventory(
        dir.path(),
        &serde_json::json!({"fixtures": [fixture(&format!("{}/a.pst", server.url), &data),fixture(&format!("{}/b.pst",server.url), &data)]}),
    );
    assert!(run(args(dir.path())).unwrap());
    assert_eq!(server.hits.load(Ordering::Relaxed), 1);
    let r = report(dir.path());
    assert_eq!(r.observations.len(), 2);
    assert_eq!(r.observations[0].path, r.observations[1].path);
    assert_eq!(
        fs::read(dir.path().join("cache").join(&r.observations[0].path)).unwrap(),
        data
    );
    assert!(run(args(dir.path())).unwrap());
    assert_eq!(server.hits.load(Ordering::Relaxed), 1);
    let object = dir.path().join("cache").join(&r.observations[0].path);
    fs::write(&object, b"corrupted").unwrap();
    assert!(!run(args(dir.path())).unwrap());
    assert_eq!(fs::read(object).unwrap(), b"corrupted");
    assert_eq!(report(dir.path()).failures.len(), 2);
    assert_eq!(
        fs::read_dir(dir.path().join("cache/reports"))
            .unwrap()
            .count(),
        3
    );
}

#[test]
/// Verify that both archive readers preserve the original PST bytes.
fn zip_and_sevenz_stream_exact_pst_members() {
    let dir = tempfile::tempdir().unwrap();
    let data = pst();
    let input = dir.path().join("source.pst");
    fs::write(&input, &data).unwrap();
    let seven = dir.path().join("source.7z");
    sevenz_rust::compress_to_path(&input, &seven).unwrap();
    let server = Server::new(BTreeMap::from([
        ("/mail.zip".into(), zip("nested/mail.pst", &data)),
        ("/mail.7z".into(), fs::read(seven).unwrap()),
    ]));
    inventory(
        dir.path(),
        &serde_json::json!({"enron_packages":[{"url":format!("{}/mail.zip",server.url),"files":[{"name":"nested/mail.pst","size":data.len(),"sha256":sha256_hex(&data)}]}],"additional_documented_source":{"download_url":format!("{}/mail.7z",server.url)}}),
    );
    assert!(run(args(dir.path())).unwrap());
    let r = report(dir.path());
    assert_eq!(r.observations.len(), 2);
    for item in r.observations {
        assert_eq!(
            fs::read(dir.path().join("cache").join(item.path)).unwrap(),
            data
        );
        assert!(item.member.is_some());
    }
}

#[test]
/// A 7z member must agree with both the inventory size and digest before publication.
fn sevenz_enforces_inventory_member_integrity() {
    let data = pst();
    for (size, digest, valid) in [
        (data.len(), sha256_hex(&data), true),
        (data.len() + 1, sha256_hex(&data), false),
        (data.len(), "0".repeat(64), false),
    ] {
        let dir = tempfile::tempdir().unwrap();
        let input = dir.path().join("source.pst");
        fs::write(&input, &data).unwrap();
        let seven = dir.path().join("source.7z");
        sevenz_rust::compress_to_path(&input, &seven).unwrap();
        let server = Server::new(BTreeMap::from([(
            "/mail.7z".into(),
            fs::read(seven).unwrap(),
        )]));
        inventory(
            dir.path(),
            &serde_json::json!({"enron_packages":[{
                "url": format!("{}/mail.7z", server.url),
                "files": [{"name": "source.pst", "size": size, "sha256": digest}]
            }]}),
        );
        assert_eq!(run(args(dir.path())).unwrap(), valid);
        let result = report(dir.path());
        if valid {
            assert_eq!(result.observations.len(), 1);
            assert!(result.observations[0].expected_sha256_verified);
        } else {
            assert!(result.observations.is_empty());
            assert_eq!(
                fs::read_dir(dir.path().join("cache/objects"))
                    .unwrap()
                    .count(),
                0
            );
        }
    }
}

#[test]
/// Verify that bad downloads and unsafe or oversized members fail while later jobs proceed.
fn failures_continue_without_publishing_bad_or_oversized_files() {
    let dir = tempfile::tempdir().unwrap();
    let data = pst();
    let mut different = data.clone();
    different.push(1);
    let server = Server::new(BTreeMap::from([
        ("/a.pst".into(), different),
        ("/b.zip".into(), zip("../../escape.pst", &data)),
        ("/btruncated.pst".into(), data.clone()),
        ("/c.zip".into(), zip("mail.pst", &data)),
        ("/z.pst".into(), data.clone()),
    ]));
    inventory(
        dir.path(),
        &serde_json::json!({"fixtures":[fixture(&format!("{}/a.pst",server.url),&data),fixture(&format!("{}/b404.pst",server.url),&data),fixture(&format!("{}/btruncated.pst",server.url),&data),fixture(&format!("{}/z.pst",server.url),&data)],"enron_all_130_download_urls":[format!("{}/b.zip",server.url),format!("{}/c.zip",server.url)]}),
    );
    let mut options = args(dir.path());
    options.max_expanded_bytes = 100;
    assert!(!run(options).unwrap());
    let r = report(dir.path());
    assert_eq!(r.failures.len(), 6);
    assert!(r.observations.is_empty());
    assert!(!dir.path().join("escape.pst").exists());
    assert_eq!(
        fs::read_dir(dir.path().join("cache/objects"))
            .unwrap()
            .count(),
        0
    );
    assert!(!run(args(dir.path())).unwrap());
    let r = report(dir.path());
    assert_eq!(r.failures.len(), 4);
    assert_eq!(r.completed_urls, 2);
}

#[test]
/// Verify the supplied inventory plan and dry-run behavior without corpus downloads.
fn supplied_inventory_plan_and_dry_run_have_no_side_effects() {
    let dir = tempfile::tempdir().unwrap();
    let mut options = args(dir.path());
    options.inventory_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    options.dry_run = true;
    let jobs = plan(&options).unwrap();
    assert_eq!(jobs.len(), 217);
    assert_eq!(jobs.iter().filter(|j| j.kind == Kind::Pst).count(), 86);
    assert_eq!(jobs.iter().filter(|j| j.kind == Kind::Zip).count(), 130);
    assert_eq!(jobs.iter().filter(|j| j.kind == Kind::SevenZip).count(), 1);
    assert!(run(options).unwrap());
    assert!(!dir.path().join("cache").exists());
    assert!(kind("https://example.com/mail.pst").is_ok());
    assert!(kind("https://user:password@example.com/mail.pst").is_err());
    assert!(kind("http://example.com/mail.pst").is_err());
}
