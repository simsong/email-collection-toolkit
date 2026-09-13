//! Read-only Microsoft outlook-pst adapter. Requirements: doc/PST_IMPORTER.md.
use anyhow::{bail, ensure, Context, Result};
use base64::{engine::general_purpose::STANDARD, Engine};
use chrono::{DateTime, Utc};
use outlook_pst::{
    ltp::prop_context::PropertyValue,
    messaging::{
        attachment::{AnsiAttachment, Attachment, AttachmentData, UnicodeAttachment},
        message::{AnsiMessage, Message, UnicodeMessage},
        store::{AnsiStore, Store, UnicodeStore},
    },
    ndb::node_id::NodeId,
    AnsiPstFile, UnicodePstFile,
};
use sha2::{Digest, Sha256};
use std::{
    borrow::Cow,
    collections::HashSet,
    fs::File,
    io::{self, BufReader, Read, Seek, Write},
    path::Path,
    rc::Rc,
};
use url::Url;

const BODY: u16 = 0x1000;
const HTML: u16 = 0x1013;
const RTF_COMPRESSED: u16 = 0x1009;
const TRANSPORT_HEADERS: u16 = 0x007D;
const SUBJECT: u16 = 0x0037;
const SENDER_SMTP: u16 = 0x5D01;
const SENDER_EMAIL: u16 = 0x0C1F;
const MESSAGE_ID: u16 = 0x1035;
const SUBMIT_TIME: u16 = 0x0039;
const DELIVERY_TIME: u16 = 0x0E06;
const INTERNET_CPID: u16 = 0x3FDE;
const ATTACH_LONG_NAME: u16 = 0x3707;
const ATTACH_SHORT_NAME: u16 = 0x3704;
const MAX_DEPTH: usize = 64;
const MAX_RECORD: u64 = crate::DEFAULT_MAX_MESSAGE_BYTES as u64;

/// Counts describe encountered objects, never an estimate of inaccessible subtrees.
#[derive(Default, Debug)]
pub struct ImportReport {
    pub folders: u64,
    pub encountered: u64,
    pub emitted: u64,
    pub non_mail: u64,
    pub errors: u64,
}

enum PstStore {
    Unicode(Rc<UnicodeStore>),
    Ansi(Rc<AnsiStore>),
}
enum PstMessage {
    Unicode(Rc<UnicodeMessage>),
    Ansi(Rc<AnsiMessage>),
}
impl PstStore {
    fn store(&self) -> &dyn Store {
        match self {
            Self::Unicode(s) => s.as_ref(),
            Self::Ansi(s) => s.as_ref(),
        }
    }
    fn message(&self, node: u32) -> Result<PstMessage> {
        let entry = self
            .store()
            .properties()
            .make_entry_id(NodeId::from(node))?;
        Ok(match self {
            Self::Unicode(s) => PstMessage::Unicode(UnicodeMessage::read(s.clone(), &entry, None)?),
            Self::Ansi(s) => PstMessage::Ansi(AnsiMessage::read(s.clone(), &entry, None)?),
        })
    }
}
impl PstMessage {
    fn message(&self) -> &dyn Message {
        match self {
            Self::Unicode(m) => m.as_ref(),
            Self::Ansi(m) => m.as_ref(),
        }
    }
    fn attachment(&self, node: NodeId) -> Result<Rc<dyn Attachment>> {
        Ok(match self {
            Self::Unicode(m) => UnicodeAttachment::read(m.clone(), node, None)?,
            Self::Ansi(m) => AnsiAttachment::read(m.clone(), node, None)?,
        })
    }
}

fn fingerprint(file: &mut File) -> Result<String> {
    file.rewind()?;
    let mut hash = Sha256::new();
    let mut buffer = [0; 65536];
    loop {
        let size = file.read(&mut buffer)?;
        if size == 0 {
            break;
        }
        hash.update(&buffer[..size]);
    }
    file.rewind()?;
    Ok(format!("{:x}", hash.finalize()))
}

/// Only File::open + read_from are used: the crate receives no write handle.
fn open(file: &File) -> Result<PstStore> {
    let mut reader = file.try_clone()?;
    let mut header = [0; 12];
    reader.read_exact(&mut header)?;
    reader.rewind()?;
    ensure!(&header[..4] == b"!BDN", "not a PST file");
    ensure!(
        &header[8..10] == b"SM",
        "not an Outlook PST (OST is not supported)"
    );
    match u16::from_le_bytes([header[10], header[11]]) {
        14 | 15 => Ok(PstStore::Ansi(AnsiStore::read(Rc::new(
            AnsiPstFile::read_from(Box::new(reader))?,
        ))?)),
        23 => Ok(PstStore::Unicode(UnicodeStore::read(Rc::new(
            UnicodePstFile::read_from(Box::new(reader))?,
        ))?)),
        version => bail!("unsupported PST version {version}; supported: ANSI 14/15 and Unicode 23"),
    }
}

/// Traverse the IPM subtree, including its own messages and Deleted Items.
/// Search folders / associated configuration items are outside this mail scope.
pub fn import(
    path: &Path,
    output: &mut impl Write,
    diagnostics: &mut impl Write,
) -> Result<ImportReport> {
    let path = path.canonicalize().context("resolve source filename")?;
    let mut source = File::open(&path).context("open source read-only")?;
    ensure!(source.metadata()?.is_file(), "source is not a regular file");
    let original_hash = fingerprint(&mut source)?;
    writeln!(diagnostics, "pst-importer {}: outlook-pst 1.2.0; API {}; source-sha256={original_hash}; MIME reconstructed from MAPI", env!("CARGO_PKG_VERSION"), crate::API_VERSION)?;
    let mut uri = Url::from_file_path(&path)
        .map_err(|()| anyhow::anyhow!("cannot represent source filename as file URI"))?;
    let pst = open(&source)?;
    let root = pst.store().properties().ipm_sub_tree_entry_id()?;
    let mut pending = vec![(u32::from(root.node_id()), 0)];
    let mut visited = HashSet::new();
    let mut messages = HashSet::new();
    let mut report = ImportReport::default();
    while let Some((node, depth)) = pending.pop() {
        let folder_result = (|| -> Result<()> {
            ensure!(depth <= MAX_DEPTH, "folder nesting exceeds {MAX_DEPTH}");
            ensure!(visited.insert(node), "folder cycle or duplicate node");
            let entry = pst.store().properties().make_entry_id(NodeId::from(node))?;
            let folder = pst.store().open_folder(&entry)?;
            report.folders += 1;
            if let Some(table) = folder.contents_table() {
                let mut rows = 0;
                for row in table.rows_matrix() {
                    rows += 1;
                    let item = u32::from(row.id());
                    report.encountered += 1;
                    uri.set_fragment(Some(&format!("item={item}")));
                    let item_result = (|| -> Result<bool> {
                        ensure!(messages.insert(item), "duplicate message node in traversal");
                        let message = pst.message(item)?;
                        let class = message.message().properties().message_class()?;
                        if !class.eq_ignore_ascii_case("IPM.Note")
                            && !class.to_ascii_lowercase().starts_with("ipm.note.")
                        {
                            // Known PIM/configuration objects are not mail; unknown classes fail visibly.
                            if [
                                "ipm.contact",
                                "ipm.distlist",
                                "ipm.appointment",
                                "ipm.task",
                                "ipm.activity",
                                "ipm.stickynote",
                            ]
                            .iter()
                            .any(|c| {
                                class.eq_ignore_ascii_case(c)
                                    || class.to_ascii_lowercase().starts_with(&format!("{c}."))
                            }) {
                                writeln!(diagnostics, "non-mail item={item} class={class:?}")?;
                                return Ok(false);
                            }
                            bail!("unsupported message class {class:?}");
                        }
                        let mut spool = tempfile::tempfile()?;
                        {
                            let mut writer = LimitedWriter {
                                inner: &mut spool,
                                remaining: MAX_RECORD,
                            };
                            write!(writer, "From pst-importer Thu Jan  1 00:00:00 1970\nX-Imported-URI: {uri}\r\nX-Importer-Name: pst-importer\r\nX-Importer-Version: {}\r\n", env!("CARGO_PKG_VERSION"))?;
                            render(message.message(), Some(&message), item, &mut writer)?;
                            writer.write_all(b"\n")?;
                        }
                        spool.rewind()?;
                        let mut failures = Vec::new();
                        let validation = crate::validate(
                            &mut BufReader::new(&mut spool),
                            crate::DEFAULT_MAX_MESSAGE_BYTES,
                            &mut |e| failures.push(e),
                        )?;
                        ensure!(
                            validation.complete == 1 && validation.errors == 0,
                            "reconstructed record failed validation: {}",
                            failures.join("; ")
                        );
                        spool.rewind()?;
                        // A failed record has not touched stdout; successful records are streamed.
                        io::copy(&mut spool, output).context("write stdout")?;
                        Ok(true)
                    })();
                    match item_result {
                        Ok(true) => report.emitted += 1,
                        Ok(false) => report.non_mail += 1,
                        Err(error) => {
                            if error
                                .downcast_ref::<io::Error>()
                                .is_some_and(|e| e.kind() == io::ErrorKind::BrokenPipe)
                            {
                                return Err(error);
                            }
                            report.errors += 1;
                            writeln!(diagnostics, "ERROR item={item}: {error:#}")?;
                        }
                    }
                }
                ensure!(
                    folder.properties().content_count()? == rows,
                    "folder count differs from contents table; completeness unknown"
                );
            } else {
                ensure!(
                    folder.properties().content_count()? == 0,
                    "missing contents table; completeness unknown"
                );
            }
            if let Some(table) = folder.hierarchy_table() {
                for row in table.rows_matrix() {
                    pending.push((u32::from(row.id()), depth + 1));
                }
            } else {
                ensure!(
                    !folder.properties().has_sub_folders()?,
                    "missing hierarchy table; subtree extent unknown"
                );
            }
            Ok(())
        })();
        if let Err(error) = folder_result {
            if error
                .downcast_ref::<io::Error>()
                .is_some_and(|e| e.kind() == io::ErrorKind::BrokenPipe)
            {
                return Err(error);
            }
            report.errors += 1;
            writeln!(
                diagnostics,
                "ERROR folder={node}: {error:#}; inaccessible subtree extent unknown"
            )?;
        }
    }
    output.flush()?;
    if fingerprint(&mut source)? != original_hash
        || fingerprint(&mut File::open(&path)?)? != original_hash
    {
        report.errors += 1;
        writeln!(diagnostics, "ERROR source changed during extraction")?;
    }
    writeln!(diagnostics, "folders={} encountered={} emitted={} non-mail={} errors={}; scope=IPM normal contents; MIME reconstructed", report.folders, report.encountered, report.emitted, report.non_mail, report.errors)?;
    Ok(report)
}

struct LimitedWriter<'a> {
    inner: &'a mut File,
    remaining: u64,
}
impl Write for LimitedWriter<'_> {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        if bytes.len() as u64 > self.remaining {
            return Err(io::Error::other("record exceeds 64 MiB output limit"));
        }
        let count = self.inner.write(bytes)?;
        self.remaining -= count as u64;
        Ok(count)
    }
    fn flush(&mut self) -> io::Result<()> {
        self.inner.flush()
    }
}

fn text(value: Option<&PropertyValue>) -> Result<Option<String>> {
    Ok(match value {
        None => None,
        Some(PropertyValue::Unicode(v)) => {
            Some(String::from_utf16(v.buffer()).context("invalid UTF-16 MAPI string")?)
        }
        Some(PropertyValue::String8(v)) => Some(
            std::str::from_utf8(v.buffer())
                .context("non-UTF-8 MAPI String8 metadata is not yet supported")?
                .to_owned(),
        ),
        _ => bail!("unexpected MAPI string property type"),
    })
}
fn raw(value: &PropertyValue) -> Result<Cow<'_, [u8]>> {
    Ok(match value {
        PropertyValue::Binary(v) => Cow::Borrowed(v.buffer()),
        PropertyValue::String8(v) => Cow::Borrowed(v.buffer()),
        PropertyValue::Unicode(v) => Cow::Owned(String::from_utf16(v.buffer())?.into_bytes()),
        _ => bail!("unexpected body/header property type"),
    })
}
fn encoded_header(name: &str, value: &str, out: &mut impl Write) -> Result<()> {
    // RFC 2047 words <= 75 characters; split only at UTF-8 character boundaries.
    write!(out, "{name}:")?;
    let mut chunk = String::new();
    let mut separator = " ";
    for ch in value.chars() {
        if chunk.len() + ch.len_utf8() > 42 {
            write!(
                out,
                "{separator}=?UTF-8?B?{}?=",
                STANDARD.encode(chunk.as_bytes())
            )?;
            chunk.clear();
            separator = "\r\n ";
        }
        chunk.push(ch);
    }
    if !chunk.is_empty() {
        write!(
            out,
            "{separator}=?UTF-8?B?{}?=",
            STANDARD.encode(chunk.as_bytes())
        )?;
    }
    write!(out, "\r\n")?;
    Ok(())
}
fn base64_part(
    out: &mut impl Write,
    boundary: &str,
    media: &str,
    filename: Option<&str>,
    content_id: Option<&str>,
    bytes: &[u8],
) -> Result<()> {
    write!(
        out,
        "--{boundary}\r\nContent-Type: {media}\r\nContent-Transfer-Encoding: base64\r\n"
    )?;
    if let Some(cid) = content_id {
        ensure!(
            cid.is_ascii() && !cid.contains(['\r', '\n', '<', '>']) && cid.len() < 980,
            "invalid attachment Content-ID"
        );
        write!(out, "Content-ID: <{cid}>\r\n")?;
    }
    if let Some(name) = filename {
        // RFC 2231 continuations avoid long lines and preserve arbitrary Unicode names.
        write!(
            out,
            "Content-Disposition: {};\r\n filename*0*=UTF-8''",
            if content_id.is_some() {
                "inline"
            } else {
                "attachment"
            }
        )?;
        let mut column = 0;
        for (index, chunk) in name.as_bytes().chunks(18).enumerate() {
            if index > 0 {
                write!(out, ";\r\n filename*{index}*=")?;
            }
            for byte in chunk {
                write!(out, "%{byte:02X}")?;
            }
            column += chunk.len();
        }
        if column == 0 {
            write!(out, "attachment")?;
        }
        write!(out, "\r\n")?;
    }
    write!(out, "\r\n")?;
    for chunk in bytes.chunks(57) {
        write!(out, "{}\r\n", STANDARD.encode(chunk))?;
    }
    // MIME separator CRLF is distinct from the encoded body (also handles empty parts).
    write!(out, "\r\n")?;
    Ok(())
}
fn charset(message: &dyn Message, value: &PropertyValue) -> Result<&'static str> {
    if matches!(value, PropertyValue::Unicode(_)) {
        return Ok("utf-8");
    }
    match message.properties().get(INTERNET_CPID) {
        Some(PropertyValue::Integer32(65001)) => Ok("utf-8"),
        Some(PropertyValue::Integer32(1252)) => Ok("windows-1252"),
        Some(PropertyValue::Integer32(20127)) => Ok("us-ascii"),
        Some(PropertyValue::Integer32(28591)) => Ok("iso-8859-1"),
        _ if raw(value)?.is_ascii() => Ok("us-ascii"),
        _ => bail!("unknown body code page; refusing to mislabel bytes"),
    }
}
fn render(
    message: &dyn Message,
    typed: Option<&PstMessage>,
    item: u32,
    out: &mut impl Write,
) -> Result<()> {
    let props = message.properties();
    let transport = props.get(TRANSPORT_HEADERS).map(raw).transpose()?;
    let original = transport
        .as_deref()
        .map(mailparse::parse_headers)
        .transpose()?;
    let headers = original.as_ref().map(|(h, _)| h.as_slice()).unwrap_or(&[]);
    use mailparse::MailHeaderMap;
    let date = headers
        .get_first_value("Date")
        .and_then(|v| {
            DateTime::parse_from_rfc2822(&v)
                .ok()
                .map(|d| d.to_rfc2822())
        })
        .or_else(|| {
            [SUBMIT_TIME, DELIVERY_TIME]
                .iter()
                .find_map(|id| match props.get(*id) {
                    Some(PropertyValue::Time(time)) => DateTime::<Utc>::from_timestamp(
                        time.div_euclid(10_000_000) - 11_644_473_600,
                        0,
                    )
                    .map(|v| v.to_rfc2822()),
                    _ => None,
                })
        })
        .unwrap_or_else(|| "Thu, 1 Jan 1970 00:00:00 +0000".into());
    write!(out, "Date: {date}\r\n")?;
    let from = headers
        .get_first_header("From")
        .map(|h| {
            std::str::from_utf8(h.get_value_raw())
                .map(|v| v.split_whitespace().collect::<Vec<_>>().join(" "))
        })
        .transpose()?
        .map(|v| Ok(Some(v)))
        .unwrap_or_else(|| {
            text(props.get(SENDER_SMTP)).and_then(|v| match v {
                Some(v) => Ok(Some(v)),
                None => text(props.get(SENDER_EMAIL)),
            })
        })?
        .unwrap_or_else(|| "unknown@invalid.invalid".into());
    ensure!(
        from.is_ascii() && !from.contains(['\r', '\n']) && from.len() < 990,
        "invalid or unsupported From address"
    );
    write!(out, "From: {from}\r\n")?;
    if let Some(subject) = text(props.get(SUBJECT))?.or_else(|| headers.get_first_value("Subject"))
    {
        encoded_header(
            "Subject",
            subject.trim_start_matches(['\u{1}', '\u{5}']),
            out,
        )?;
    }
    let original_id = headers
        .get_first_value("Message-ID")
        .or(text(props.get(MESSAGE_ID))?);
    if let Some(id) = original_id
        .as_deref()
        .filter(|id| crate::message_id_valid(id))
    {
        write!(out, "Message-ID: {id}\r\n")?;
    }
    // Preserve original non-content fields, including inherited importer provenance.
    // Exact complete transport-header property is also carried as a separate MIME part.
    for header in headers {
        let name = header.get_key();
        if [
            "date",
            "from",
            "subject",
            "message-id",
            "mime-version",
            "content-length",
        ]
        .contains(&name.to_ascii_lowercase().as_str())
            || name.to_ascii_lowercase().starts_with("content-")
        {
            continue;
        }
        ensure!(
            name.is_ascii() && name.bytes().all(|b| (33..=126).contains(&b) && b != b':'),
            "invalid transport header name"
        );
        let value = header.get_value_raw();
        write!(out, "{name}: ")?;
        // mboxrd quote every physical line; copied values retain their original folds.
        for (index, line) in value.split_inclusive(|b| *b == b'\n').enumerate() {
            let depth = line.iter().take_while(|b| **b == b'>').count();
            if index > 0 && line[depth..].starts_with(b"From ") {
                out.write_all(b">")?;
            }
            out.write_all(line)?;
        }
        write!(out, "\r\n")?;
    }
    write_recipients(message, headers, out)?;
    let boundary = format!("=_mct_pst_{item:08x}");
    write!(
        out,
        "MIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=\"{boundary}\"\r\n\r\n"
    )?;
    let alternatives = props.get(BODY).is_some() && props.get(HTML).is_some();
    let body_boundary = if alternatives {
        format!("=_mct_alternative_{item:08x}")
    } else {
        boundary.clone()
    };
    if alternatives {
        write!(out, "--{boundary}\r\nContent-Type: multipart/alternative; boundary=\"{body_boundary}\"\r\n\r\n")?;
    }
    let mut has_body = false;
    for (id, media) in [(BODY, "text/plain"), (HTML, "text/html")] {
        if let Some(value) = props.get(id) {
            base64_part(
                out,
                &body_boundary,
                &format!("{media}; charset={}", charset(message, value)?),
                None,
                None,
                &raw(value)?,
            )?;
            has_body = true;
        }
    }
    if alternatives {
        write!(out, "--{body_boundary}--\r\n\r\n")?;
    }
    if let Some(value) = props.get(RTF_COMPRESSED) {
        base64_part(
            out,
            &boundary,
            "application/octet-stream",
            Some("body.rtf-compressed"),
            None,
            &raw(value)?,
        )?;
        has_body = true;
    }
    // Absent body is a valid empty mail; never pretend an undecodable property was empty.
    if !has_body {
        base64_part(
            out,
            &boundary,
            "text/plain; charset=us-ascii",
            None,
            None,
            b"",
        )?;
    }
    if let Some(id) = original_id
        .as_deref()
        .filter(|id| !crate::message_id_valid(id))
    {
        base64_part(
            out,
            &boundary,
            "text/plain; charset=utf-8",
            Some("original-message-id.txt"),
            None,
            id.as_bytes(),
        )?;
    }
    if let Some(bytes) = transport {
        base64_part(
            out,
            &boundary,
            "application/octet-stream",
            Some("original-transport-headers.txt"),
            None,
            &bytes,
        )?;
    }
    if let Some(table) = message.attachment_table() {
        for row in table.rows_matrix() {
            let typed = typed.context(
                "embedded messages with attachments are not supported by this adapter version",
            )?;
            let attachment = typed.attachment(NodeId::from(u32::from(row.id())))?;
            let method = attachment.properties().attachment_method()?;
            let filename = text(attachment.properties().get(ATTACH_LONG_NAME))?
                .or(text(attachment.properties().get(ATTACH_SHORT_NAME))?)
                .unwrap_or_else(|| "attachment.bin".into());
            let media = text(attachment.properties().get(0x370E))?
                .filter(|v| !v.is_empty())
                .unwrap_or_else(|| "application/octet-stream".into());
            ensure!(
                media.is_ascii() && !media.contains(['\r', '\n']) && media.len() < 128,
                "invalid attachment media type"
            );
            let cid = text(attachment.properties().get(0x3712))?.filter(|v| !v.is_empty());
            match (method, attachment.data()) {
                (1, Some(AttachmentData::Binary(bytes))) => base64_part(out, &boundary, &media, Some(&filename), cid.as_deref(), bytes.buffer())?,
                // Opaque embedded mail is retained only when fully reconstructable.
                (5, Some(AttachmentData::Message(embedded))) => {
                    let mut spool = tempfile::tempfile()?;
                    render(embedded.as_ref(), None, item.wrapping_add(1), &mut LimitedWriter { inner: &mut spool, remaining: MAX_RECORD })?;
                    spool.rewind()?;
                    write!(out, "--{boundary}\r\nContent-Type: message/rfc822\r\nContent-Disposition: attachment\r\n\r\n")?;
                    io::copy(&mut spool, out)?;
                    write!(out, "\r\n")?;
                }
                _ => bail!("unsupported or missing attachment data (method {method}); no external reference is fetched"),
            }
        }
    }
    write!(out, "--{boundary}--\r\n")?;
    Ok(())
}

fn write_recipients(
    message: &dyn Message,
    headers: &[mailparse::MailHeader<'_>],
    out: &mut impl Write,
) -> Result<()> {
    use mailparse::MailHeaderMap;
    let Some(table) = message.recipient_table() else {
        return Ok(());
    };
    let context = table.context();
    let mut recipients: [Vec<String>; 3] = Default::default();
    for row in table.rows_matrix() {
        let columns = row.columns(context)?;
        let read = |id| -> Result<Option<PropertyValue>> {
            let Some(index) = context.columns().iter().position(|c| c.prop_id() == id) else {
                return Ok(None);
            };
            columns[index]
                .as_ref()
                .map(|v| table.read_column(v, context.columns()[index].prop_type()))
                .transpose()
                .map_err(Into::into)
        };
        let index = match read(0x0C15)? {
            Some(PropertyValue::Integer32(1)) => 0,
            Some(PropertyValue::Integer32(2)) => 1,
            Some(PropertyValue::Integer32(3)) => 2,
            _ => bail!("invalid recipient type"),
        };
        if headers
            .get_first_header(["To", "Cc", "Bcc"][index])
            .is_some()
        {
            continue;
        }
        let smtp = read(0x39FE)?;
        let address = if let Some(address) = text(smtp.as_ref())? {
            address
        } else {
            ensure!(
                text(read(0x3002)?.as_ref())?.is_some_and(|v| v.eq_ignore_ascii_case("SMTP")),
                "recipient has no SMTP address; refusing to omit it"
            );
            text(read(0x3003)?.as_ref())?.context("missing recipient address")?
        };
        ensure!(
            address.is_ascii() && !address.contains(['\r', '\n']) && address.len() < 980,
            "invalid recipient address"
        );
        recipients[index].push(address);
    }
    for (field, addresses) in ["To", "Cc", "Bcc"].iter().zip(recipients) {
        if !addresses.is_empty() {
            write!(out, "{field}: {}\r\n", addresses.join(",\r\n "))?;
        }
    }
    Ok(())
}
