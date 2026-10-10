// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
// Describe and render verified canonical messages for the shared message pane.
// MIME part IDs use preorder traversal, matching the existing viewer contract.
// Text decoding is derived; exports retain verified original bytes verbatim.
// Sanitized HTML is additionally confined by CSP and the frontend sandbox.
// CID raster images are embedded locally; network images need explicit consent.
// Attachment payloads are decoded only on demand and never executed here.
use anyhow::{ensure, Context, Result};
use base64::{engine::general_purpose::STANDARD, Engine};
use mailparse::{DispositionType, MailHeaderMap, ParsedMail};
use serde_json::Value;
use std::{
    borrow::Cow,
    collections::HashMap,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
};

fn parts<'a, 'b>(mail: &'a ParsedMail<'b>, result: &mut Vec<&'a ParsedMail<'b>>) {
    result.push(mail);
    for child in &mail.subparts {
        parts(child, result);
    }
}
fn attachment(part: &ParsedMail<'_>) -> bool {
    part.get_content_disposition().disposition == DispositionType::Attachment
        || part
            .get_content_disposition()
            .params
            .contains_key("filename")
        || part.ctype.params.contains_key("name")
        || part.ctype.mimetype == "message/rfc822"
        || (!part.ctype.mimetype.starts_with("text/") && part.subparts.is_empty())
}
pub(crate) fn filename(part: &ParsedMail<'_>, id: i64) -> String {
    let disposition = part.get_content_disposition();
    let name = disposition
        .params
        .get("filename")
        .or(part.ctype.params.get("name"));
    let fallback = format!("attachment-{id}");
    let mut cleaned = name
        .unwrap_or(&fallback)
        .rsplit(['/', '\\'])
        .next()
        .unwrap_or(&fallback)
        .chars()
        .map(|c| {
            if c.is_control() || "/\\:*?\"<>|".contains(c) {
                '_'
            } else {
                c
            }
        })
        .take(180)
        .collect::<String>();
    let start = cleaned.len() - cleaned.trim_start_matches('.').len();
    let end = cleaned.trim_end_matches('.').len().max(start);
    cleaned.truncate(end);
    cleaned.drain(..start);
    if cleaned.is_empty() {
        fallback
    } else {
        cleaned
    }
}
fn raster(mime: &str) -> bool {
    matches!(
        mime,
        "image/png" | "image/jpeg" | "image/gif" | "image/webp" | "image/bmp"
    )
}
fn decoded_text<'a>(part: &'a ParsedMail<'_>) -> Result<Cow<'a, str>, mailparse::MailParseError> {
    match part.get_body_encoded() {
        mailparse::body::Body::Base64(_) | mailparse::body::Body::QuotedPrintable(_) => {
            part.get_body().map(Cow::Owned)
        }
        _ => Ok(charset::Charset::for_label(part.ctype.charset.as_bytes())
            .map(|encoding| encoding.decode(raw_body(part)).0)
            .unwrap_or_else(|| charset::decode_ascii(raw_body(part)))),
    }
}
fn text<'a>(part: &'a ParsedMail<'_>) -> Cow<'a, str> {
    decoded_text(part).unwrap_or_else(|_| match part.get_body_raw() {
        Ok(bytes) => Cow::Owned(String::from_utf8_lossy(&bytes).into_owned()),
        Err(_) => String::from_utf8_lossy(part.raw_bytes),
    })
}
fn decode<'a>(raw: &'a [u8], mail: &ParsedMail<'_>) -> Cow<'a, str> {
    if let Ok(text) = std::str::from_utf8(raw) {
        return Cow::Borrowed(text);
    }
    if let Some(encoding) = encoding_rs::Encoding::for_label(mail.ctype.charset.as_bytes()) {
        return encoding.decode(raw).0;
    }
    String::from_utf8_lossy(raw)
}
fn header_value(header: &mailparse::MailHeader<'_>, mail: &ParsedMail<'_>) -> String {
    let bytes = header.get_value_raw();
    if !bytes.is_ascii() && std::str::from_utf8(bytes).is_err() {
        decode(bytes, mail).into_owned()
    } else {
        header.get_value()
    }
}
fn starts_ascii(value: &str, prefix: &str) -> bool {
    value
        .as_bytes()
        .get(..prefix.len())
        .is_some_and(|v| v.eq_ignore_ascii_case(prefix.as_bytes()))
}
pub(crate) fn legacy_html(content: &str) -> Option<&str> {
    let trimmed = content.trim();
    if starts_ascii(trimmed, "<x-html>")
        && trimmed
            .as_bytes()
            .get(trimmed.len().saturating_sub(9)..)
            .is_some_and(|v| v.eq_ignore_ascii_case(b"</x-html>"))
        && trimmed.len() >= 17
    {
        Some(&trimmed[8..trimmed.len() - 9])
    } else {
        None
    }
}
fn raw_body<'a>(part: &'a ParsedMail<'_>) -> &'a [u8] {
    match part.get_body_encoded() {
        mailparse::body::Body::Base64(body) | mailparse::body::Body::QuotedPrintable(body) => {
            body.get_raw()
        }
        mailparse::body::Body::SevenBit(body) | mailparse::body::Body::EightBit(body) => {
            body.get_raw()
        }
        mailparse::body::Body::Binary(body) => body.get_raw(),
    }
}
fn body_bytes<'a>(part: &'a ParsedMail<'_>) -> Result<Cow<'a, [u8]>, mailparse::MailParseError> {
    match part.get_body_encoded() {
        mailparse::body::Body::Base64(body) | mailparse::body::Body::QuotedPrintable(body) => {
            body.get_decoded().map(Cow::Owned)
        }
        _ => Ok(Cow::Borrowed(raw_body(part))),
    }
}
fn decoded_length(part: &ParsedMail<'_>) -> usize {
    let encoding = match part.get_body_encoded() {
        mailparse::body::Body::Base64(_) => "base64",
        mailparse::body::Body::QuotedPrintable(_) => "quoted-printable",
        _ => return raw_body(part).len(),
    };
    mime_transfer::decode(raw_body(part), encoding, false, |_| {}).unwrap_or(part.raw_bytes.len())
}
pub(crate) fn describe(raw: &[u8]) -> Result<Value> {
    let mail = mailparse::parse_mail(raw)?;
    let mut list = Vec::new();
    parts(&mail, &mut list);
    let headers: Vec<_> = mail
        .headers
        .iter()
        .map(|h| owned_json!({"name":h.get_key(),"value":header_value(h,&mail)}))
        .collect();
    let mut bodies = Vec::new();
    let mut attachments = Vec::new();
    let root_decoded = (!attachment(&mail)
        && mail.subparts.is_empty()
        && matches!(mail.ctype.mimetype.as_str(), "text/plain" | "text/html"))
    .then(|| decoded_text(&mail));
    let (legacy, root_length) = match &root_decoded {
        Some(Ok(body)) => (legacy_html(body).is_some(), None),
        Some(Err(_)) => (false, None),
        None => {
            let transfer = match mail.get_body_encoded() {
                mailparse::body::Body::Base64(_) => "base64",
                mailparse::body::Body::QuotedPrintable(_) => "quoted-printable",
                _ => "binary",
            };
            let (legacy, size) =
                crate::legacy::inspect(raw_body(&mail), transfer, &mail.ctype.charset);
            (legacy, Some(size.unwrap_or(mail.raw_bytes.len())))
        }
    };
    let mut preferred = -1;
    let mut html_length = 0;
    let mut valid_body = false;
    for (id, part) in list.iter().enumerate() {
        let mime = &part.ctype.mimetype;
        if id == 0 && legacy {
            continue;
        }
        if attachment(part) {
            let name = filename(part, id as i64);
            let preview = if raster(mime) {
                Some("image")
            } else if mime == "application/pdf" {
                Some("pdf")
            } else {
                None
            };
            attachments.push(owned_json!({"part_id":id,"filename":name,"content_type":mime.as_str(),"byte_length":if id == 0 { root_length.unwrap_or_else(|| decoded_length(part)) } else { decoded_length(part) },"inline":part.headers.get_first_value("Content-ID").is_some(),"preview":preview,"risky":true}));
        } else if matches!(mime.as_str(), "text/plain" | "text/html") && part.subparts.is_empty() {
            bodies.push(owned_json!({"part_id":id,"content_type":mime.as_str(),"label":format!("{} — part {id}",if mime=="text/html"{"HTML"}else{"Plain Text"})}));
            let decoded = if id == 0 {
                root_decoded
                    .as_ref()
                    .expect("root body classified before decoding")
                    .as_deref()
                    .map(Cow::Borrowed)
                    .map_err(|_| ())
            } else {
                decoded_text(part).map_err(|_| ())
            };
            let usable = decoded.as_ref().is_ok_and(|body| !body.trim().is_empty());
            let length = decoded.as_ref().map(|body| body.trim().len()).unwrap_or(0);
            if (usable && (!valid_body || (mime == "text/html" && length > html_length)))
                || preferred == -1
            {
                preferred = id as i64;
                valid_body = usable;
            }
            if mime == "text/html" && usable {
                html_length = html_length.max(length);
            }
        }
    }
    if legacy {
        bodies.push(
            owned_json!({"part_id":-2,"content_type":"text/html","label":"HTML — legacy x-html"}),
        );
        preferred = -2;
    }
    bodies.push(owned_json!({"part_id":-1,"content_type":"message/rfc822","label":"Raw Source"}));
    Ok(
        owned_json!({"headers":headers,"body_parts":bodies,"preferred_part_id":preferred,"attachments":attachments,"attached_origins":Vec::<Value>::new(),"subject":mail.headers.iter().find(|h|h.get_key_ref().eq_ignore_ascii_case("subject")).map(|h|header_value(h,&mail)).unwrap_or_else(||"(no subject)".into())}),
    )
}
pub(crate) fn render(raw: &[u8], id: i64, allow_remote: bool) -> Result<Value> {
    let mail = mailparse::parse_mail(raw)?;
    if id == -1 {
        return Ok(
            owned_json!({"part_id":id,"kind":"raw","content_type":"message/rfc822","content":crate::limited_view(decode(raw,&mail)),"remote_content_blocked":false}),
        );
    }
    let mut list = Vec::new();
    parts(&mail, &mut list);
    if id == -2 {
        let decoded = text(&mail);
        let content = legacy_html(&decoded).context("No legacy x-html body")?;
        let (content, blocked) = safe_html(content, &list, allow_remote)?;
        return Ok(
            owned_json!({"part_id":id,"kind":"html","content_type":"text/html","content":content,"remote_content_blocked":blocked}),
        );
    }
    let part = list
        .get(usize::try_from(id)?)
        .context("Unknown MIME part")?;
    ensure!(!attachment(part), "Select a body part for display");
    let content = text(part);
    let (kind, content, blocked) = match part.ctype.mimetype.as_str() {
        "text/plain" => ("text", crate::limited_view(content), false),
        "text/html" => {
            let (html, blocked) = safe_html(&content, &list, allow_remote)?;
            ("html", html, blocked)
        }
        _ => anyhow::bail!("Part is not displayable text"),
    };
    Ok(
        owned_json!({"part_id":id,"kind":kind,"content_type":part.ctype.mimetype.as_str(),"content":content,"remote_content_blocked":blocked}),
    )
}
fn safe_html(value: &str, parts: &[&ParsedMail<'_>], allow_remote: bool) -> Result<(String, bool)> {
    let mut images = HashMap::new();
    for part in parts {
        if raster(&part.ctype.mimetype) {
            if let Some(cid) = part.headers.get_first_value("Content-ID") {
                let mut data = format!("data:{};base64,", part.ctype.mimetype);
                STANDARD.encode_string(body_bytes(part).unwrap_or_default().as_ref(), &mut data);
                images.insert(cid.trim_matches(['<', '>']).to_lowercase(), data);
            }
        }
    }
    let blocked = Arc::new(AtomicBool::new(false));
    let flag = Arc::clone(&blocked);
    let html = ammonia::Builder::default()
        .url_schemes(
            ["http", "https", "mailto", "cid", "data"]
                .into_iter()
                .collect(),
        )
        .url_relative(ammonia::UrlRelative::Deny)
        .attribute_filter(move |tag, attribute, value| {
            if tag == "img" && attribute == "src" {
                if starts_ascii(value, "cid:") {
                    let cid = value[4..].to_ascii_lowercase();
                    return images
                        .get(cid.trim_matches(['<', '>']))
                        .map(|v| Cow::Owned(v.clone()));
                }
                if starts_ascii(value, "https:") || starts_ascii(value, "http:") {
                    if !allow_remote {
                        flag.store(true, Ordering::Relaxed);
                        return None;
                    }
                } else if ![
                    "data:image/png;",
                    "data:image/jpeg;",
                    "data:image/gif;",
                    "data:image/webp;",
                    "data:image/bmp;",
                ]
                .iter()
                .any(|s| starts_ascii(value, s))
                {
                    return None;
                }
            }
            if attribute == "href"
                && !["https:", "http:", "mailto:"]
                    .iter()
                    .any(|s| starts_ascii(value, s))
            {
                return None;
            }
            Some(Cow::Borrowed(value))
        })
        .clean(value)
        .to_string();
    let sources = if allow_remote {
        "data: http: https:"
    } else {
        "data:"
    };
    Ok((format!("<!doctype html><meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; img-src {sources}; style-src 'unsafe-inline'; font-src 'none'; form-action 'none'; base-uri 'none'\">{html}"),blocked.load(Ordering::Relaxed)))
}
pub(crate) fn payload(raw: &[u8], id: i64) -> Result<(String, String, Vec<u8>)> {
    let mail = mailparse::parse_mail(raw)?;
    let mut list = Vec::new();
    parts(&mail, &mut list);
    let part = list
        .get(usize::try_from(id)?)
        .context("Unknown MIME part")?;
    ensure!(attachment(part), "This part is not an attachment");
    Ok((
        filename(part, id),
        part.ctype.mimetype.clone(),
        part.get_body_raw()?,
    ))
}
pub(crate) fn attachment_content(raw: &[u8], id: i64) -> Result<Value> {
    let (name, mime, bytes) = payload(raw, id)?;
    Ok(owned_json!({"filename":name,"content_type":mime,"content_base64":STANDARD.encode(bytes)}))
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn inspection_borrows_text_and_counts_attachment_bytes() {
        // requirements.md: MIME display preserves charset and malformed attachment policy.
        for raw in [b"Content-Type: text/plain; charset=utf-8\r\n\r\nhello".as_slice(), b"Content-Type: multipart/mixed; boundary=x\r\n\r\npreamble\r\n--x\r\n\r\ntext\r\n--x--\r\n"] {
            let mail = mailparse::parse_mail(raw).unwrap();
            assert!(matches!(decoded_text(&mail).unwrap(), Cow::Borrowed(_)));
            assert_eq!(decoded_text(&mail).unwrap(), mail.get_body().unwrap());
        }
        for encoding in ["7bit", "8bit", "binary", "base64", "quoted-printable"] {
            for body in ["YWJj", "YQ==Yg==", "!!!!", "a=3Db\r\n", "=xx", "\r\n", ""] {
                let raw = format!("Content-Type: application/octet-stream\r\nContent-Transfer-Encoding: {encoding}\r\n\r\n{body}");
                let mail = mailparse::parse_mail(raw.as_bytes()).unwrap();
                let expected = mail
                    .get_body_raw()
                    .map(|b| b.len())
                    .unwrap_or(mail.raw_bytes.len());
                assert_eq!(decoded_length(&mail), expected, "{encoding}: {body:?}");
                assert_eq!(
                    describe(raw.as_bytes()).unwrap()["attachments"][0]["byte_length"],
                    expected
                );
            }
        }
        for content in ["<X-HTML>body</x-HTML>", " <x-html>é</x-html> "] {
            let raw = format!("Content-Type: text/plain; charset=utf-8\r\n\r\n{content}");
            assert_eq!(describe(raw.as_bytes()).unwrap()["preferred_part_id"], -2);
            assert!(render(raw.as_bytes(), -2, false).unwrap()["content"]
                .as_str()
                .unwrap()
                .contains(legacy_html(content).unwrap()));
        }
    }
    #[test]
    fn large_root_attachments_use_bounded_legacy_inspection() {
        // requirements.md: inspecting unopened attachments does not materialize payloads.
        for encoding in ["base64", "quoted-printable", "binary"] {
            let mut decoded = b"not legacy ".repeat(200000);
            decoded.push(b'x');
            let body = if encoding == "base64" {
                STANDARD.encode(&decoded).into_bytes()
            } else {
                decoded.clone()
            };
            let raw = [format!("Content-Type: application/octet-stream; charset=utf-8\r\nContent-Transfer-Encoding: {encoding}\r\n\r\n").as_bytes(), &body].concat();
            let view = describe(&raw).unwrap();
            assert_eq!(view["attachments"][0]["byte_length"], decoded.len());
            assert_eq!(view["preferred_part_id"], -1);
        }
        // A prefix mismatch must not skip later transfer errors or fallback sizing.
        let encoded = format!("{}!", STANDARD.encode(b"not legacy ".repeat(2000)));
        let raw = format!("Content-Type: application/octet-stream; charset=utf-8\r\nContent-Transfer-Encoding: base64\r\n\r\n{encoded}");
        assert_eq!(
            describe(raw.as_bytes()).unwrap()["attachments"][0]["byte_length"],
            raw.len()
        );
        let body = format!(
            "{}<X-HTML>{}</x-html>{}",
            "\u{2003}".repeat(20000),
            "é".repeat(200000),
            "\u{2003}".repeat(20000)
        );
        let raw = format!("Content-Type: multipart/mixed; boundary=x; charset=utf-8\r\n\r\n{body}\r\n--x\r\nContent-Type: text/plain\r\n\r\nsibling\r\n--x--\r\n");
        let mail = mailparse::parse_mail(raw.as_bytes()).unwrap();
        assert!(legacy_html(&mail.get_body().unwrap()).is_some());
        assert_eq!(describe(raw.as_bytes()).unwrap()["preferred_part_id"], -2);
        let raw = format!("Content-Type: application/octet-stream; charset=utf-8\r\nContent-Transfer-Encoding: base64\r\n\r\n{}", STANDARD.encode(body.as_bytes()));
        assert_eq!(describe(raw.as_bytes()).unwrap()["preferred_part_id"], -2);
    }
    #[test]
    fn raw_display_borrows_before_bounded_utf8_truncation() {
        // requirements.md: display truncation is derived; source bytes remain untouched.
        let raw = format!(
            "Content-Type: text/plain; charset=utf-8\r\n\r\n{}",
            "é".repeat(crate::MAX_DISPLAY)
        );
        let mail = mailparse::parse_mail(raw.as_bytes()).unwrap();
        assert!(matches!(decode(raw.as_bytes(), &mail), Cow::Borrowed(_)));
        let rendered = render(raw.as_bytes(), -1, false).unwrap();
        let content = rendered["content"].as_str().unwrap();
        assert!(content.len() < crate::MAX_DISPLAY + 100);
        assert!(content.ends_with("[Display truncated at 256 KiB; original message is unchanged.]"));
    }
    #[test]
    fn damaged_attachment_and_html_do_not_hide_plain_sibling() {
        // MIME preservation: malformed derived payloads cannot hide usable content.
        let raw=b"Content-Type: multipart/mixed; boundary=x\r\n\r\n--x\r\nContent-Type: text/html\r\nContent-Transfer-Encoding: base64\r\n\r\n!!!!invalid!!!!\r\n--x\r\nContent-Type: text/plain\r\n\r\nReadable sibling\r\n--x\r\nContent-Type: image/png\r\nContent-Disposition: attachment; filename=bad.png\r\nContent-Transfer-Encoding: base64\r\nContent-ID: <bad>\r\n\r\n!!!!invalid!!!!\r\n--x--\r\n";
        let view = describe(raw).unwrap();
        assert_eq!(view["preferred_part_id"], 2);
        assert!(render(raw, 2, false).unwrap()["content"]
            .as_str()
            .unwrap()
            .contains("Readable sibling"));
        assert_eq!(view["attachments"].as_array().unwrap().len(), 1);
    }
    #[test]
    fn html_is_inert_and_images_need_consent() {
        // Message-viewing requirement: no scripts, local file reads, or implicit tracking.
        let raw=b"Content-Type: text/html\r\n\r\n<script>alert(1)</script><img src=\"https://tracker.test/pixel\" onerror=\"alert(2)\"><img src=\"file:///etc/passwd\"><a href=\"javascript:alert(3)\">link</a><b>hello</b>";
        let rendered = render(raw, 0, false).unwrap();
        let html = rendered["content"].as_str().unwrap();
        assert!(rendered["remote_content_blocked"] == true);
        for bad in [
            "<script",
            "onerror",
            "file:",
            "javascript:",
            "https://tracker.test",
        ] {
            assert!(!html.contains(bad), "{html}");
        }
        assert!(html.contains("<b>hello</b>"));
        assert!(render(raw, 0, true).unwrap()["content"]
            .as_str()
            .unwrap()
            .contains("https://tracker.test"));
    }
}
