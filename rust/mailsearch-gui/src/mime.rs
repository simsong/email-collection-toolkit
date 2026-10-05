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
use serde_json::{json, Value};
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
    let cleaned = name
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
        .collect::<String>()
        .trim_matches('.')
        .to_string();
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
fn text(part: &ParsedMail<'_>) -> String {
    part.get_body().unwrap_or_else(|_| {
        String::from_utf8_lossy(part.get_body_raw().as_deref().unwrap_or(part.raw_bytes))
            .into_owned()
    })
}
fn decode(raw: &[u8], mail: &ParsedMail<'_>) -> String {
    if let Ok(text) = std::str::from_utf8(raw) {
        return text.to_owned();
    }
    if let Some(encoding) = encoding_rs::Encoding::for_label(mail.ctype.charset.as_bytes()) {
        return encoding.decode(raw).0.into_owned();
    }
    String::from_utf8_lossy(raw).into_owned()
}
fn header_value(header: &mailparse::MailHeader<'_>, mail: &ParsedMail<'_>) -> String {
    let bytes = header.get_value_raw();
    if !bytes.is_ascii() && std::str::from_utf8(bytes).is_err() {
        decode(bytes, mail)
    } else {
        header.get_value()
    }
}
fn legacy_html(mail: &ParsedMail<'_>) -> Option<String> {
    let content = text(mail);
    let trimmed = content.trim();
    if trimmed.to_lowercase().starts_with("<x-html>")
        && trimmed.to_lowercase().ends_with("</x-html>")
    {
        Some(trimmed[8..trimmed.len() - 9].to_owned())
    } else {
        None
    }
}
pub(crate) fn describe(raw: &[u8]) -> Result<Value> {
    let mail = mailparse::parse_mail(raw)?;
    let mut list = Vec::new();
    parts(&mail, &mut list);
    let headers: Vec<_> = mail
        .headers
        .iter()
        .map(|h| json!({"name":h.get_key(),"value":header_value(h,&mail)}))
        .collect();
    let mut bodies = Vec::new();
    let mut attachments = Vec::new();
    let mut preferred = -1;
    let mut html_length = 0;
    let mut valid_body = false;
    for (id, part) in list.iter().enumerate() {
        let mime = &part.ctype.mimetype;
        if id == 0 && legacy_html(&mail).is_some() {
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
            attachments.push(json!({"part_id":id,"filename":name,"content_type":mime,"byte_length":part.get_body_raw().map(|bytes|bytes.len()).unwrap_or(part.raw_bytes.len()),"inline":part.headers.get_first_value("Content-ID").is_some(),"preview":preview,"risky":true}));
        } else if matches!(mime.as_str(), "text/plain" | "text/html") && part.subparts.is_empty() {
            bodies.push(json!({"part_id":id,"content_type":mime,"label":format!("{} — part {id}",if mime=="text/html"{"HTML"}else{"Plain Text"})}));
            let decoded = part.get_body();
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
    if legacy_html(&mail).is_some() {
        bodies
            .push(json!({"part_id":-2,"content_type":"text/html","label":"HTML — legacy x-html"}));
        preferred = -2;
    }
    bodies.push(json!({"part_id":-1,"content_type":"message/rfc822","label":"Raw Source"}));
    Ok(
        json!({"headers":headers,"body_parts":bodies,"preferred_part_id":preferred,"attachments":attachments,"attached_origins":[],"subject":mail.headers.iter().find(|h|h.get_key_ref().eq_ignore_ascii_case("subject")).map(|h|header_value(h,&mail)).unwrap_or_else(||"(no subject)".into())}),
    )
}
pub(crate) fn render(raw: &[u8], id: i64, allow_remote: bool) -> Result<Value> {
    let mail = mailparse::parse_mail(raw)?;
    if id == -1 {
        return Ok(
            json!({"part_id":id,"kind":"raw","content_type":"message/rfc822","content":crate::limited(decode(raw,&mail)),"remote_content_blocked":false}),
        );
    }
    let mut list = Vec::new();
    parts(&mail, &mut list);
    if id == -2 {
        let content = legacy_html(&mail).context("No legacy x-html body")?;
        let (content, blocked) = safe_html(&content, &list, allow_remote)?;
        return Ok(
            json!({"part_id":id,"kind":"html","content_type":"text/html","content":content,"remote_content_blocked":blocked}),
        );
    }
    let part = list
        .get(usize::try_from(id)?)
        .context("Unknown MIME part")?;
    ensure!(!attachment(part), "Select a body part for display");
    let content = text(part);
    let (kind, content, blocked) = match part.ctype.mimetype.as_str() {
        "text/plain" => ("text", crate::limited(content), false),
        "text/html" => {
            let (html, blocked) = safe_html(&content, &list, allow_remote)?;
            ("html", html, blocked)
        }
        _ => anyhow::bail!("Part is not displayable text"),
    };
    Ok(
        json!({"part_id":id,"kind":kind,"content_type":part.ctype.mimetype,"content":content,"remote_content_blocked":blocked}),
    )
}
fn safe_html(value: &str, parts: &[&ParsedMail<'_>], allow_remote: bool) -> Result<(String, bool)> {
    let mut images = HashMap::new();
    for part in parts {
        if raster(&part.ctype.mimetype) {
            if let Some(cid) = part.headers.get_first_value("Content-ID") {
                images.insert(
                    cid.trim_matches(['<', '>']).to_lowercase(),
                    format!(
                        "data:{};base64,{}",
                        part.ctype.mimetype,
                        STANDARD.encode(part.get_body_raw().unwrap_or_default())
                    ),
                );
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
                let lower = value.to_ascii_lowercase();
                if let Some(cid) = lower.strip_prefix("cid:") {
                    return images
                        .get(cid.trim_matches(['<', '>']))
                        .map(|v| Cow::Owned(v.clone()));
                }
                if lower.starts_with("https:") || lower.starts_with("http:") {
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
                .any(|s| lower.starts_with(s))
                {
                    return None;
                }
            }
            if attribute == "href"
                && !["https:", "http:", "mailto:"]
                    .iter()
                    .any(|s| value.to_ascii_lowercase().starts_with(s))
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
    Ok(json!({"filename":name,"content_type":mime,"content_base64":STANDARD.encode(bytes)}))
}
#[cfg(test)]
mod tests {
    use super::*;
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
