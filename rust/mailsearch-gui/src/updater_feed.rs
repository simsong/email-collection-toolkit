// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
// Authenticate full appcast bytes before selecting platform and update channels.
// Sparkle's trailing Ed25519 block authenticates XML and its exact byte length.
// The Windows adapter serves filtered authenticated items to its native SDK.
// Installer enclosures remain unchanged so WinSparkle verifies their signatures.
// Feed size, repository URLs and XML structure are bounded and checked first.
// No archive or private signing key participates in update discovery.
use anyhow::{ensure, Context, Result};
use base64::{engine::general_purpose::STANDARD, Engine};
use ed25519_dalek::{Signature, VerifyingKey};

pub const MAX_FEED_BYTES: usize = 16 * 1024 * 1024;
const NAMESPACE: &str = "http://www.andymatuschak.org/xml-namespaces/sparkle";
const MARKER: &str = "<!-- sparkle-signatures:\nedSignature: ";

pub fn authenticated_xml<'a>(data: &'a [u8], public_key: &str) -> Result<&'a str> {
    ensure!(
        data.len() <= MAX_FEED_BYTES,
        "Update feed exceeds size limit"
    );
    let text = std::str::from_utf8(data).context("Update feed is not UTF-8")?;
    let (content, block) = text
        .rsplit_once(MARKER)
        .context("Update feed has no signature")?;
    let (signature, tail) = block
        .split_once("\nlength: ")
        .context("Invalid feed signature block")?;
    let tail = tail
        .strip_suffix("\n-->\n")
        .or_else(|| tail.strip_suffix("\n-->"))
        .context("Invalid feed signature suffix")?;
    ensure!(
        tail.bytes().all(|byte| byte.is_ascii_digit()) && !tail.is_empty(),
        "Invalid feed signed length"
    );
    ensure!(
        tail.parse::<usize>()? == content.len(),
        "Feed signed length differs from XML"
    );
    let key: [u8; 32] = STANDARD
        .decode(public_key)?
        .try_into()
        .map_err(|_| anyhow::anyhow!("Invalid signing key length"))?;
    let signature = Signature::from_slice(&STANDARD.decode(signature)?)?;
    VerifyingKey::from_bytes(&key)?
        .verify_strict(content.as_bytes(), &signature)
        .context("Update feed signature verification failed")?;
    Ok(content)
}

pub fn filtered_feed(data: &[u8], public_key: &str, preview: bool) -> Result<String> {
    let text = authenticated_xml(data, public_key)?;
    let document = roxmltree::Document::parse(text).context("Invalid authenticated appcast XML")?;
    let root = document.root_element();
    ensure!(root.has_tag_name("rss"), "Update feed is not RSS");
    let channel = root
        .children()
        .find(|node| node.has_tag_name("channel"))
        .context("Missing appcast channel")?;
    let mut result = String::from("<?xml version=\"1.0\" encoding=\"UTF-8\"?><rss version=\"2.0\"");
    for namespace in channel.namespaces() {
        let name = namespace
            .name()
            .map(|name| format!(":{name}"))
            .unwrap_or_default();
        result.push_str(&format!(" xmlns{name}=\"{}\"", escape(namespace.uri())));
    }
    result.push_str("><channel><title>Email Collection Toolkit updates</title>");
    for item in channel.children().filter(|node| node.has_tag_name("item")) {
        let enclosure = item
            .children()
            .find(|node| node.has_tag_name("enclosure"))
            .context("Missing installer enclosure")?;
        let platform = enclosure.attribute((NAMESPACE, "os")).unwrap_or("macos");
        if platform == "macos" {
            continue;
        }
        ensure!(platform == "windows", "Unexpected update platform");
        let track = item
            .children()
            .find(|node| node.has_tag_name((NAMESPACE, "channel")))
            .and_then(|node| node.text());
        ensure!(
            track.is_none() || track == Some("preview"),
            "Unexpected update channel"
        );
        if !preview && track == Some("preview") {
            continue;
        }
        let url = url::Url::parse(
            enclosure
                .attribute("url")
                .context("Missing installer URL")?,
        )?;
        let tag = item
            .children()
            .find(|node| node.has_tag_name("guid"))
            .and_then(|node| node.text())
            .context("Missing update release identity")?;
        ensure!(
            tag.starts_with('v') && !tag.contains(['/', '\\', '%', '?', '#']),
            "Invalid release identity"
        );
        let prefix = format!("/simsong/email-collection-toolkit/releases/download/{tag}/");
        ensure!(
            url.scheme() == "https"
                && url.host_str() == Some("github.com")
                && url.port().is_none()
                && url.username().is_empty()
                && url.password().is_none()
                && url.query().is_none()
                && url.fragment().is_none(),
            "Untrusted installer URL"
        );
        let filename = url
            .path()
            .strip_prefix(&prefix)
            .context("Installer URL differs from release identity")?;
        ensure!(
            !filename.contains(['/', '%', '\\']) && filename.ends_with(".msixbundle"),
            "Invalid Windows installer URL"
        );
        let signature = enclosure
            .attribute((NAMESPACE, "edSignature"))
            .context("Missing installer signature")?;
        ensure!(
            STANDARD.decode(signature)?.len() == 64,
            "Invalid installer signature length"
        );
        ensure!(
            enclosure
                .attribute("length")
                .context("Missing installer length")?
                .parse::<u64>()?
                > 0,
            "Invalid installer length"
        );
        let build = item
            .children()
            .find(|node| node.has_tag_name((NAMESPACE, "version")))
            .and_then(|node| node.text())
            .context("Missing update build")?;
        ensure!(build.parse::<u64>()? > 0, "Invalid update build");
        result.push_str(&text[item.range()]);
    }
    result.push_str("</channel></rss>");
    Ok(result)
}

fn escape(text: &str) -> String {
    text.replace('&', "&amp;")
        .replace('"', "&quot;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
}

#[cfg(test)]
mod tests {
    use super::*;
    use ed25519_dalek::{Signer, SigningKey};

    fn signed(xml: &str) -> (Vec<u8>, String) {
        let key = SigningKey::from_bytes(&[19; 32]);
        let signature = STANDARD.encode(key.sign(xml.as_bytes()).to_bytes());
        (
            format!("{xml}{MARKER}{signature}\nlength: {}\n-->\n", xml.len()).into_bytes(),
            STANDARD.encode(key.verifying_key().to_bytes()),
        )
    }

    #[test]
    fn signature_covers_exact_xml_and_rejects_tampering() {
        let (data, key) = signed("<rss><channel/></rss>\n");
        assert_eq!(
            authenticated_xml(&data, &key).unwrap(),
            "<rss><channel/></rss>\n"
        );
        let mut damaged = data.clone();
        damaged[5] ^= 1;
        assert!(authenticated_xml(&damaged, &key).is_err());
        let mut extended = data;
        extended.extend_from_slice(b"extra");
        assert!(authenticated_xml(&extended, &key).is_err());
        assert!(authenticated_xml(&vec![0; MAX_FEED_BYTES + 1], &key).is_err());
    }

    #[test]
    fn windows_discovery_filters_mac_and_preview_without_rewriting_enclosures() {
        let prefix = "https://github.com/simsong/email-collection-toolkit/releases/download";
        let enclosure = format!("<enclosure url=\"{prefix}/v1.0.0/fixture.msixbundle\" length=\"123\" sparkle:os=\"windows\" sparkle:edSignature=\"{}\"/>", STANDARD.encode([0;64]));
        let stable = format!(
            "<item><guid>v1.0.0</guid><sparkle:version>100</sparkle:version>{enclosure}</item>"
        );
        let preview = stable.replace("<item>", "<item><sparkle:channel>preview</sparkle:channel>");
        let (data, key) = signed(&format!("<rss xmlns:sparkle=\"{NAMESPACE}\"><channel><item><enclosure url=\"{prefix}/v0.9.0/old.dmg\"/></item>{preview}{stable}</channel></rss>\n"));
        let release = filtered_feed(&data, &key, false).unwrap();
        assert!(release.contains(&enclosure));
        assert!(!release.contains("old.dmg"));
        assert!(!release.contains("preview"));
        let preview = filtered_feed(&data, &key, true).unwrap();
        assert_eq!(preview.matches(&enclosure).count(), 2);
    }
}
