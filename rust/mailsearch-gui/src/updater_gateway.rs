// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
// Give WinSparkle a platform/channel view of the authenticated shared feed.
// A private loopback server fetches bounded HTTPS XML and verifies it in Rust.
// Only unchanged, signed Windows installer enclosures reach the native SDK.
// Channel changes affect subsequent discovery without reinitializing WinSparkle.
// Finite I/O deadlines and one worker bound resource use; Drop requests shutdown.
// Tests exercise actual HTTP sockets and signed fixture files, never installers.
use crate::{
    update_policy::Channel,
    updater_feed::{filtered_feed, MAX_FEED_BYTES},
};
use anyhow::{ensure, Context, Result};
use std::{
    io::{Read, Write},
    net::{TcpListener, TcpStream},
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
    thread,
    time::Duration,
};

enum Source {
    Https(String),
    #[cfg(test)]
    File(std::path::PathBuf),
}

impl Source {
    fn read(&self, client: &reqwest::blocking::Client) -> Result<Vec<u8>> {
        let mut bytes = Vec::new();
        match self {
            Self::Https(url) => {
                let response = client.get(url).send()?.error_for_status()?;
                ensure!(
                    response.url().scheme() == "https",
                    "Feed redirected away from HTTPS"
                );
                response
                    .take(MAX_FEED_BYTES as u64 + 1)
                    .read_to_end(&mut bytes)?;
            }
            #[cfg(test)]
            Self::File(path) => {
                std::fs::File::open(path)?
                    .take(MAX_FEED_BYTES as u64 + 1)
                    .read_to_end(&mut bytes)?;
            }
        }
        ensure!(
            bytes.len() <= MAX_FEED_BYTES,
            "Update feed exceeds size limit"
        );
        Ok(bytes)
    }
}

pub struct Gateway {
    pub url: String,
    preview: Arc<AtomicBool>,
    stop: Arc<AtomicBool>,
}

impl Gateway {
    pub fn start(feed: &str, key: &str, channel: Channel) -> Result<Self> {
        crate::updater::validate_feed(feed)?;
        Self::spawn(Source::Https(feed.into()), key.into(), channel)
    }

    fn spawn(source: Source, key: String, channel: Channel) -> Result<Self> {
        let socket = TcpListener::bind((std::net::Ipv4Addr::LOCALHOST, 0))?;
        socket.set_nonblocking(true)?;
        let url = format!("http://{}/appcast.xml", socket.local_addr()?);
        let client = reqwest::blocking::Client::builder()
            .https_only(true)
            .connect_timeout(Duration::from_secs(10))
            .timeout(Duration::from_secs(30))
            .redirect(reqwest::redirect::Policy::limited(5))
            .build()?;
        let preview = Arc::new(AtomicBool::new(channel == Channel::Preview));
        let stop = Arc::new(AtomicBool::new(false));
        let worker_preview = preview.clone();
        let worker_stop = stop.clone();
        thread::Builder::new().name("authenticated-update-feed".into()).spawn(move || {
            while !worker_stop.load(Ordering::Acquire) {
                match socket.accept() {
                    Ok((mut stream, _)) => {
                        if let Err(error) = serve(&mut stream, &source, &key, &client, &worker_preview) {
                            eprintln!("Update discovery failed: {error:#}");
                            let _ = stream.write_all(b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n");
                        }
                    }
                    Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => thread::sleep(Duration::from_millis(50)),
                    Err(_) => break,
                }
            }
        }).context("Start authenticated update discovery")?;
        Ok(Self { url, preview, stop })
    }

    pub fn configure(&self, channel: Channel) {
        self.preview
            .store(channel == Channel::Preview, Ordering::Release);
    }
}

fn serve(
    stream: &mut TcpStream,
    source: &Source,
    key: &str,
    client: &reqwest::blocking::Client,
    preview: &AtomicBool,
) -> Result<()> {
    stream.set_read_timeout(Some(Duration::from_secs(2)))?;
    stream.set_write_timeout(Some(Duration::from_secs(2)))?;
    let mut request = Vec::new();
    let mut buffer = [0; 1024];
    while !request.ends_with(b"\r\n\r\n") {
        let count = stream.read(&mut buffer)?;
        ensure!(
            count > 0 && request.len() + count <= 8192,
            "Invalid update request"
        );
        request.extend_from_slice(&buffer[..count]);
    }
    ensure!(
        request.starts_with(b"GET /appcast.xml HTTP/1."),
        "Unknown update route"
    );
    let data = source.read(client)?;
    let xml = filtered_feed(&data, key, preview.load(Ordering::Acquire))?;
    write!(stream, "HTTP/1.1 200 OK\r\nContent-Type: application/rss+xml\r\nContent-Length: {}\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n{}", xml.len(), xml)?;
    Ok(())
}

impl Drop for Gateway {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Release);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use base64::{engine::general_purpose::STANDARD, Engine};
    use ed25519_dalek::{Signer, SigningKey};

    #[test]
    fn loopback_discovery_authenticates_each_fetch_and_applies_channel_changes() {
        // Shared-update requirement: actual HTTP discovery authenticates complete
        // XML, preserves installer signatures, and never serves corrupted bytes.
        let fixture = tempfile::NamedTempFile::new().unwrap();
        assert!(Gateway::start("http://example.test/feed.xml", "", Channel::Release).is_err());
        drop(Gateway::start("https://example.test/feed.xml", "", Channel::Release).unwrap());
        let key = SigningKey::from_bytes(&[37; 32]);
        let xml = format!("<rss xmlns:sparkle=\"http://www.andymatuschak.org/xml-namespaces/sparkle\"><channel><item><guid>v1.0.0a1</guid><sparkle:channel>preview</sparkle:channel><sparkle:version>1</sparkle:version><enclosure sparkle:os=\"windows\" url=\"https://github.com/simsong/email-collection-toolkit/releases/download/v1.0.0a1/fixture.msixbundle\" sparkle:edSignature=\"{}\" length=\"1\"/></item></channel></rss>\n", STANDARD.encode([0;64]));
        let signed = format!(
            "{xml}<!-- sparkle-signatures:\nedSignature: {}\nlength: {}\n-->\n",
            STANDARD.encode(key.sign(xml.as_bytes()).to_bytes()),
            xml.len()
        );
        std::fs::write(fixture.path(), &signed).unwrap();
        let gateway = Gateway::spawn(
            Source::File(fixture.path().into()),
            STANDARD.encode(key.verifying_key().to_bytes()),
            Channel::Release,
        )
        .unwrap();
        let client = reqwest::blocking::Client::builder()
            .no_proxy()
            .timeout(Duration::from_secs(5))
            .build()
            .unwrap();
        let result = client
            .get(&gateway.url)
            .send()
            .unwrap()
            .error_for_status()
            .unwrap()
            .text()
            .unwrap();
        assert!(!result.contains("enclosure"));
        gateway.configure(Channel::Preview);
        let result = client
            .get(&gateway.url)
            .send()
            .unwrap()
            .error_for_status()
            .unwrap()
            .text()
            .unwrap();
        assert!(result.contains("fixture.msixbundle"));
        std::fs::write(
            fixture.path(),
            signed.replace("length=\"1\"", "length=\"2\""),
        )
        .unwrap();
        assert_eq!(client.get(&gateway.url).send().unwrap().status(), 503);
        assert_eq!(
            client
                .get(gateway.url.replace("appcast.xml", "other"))
                .send()
                .unwrap()
                .status(),
            503
        );
    }
}
