// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

//! Read-only Microsoft outlook-pst adapter. Requirements: doc/PST_IMPORTER.md.
use anyhow::{bail, ensure, Context, Result};
use base64::{engine::general_purpose::STANDARD, Engine};
use chrono::{DateTime, Utc};
use outlook_pst::{
    ltp::prop_context::PropertyValue,
    messaging::{
        attachment::{AnsiAttachment, Attachment, AttachmentData, UnicodeAttachment},
        message::{AnsiMessage, Message, UnicodeMessage},
        named_prop::{NamedPropertyGuid, NamedPropertyId},
        store::{AnsiStore, Store, UnicodeStore},
    },
    ndb::node_id::{NodeId, NodeIdType, NID_ROOT_FOLDER},
    AnsiPstFile, UnicodePstFile,
};
use sha2::{Digest, Sha256};
use std::{
    borrow::Cow,
    collections::{HashMap, HashSet},
    fs::File,
    io::{self, BufRead, BufReader, Read, Seek, Write},
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
const SENDER_NAME: u16 = 0x0C1A;
const REPRESENTING_EMAIL: u16 = 0x0065;
const REPRESENTING_SMTP: u16 = 0x5D02;
const RECIPIENT_EMAIL: u16 = 0x3003;
const RECIPIENT_SMTP: u16 = 0x39FE;
const MESSAGE_ID: u16 = 0x1035;
const SUBMIT_TIME: u16 = 0x0039;
const DELIVERY_TIME: u16 = 0x0E06;
const INTERNET_CPID: u16 = 0x3FDE;
const MESSAGE_CPID: u16 = 0x3FFD;
const ATTACH_LONG_NAME: u16 = 0x3707;
const ATTACH_SHORT_NAME: u16 = 0x3704;
const MAX_DEPTH: usize = 64;
const MAX_RECORD: u64 = crate::DEFAULT_MAX_MESSAGE_BYTES as u64;

#[derive(Debug)]
struct ExchangeMapping {
    smtp: String,
    item: u32,
    property: u16,
}

#[derive(Default)]
struct ExchangeDirectory {
    mappings: HashMap<String, Option<ExchangeMapping>>,
    contacts: Vec<ContactAddress>,
    failures: HashMap<u32, anyhow::Error>,
    contact_items: usize,
}

#[derive(Debug)]
struct ContactAddress {
    address: Option<String>,
    smtp: Option<String>,
    entry_dn: Option<String>,
    item: u32,
    property: u16,
}

// MS-OXOCNTC: Email1, Email2, Email3 in PSETID_Address. These are LIDs,
// not property IDs; each store supplies its own name-to-ID map.
const EMAIL_SLOTS: [u32; 3] = [0x8080, 0x8090, 0x80A0];

fn contact_property_ids(pst: &PstStore) -> Result<HashMap<u32, u16>> {
    let map = pst.store().named_property_map()?;
    let guids = map.properties().stream_guid()?;
    let mut ids = HashMap::new();
    for entry in map.properties().stream_entry()? {
        let (NamedPropertyGuid::GuidIndex(index), NamedPropertyId::Number(lid)) =
            (entry.guid(), entry.id())
        else {
            continue;
        };
        let guid = guids
            .get(usize::from(index))
            .context("named-property GUID index out of bounds")?;
        if guid.data1() == 0x00062004
            && guid.data2() == 0
            && guid.data3() == 0
            && guid.data4() == &[0xC0, 0, 0, 0, 0, 0, 0, 0x46]
            && EMAIL_SLOTS
                .iter()
                .any(|base| (*base + 2..=*base + 5).contains(&lid))
        {
            ensure!(
                ids.insert(lid, entry.prop_id()).is_none(),
                "duplicate contact named property"
            );
        }
    }
    Ok(ids)
}

fn address_book_dn(bytes: &[u8]) -> Option<String> {
    // MS-OXCDATA 2.2.5.2: long-term Exchange Address Book EntryID.
    const PREFIX: [u8; 24] = [
        0, 0, 0, 0, 0xDC, 0xA7, 0x40, 0xC8, 0xC0, 0x42, 0x10, 0x1A, 0xB4, 0xB9, 8, 0, 0x2B, 0x2F,
        0xE1, 0x82, 1, 0, 0, 0,
    ];
    if !bytes.starts_with(&PREFIX) {
        return None;
    }
    let kind = u32::from_le_bytes(bytes.get(24..28)?.try_into().ok()?);
    if kind > 6 {
        return None;
    }
    let dn = std::str::from_utf8(bytes.get(28..)?.strip_suffix(&[0])?).ok()?;
    crate::exchange_dn_valid(dn).then(|| dn.to_owned())
}

fn smtp_valid(smtp: &str) -> bool {
    smtp.len() < 990 && !smtp.chars().any(char::is_control)
        && smtp.split_once('@').is_some_and(|(local, domain)| !local.is_empty() && !domain.is_empty())
        && mailparse::addrparse(smtp).is_ok_and(|addresses| addresses.len() == 1 && matches!(&addresses[0], mailparse::MailAddr::Single(address) if address.addr == smtp))
}

impl ExchangeDirectory {
    fn remember(&mut self, dn: Option<String>, smtp: Option<String>, item: u32, property: u16) {
        let (Some(dn), Some(smtp)) = (dn, smtp) else {
            return;
        };
        if !crate::exchange_dn_valid(&dn) || dn.len() >= 990 || !smtp_valid(&smtp) {
            return;
        }
        self.mappings
            .entry(dn.to_ascii_lowercase())
            .and_modify(|mapping| {
                if mapping
                    .as_ref()
                    .is_some_and(|value| !value.smtp.eq_ignore_ascii_case(&smtp))
                {
                    *mapping = None;
                }
            })
            .or_insert(Some(ExchangeMapping {
                smtp,
                item,
                property,
            }));
    }

    fn resolve(&self, dn: &str) -> Option<&ExchangeMapping> {
        self.mappings
            .get(&dn.to_ascii_lowercase())
            .and_then(Option::as_ref)
    }

    fn read_contact<'a>(
        &mut self,
        item: u32,
        ids: &HashMap<u32, u16>,
        get_text: impl Fn(u16) -> Result<Option<String>>,
        get_binary: impl Fn(u16) -> Option<&'a [u8]>,
    ) -> Result<()> {
        self.contact_items += 1;
        for base in EMAIL_SLOTS {
            let property = |offset| ids.get(&(base + offset)).copied();
            let value = |offset| {
                property(offset)
                    .map(&get_text)
                    .transpose()
                    .map(Option::flatten)
            };
            let address = value(3)?;
            let original = value(4)?;
            let address_type = value(2)?;
            let is_smtp = address_type
                .as_deref()
                .is_some_and(|v| v.eq_ignore_ascii_case("SMTP"));
            let smtp = if is_smtp { address.clone() } else { original };
            let smtp = smtp.filter(|value| smtp_valid(value));
            let entry_dn = property(5).and_then(&get_binary).and_then(address_book_dn);
            if address.is_none() && smtp.is_none() && entry_dn.is_none() {
                continue;
            }
            let contact = ContactAddress {
                address,
                smtp,
                entry_dn,
                item,
                property: property(if is_smtp { 3 } else { 4 }).unwrap_or(0),
            };
            self.remember(
                contact.address.clone(),
                contact.smtp.clone(),
                contact.item,
                contact.property,
            );
            self.remember(
                contact.entry_dn.clone(),
                contact.smtp.clone(),
                contact.item,
                contact.property,
            );
            self.contacts.push(contact);
        }
        Ok(())
    }
}

fn exchange_directory(pst: &PstStore) -> ExchangeDirectory {
    // Scan the entire hierarchy before emitting mail; retain addresses, not bodies.
    let mut directory = ExchangeDirectory::default();
    let ids = match contact_property_ids(pst) {
        Ok(ids) => ids,
        Err(error) => {
            directory
                .failures
                .insert(0, error.context("read contact named-property map"));
            HashMap::new()
        }
    };
    let mut pending = vec![(u32::from(NID_ROOT_FOLDER), 0)];
    let mut visited = HashSet::new();
    let mut messages = HashSet::new();
    while let Some((node, depth)) = pending.pop() {
        if NodeId::from(node).id_type().ok() == Some(NodeIdType::SearchFolder)
            || !visited.insert(node)
        {
            continue;
        }
        let folder = (|| -> Result<_> {
            ensure!(
                depth <= MAX_DEPTH,
                "address lookup folder nesting exceeds {MAX_DEPTH}"
            );
            let entry = pst.store().properties().make_entry_id(NodeId::from(node))?;
            Ok(pst.store().open_folder(&entry)?)
        })();
        let folder = match folder {
            Ok(folder) => folder,
            Err(error) => {
                directory.failures.insert(node, error);
                continue;
            }
        };
        if let Some(table) = folder.contents_table() {
            for row in table.rows_matrix() {
                let item = u32::from(row.id());
                if !messages.insert(item) {
                    continue;
                }
                let message = match pst.message(item) {
                    Ok(message) => message,
                    Err(error) => {
                        directory.failures.insert(item, error);
                        continue;
                    }
                };
                let message = message.message();
                if message.properties().message_class().is_ok_and(|class| {
                    class.eq_ignore_ascii_case("IPM.Contact")
                        || class.to_ascii_lowercase().starts_with("ipm.contact.")
                }) {
                    if let Err(error) = directory.read_contact(
                        item,
                        &ids,
                        |id| {
                            text(
                                message
                                    .properties()
                                    .get(id)
                                    .filter(|v| !matches!(v, PropertyValue::Null)),
                            )
                        },
                        |id| match message.properties().get(id) {
                            Some(PropertyValue::Binary(value)) => Some(value.buffer()),
                            _ => None,
                        },
                    ) {
                        directory
                            .failures
                            .insert(item, error.context("read contact email slots"));
                    }
                }
                for (dn, smtp) in [
                    (SENDER_EMAIL, SENDER_SMTP),
                    (REPRESENTING_EMAIL, REPRESENTING_SMTP),
                ] {
                    directory.remember(
                        text(message.properties().get(dn)).ok().flatten(),
                        text(message.properties().get(smtp)).ok().flatten(),
                        item,
                        smtp,
                    );
                }
                if let Some(table) = message.recipient_table() {
                    let context = table.context();
                    for row in table.rows_matrix() {
                        let columns = match row.columns(context) {
                            Ok(columns) => columns,
                            Err(error) => {
                                directory.failures.insert(item, error.into());
                                continue;
                            }
                        };
                        let read = |id| -> Option<String> {
                            let index = context.columns().iter().position(|c| c.prop_id() == id)?;
                            let value = table
                                .read_column(
                                    columns[index].as_ref()?,
                                    context.columns()[index].prop_type(),
                                )
                                .ok()?;
                            text(Some(&value)).ok().flatten()
                        };
                        directory.remember(
                            read(RECIPIENT_EMAIL),
                            read(RECIPIENT_SMTP),
                            item,
                            RECIPIENT_SMTP,
                        );
                    }
                }
            }
        }
        if let Some(table) = folder.hierarchy_table() {
            for row in table.rows_matrix() {
                pending.push((u32::from(row.id()), depth + 1));
            }
        }
        let completeness = (|| -> Result<()> {
            let rows = folder
                .contents_table()
                .map_or(0, |table| table.rows_matrix().count());
            ensure!(
                folder.properties().content_count()? as usize == rows,
                "address lookup folder count differs from contents table"
            );
            ensure!(
                folder.hierarchy_table().is_some() || !folder.properties().has_sub_folders()?,
                "address lookup missing hierarchy table; subtree extent unknown"
            );
            Ok(())
        })();
        if let Err(error) = completeness {
            directory.failures.insert(node, error);
        }
    }
    directory
}

/// Counts describe encountered objects, never an estimate of inaccessible subtrees.
#[derive(Default, Debug)]
pub struct ImportReport {
    pub folders: u64,
    pub encountered: u64,
    pub emitted: u64,
    pub non_mail: u64,
    pub errors: u64,
    pub invalid_exported: u64,
}

fn excluded_class(class: &str) -> bool {
    [
        "ipm.contact",
        "ipm.distlist",
        "ipm.appointment",
        "ipm.task",
        "ipm.activity",
        "ipm.stickynote",
        "ipm.schedule.meeting",
    ]
    .iter()
    .any(|prefix| {
        class.eq_ignore_ascii_case(prefix)
            || class
                .to_ascii_lowercase()
                .starts_with(&format!("{prefix}."))
    })
}

fn error_kind(error: &anyhow::Error) -> &'static str {
    if error.chain().any(|cause| {
        let cause = cause
            .downcast_ref::<io::Error>()
            .and_then(io::Error::get_ref)
            .map_or(cause, |inner| inner as &(dyn std::error::Error + 'static));
        cause.is::<outlook_pst::PstError>()
            || cause.is::<outlook_pst::messaging::MessagingError>()
            || cause.is::<outlook_pst::ltp::LtpError>()
            || cause.is::<outlook_pst::ndb::NdbError>()
    }) {
        "LIBRARY ERROR (outlook-pst 1.2.0)"
    } else {
        "ERROR"
    }
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
    import_selected(path, output, diagnostics, false)
}

/// Diagnostic mode exports available properties of failed items, never valid mail.
pub fn import_selected(
    path: &Path,
    output: &mut impl Write,
    diagnostics: &mut impl Write,
    only_invalid: bool,
) -> Result<ImportReport> {
    let scanner = crate::antivirus::Scanner::from_environment()?;
    let path = path.canonicalize().context("resolve source filename")?;
    let mut source = File::open(&path).context("open source read-only")?;
    ensure!(source.metadata()?.is_file(), "source is not a regular file");
    let original_hash = fingerprint(&mut source)?;
    writeln!(diagnostics, "pst-importer {}: outlook-pst 1.2.0; API {}; source-sha256={original_hash}; MIME reconstructed from MAPI", env!("CARGO_PKG_VERSION"), crate::API_VERSION)?;
    let mut uri = Url::from_file_path(&path)
        .map_err(|()| anyhow::anyhow!("cannot represent source filename as file URI"))?;
    let pst = open(&source)?;
    let root = pst.store().properties().ipm_sub_tree_entry_id()?;
    let mut directory = exchange_directory(&pst);
    writeln!(diagnostics, "address-book: contacts={} email-slots={} exchange-identities={} conflicts={} unreadable={}; scope=entire folder hierarchy, normal contents",
        directory.contact_items, directory.contacts.len(), directory.mappings.values().filter(|v| v.is_some()).count(), directory.mappings.values().filter(|v| v.is_none()).count(), directory.failures.len())?;
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
                    let mut loaded = None;
                    let mut reconstructed = None;
                    let item_result = (|| -> Result<bool> {
                        ensure!(messages.insert(item), "duplicate message node in traversal");
                        let message = loaded.insert(pst.message(item)?);
                        let class = message.message().properties().message_class()?;
                        if excluded_class(&class) {
                            return Ok(false);
                        }
                        if !class.eq_ignore_ascii_case("IPM.Note")
                            && !class.to_ascii_lowercase().starts_with("ipm.note.")
                        {
                            bail!("unsupported message class {class:?}");
                        }
                        let spool = reconstructed.insert(tempfile::tempfile()?);
                        {
                            let mut writer = LimitedWriter {
                                inner: spool,
                                remaining: MAX_RECORD,
                            };
                            write!(writer, "From pst-importer Thu Jan  1 00:00:00 1970\nX-Imported-URI: {uri}\r\nX-Importer-Name: pst-importer\r\nX-Importer-Version: {}\r\n", env!("CARGO_PKG_VERSION"))?;
                            render(
                                message.message(),
                                Some(message),
                                item,
                                &directory,
                                &mut writer,
                            )?;
                            writer.write_all(b"\n")?;
                        }
                        spool.rewind()?;
                        let mut failures = Vec::new();
                        let validation = crate::validate(
                            &mut BufReader::new(&mut *spool),
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
                        if !only_invalid {
                            if let Some(scanner) = &scanner {
                                scanner.write_record(spool, output)?;
                            } else {
                                io::copy(spool, output).context("write stdout")?;
                            }
                        }
                        Ok(true)
                    })();
                    match item_result {
                        Ok(true) => report.emitted += u64::from(!only_invalid),
                        Ok(false) => report.non_mail += 1,
                        Err(error) => {
                            directory.failures.remove(&item);
                            if error
                                .downcast_ref::<io::Error>()
                                .is_some_and(|e| e.kind() == io::ErrorKind::BrokenPipe)
                            {
                                return Err(error);
                            }
                            report.errors += 1;
                            writeln!(diagnostics, "{} item={item}: {error:#}", error_kind(&error))?;
                            if only_invalid {
                                if let Some(message) = loaded {
                                    export_invalid(
                                        message.message(),
                                        &uri,
                                        item,
                                        &error,
                                        reconstructed.as_mut(),
                                        output,
                                    )?;
                                    report.invalid_exported += 1;
                                }
                            }
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
            directory.failures.remove(&node);
            if error
                .downcast_ref::<io::Error>()
                .is_some_and(|e| e.kind() == io::ErrorKind::BrokenPipe)
            {
                return Err(error);
            }
            report.errors += 1;
            writeln!(
                diagnostics,
                "{} folder={node}: {error:#}; inaccessible subtree extent unknown",
                error_kind(&error)
            )?;
        }
    }
    // Main-pass failures were already reported. Surface remaining lookup failures,
    // including contacts outside the IPM mail subtree, without double counting.
    let mut failures: Vec<_> = directory.failures.into_iter().collect();
    failures.sort_by_key(|(node, _)| *node);
    for (node, error) in failures {
        report.errors += 1;
        writeln!(
            diagnostics,
            "{} address-book node={node}: {error:#}",
            error_kind(&error)
        )?;
    }
    output.flush()?;
    if fingerprint(&mut source)? != original_hash
        || fingerprint(&mut File::open(&path)?)? != original_hash
    {
        report.errors += 1;
        writeln!(diagnostics, "ERROR source changed during extraction")?;
    }
    writeln!(diagnostics, "folders={} encountered={} emitted={} non-mail={} errors={}; scope=IPM normal contents; MIME reconstructed", report.folders, report.encountered, report.emitted, report.non_mail, report.errors)?;
    if only_invalid {
        writeln!(
            diagnostics,
            "invalid-exported={}; diagnostic records only; unreadable items cannot be exported",
            report.invalid_exported
        )?;
    }
    Ok(report)
}

fn export_invalid(
    message: &dyn Message,
    uri: &Url,
    item: u32,
    error: &anyhow::Error,
    reconstructed: Option<&mut File>,
    output: &mut impl Write,
) -> Result<()> {
    let mut spool = tempfile::tempfile()?;
    {
        let out = &mut LimitedWriter {
            inner: &mut spool,
            remaining: MAX_RECORD,
        };
        let boundary = format!("=_mct_invalid_{item:08x}");
        write!(out, "From pst-importer Thu Jan  1 00:00:00 1970\nX-Imported-URI: {uri}\r\nX-Importer-Name: pst-importer\r\nX-Importer-Version: {}\r\nX-PST-Diagnostic: invalid-message\r\nFrom: pst-diagnostic@invalid.invalid\r\nDate: Thu, 1 Jan 1970 00:00:00 +0000\r\nSubject: PST diagnostic for item {item}\r\nMIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=\"{boundary}\"\r\n\r\n", env!("CARGO_PKG_VERSION"))?;
        let props = message.properties();
        let summary = format!(
            "Diagnostic evidence, not a recovered message. Source: {uri}\n{}: {error:#}\nSubject: {:?}\nSender SMTP: {:?}\nSender email: {:?}\nInternet code page (0x3FDE): {:?}\nMessage code page (0x3FFD): {:?}\nUnicode properties are attached as UTF-16LE; String8/binary properties retain their bytes. Attachments and unmapped properties remain in the source PST.\n",
            error_kind(error), props.get(SUBJECT), props.get(SENDER_SMTP),
            props.get(SENDER_EMAIL), props.get(INTERNET_CPID), props.get(MESSAGE_CPID),
        );
        base64_part(
            out,
            &boundary,
            "text/plain; charset=utf-8",
            None,
            None,
            summary.as_bytes(),
        )?;
        if let Some(reconstructed) = reconstructed {
            if let Some(headers) = reconstructed_headers(reconstructed)? {
                base64_part(
                    out,
                    &boundary,
                    "application/octet-stream",
                    Some("reconstructed-headers.txt"),
                    None,
                    &headers,
                )?;
            }
        }
        for (id, name) in [
            (TRANSPORT_HEADERS, "transport-headers"),
            (SUBJECT, "subject"),
            (SENDER_SMTP, "sender-smtp"),
            (SENDER_EMAIL, "sender-email"),
            (BODY, "body"),
            (HTML, "body-html"),
            (RTF_COMPRESSED, "body-rtf-compressed"),
        ] {
            let Some(value) = props.get(id) else {
                continue;
            };
            let (bytes, media) = match value {
                PropertyValue::Unicode(v) => (
                    Cow::Owned(
                        v.buffer()
                            .iter()
                            .flat_map(|c| c.to_le_bytes())
                            .collect::<Vec<_>>(),
                    ),
                    "text/plain; charset=utf-16le",
                ),
                PropertyValue::String8(v) => {
                    (Cow::Borrowed(v.buffer()), "application/octet-stream")
                }
                PropertyValue::Binary(v) => (Cow::Borrowed(v.buffer()), "application/octet-stream"),
                _ => continue,
            };
            base64_part(out, &boundary, media, Some(name), None, &bytes)?;
        }
        write!(out, "--{boundary}--\r\n\n")?;
    }
    spool.rewind()?;
    io::copy(&mut spool, output).context("write diagnostic stdout")?;
    Ok(())
}

fn reconstructed_headers(spool: &mut File) -> Result<Option<Vec<u8>>> {
    spool.rewind()?;
    let mut reader = BufReader::new(spool.take(MAX_RECORD));
    // Exclude the mbox envelope; retain every RFC header byte and fold.
    let mut headers = Vec::new();
    reader.read_until(b'\n', &mut headers)?;
    headers.clear();
    loop {
        let start = headers.len();
        if reader.read_until(b'\n', &mut headers)? == 0 {
            return Ok(None); // Rendering failed before the headers were complete.
        }
        if &headers[start..] == b"\r\n" || &headers[start..] == b"\n" {
            return Ok(Some(headers));
        }
    }
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

fn subject_header(value: &str, out: &mut impl Write) -> Result<()> {
    // MS-PST 2.5.3.1.1.1: marker + prefix-length character precede the
    // complete subject. Retain both the textual prefix and normalized subject.
    let value = if let Some(rest) = value.strip_prefix('\u{1}') {
        let length = rest
            .chars()
            .next()
            .context("missing PST subject prefix length")?;
        &rest[length.len_utf8()..]
    } else {
        value
    };
    ensure!(
        !value.chars().any(|c| c.is_control() && c != '\t'),
        "control character in Subject"
    );
    let header = format!("Subject: {value}");
    let (header, _) = mailparse::parse_header(header.as_bytes())?;
    let decoded = header.get_value();
    ensure!(
        !decoded.chars().any(|c| c.is_control() && c != '\t'),
        "decoded control character in Subject"
    );
    // Keep normal subjects readable. Encoded words are needed only to fold an
    // exceptionally long unbroken word without adding whitespace to its value.
    if decoded.split([' ', '\t']).any(|word| word.len() > 900) {
        return encoded_header("Subject", &decoded, out);
    }
    write!(out, "Subject: ")?;
    let mut column = "Subject: ".len();
    for ch in decoded.chars() {
        if matches!(ch, ' ' | '\t') && column >= 78 {
            write!(out, "\r\n")?;
            column = 0;
        }
        write!(out, "{ch}")?;
        column += ch.len_utf8();
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
fn byte_charset(codepage: Option<&PropertyValue>, bytes: &[u8]) -> Result<&'static str> {
    match codepage {
        Some(PropertyValue::Integer32(65001)) => Ok("utf-8"),
        Some(PropertyValue::Integer32(1252)) => Ok("windows-1252"),
        Some(PropertyValue::Integer32(1256)) => Ok("windows-1256"),
        Some(PropertyValue::Integer32(20127)) => Ok("us-ascii"),
        Some(PropertyValue::Integer32(28591)) => Ok("iso-8859-1"),
        _ if bytes.is_ascii() => Ok("us-ascii"),
        _ => bail!("unknown body code page; refusing to mislabel bytes"),
    }
}
fn render(
    message: &dyn Message,
    typed: Option<&PstMessage>,
    item: u32,
    directory: &ExchangeDirectory,
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
    let from = if let Some(mapping) = directory.resolve(&from) {
        write!(out, "X-PST-Original-Sender: {from}\r\nX-PST-Sender-Resolution: item={}; property=0x{:04X}\r\n", mapping.item, mapping.property)?;
        let name = text(props.get(SENDER_NAME))
            .ok()
            .flatten()
            .filter(|name| !name.chars().any(char::is_control));
        if let Some(name) = name {
            format!(
                "\"{}\" <{}>",
                name.replace('\\', "\\\\").replace('"', "\\\""),
                mapping.smtp
            )
        } else {
            mapping.smtp.clone()
        }
    } else {
        resolve_address_header("From", &from, directory, out)?.unwrap_or(from)
    };
    ensure!(
        !from.chars().any(char::is_control) && from.len() < 990,
        "invalid or unsupported From address"
    );
    write!(out, "From: {from}\r\n")?;
    if crate::exchange_dn_valid(&from) {
        writeln!(out, "{}: EX\r", crate::PST_SENDER_ADDRESS_TYPE)?;
    }
    if let Some(subject) = text(props.get(SUBJECT))?.or_else(|| headers.get_first_value("Subject"))
    {
        subject_header(&subject, out)?;
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
        if [
            "to",
            "cc",
            "bcc",
            "sender",
            "reply-to",
            "resent-from",
            "resent-sender",
            "resent-to",
            "resent-cc",
            "resent-bcc",
            "return-path",
        ]
        .contains(&name.to_ascii_lowercase().as_str())
        {
            let unfolded = header.get_value();
            if let Some(value) = resolve_address_header(&name, &unfolded, directory, out)? {
                write!(out, "{name}: {value}\r\n")?;
                continue;
            }
        }
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
    write_recipients(message, headers, directory, out)?;
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
            let bytes = raw(value)?;
            let charset = if matches!(value, PropertyValue::Unicode(_)) {
                "utf-8"
            } else {
                byte_charset(props.get(INTERNET_CPID), &bytes)?
            };
            base64_part(
                out,
                &body_boundary,
                &format!("{media}; charset={charset}"),
                None,
                None,
                &bytes,
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
                    render(embedded.as_ref(), None, item.wrapping_add(1), directory, &mut LimitedWriter { inner: &mut spool, remaining: MAX_RECORD })?;
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

fn resolve_address_header(
    field: &str,
    value: &str,
    directory: &ExchangeDirectory,
    out: &mut impl Write,
) -> Result<Option<String>> {
    // Rewrite only an entire native address or an angle-bracket address outside
    // quoted display names/comments. Never replace matching text in a name.
    let mut ranges = Vec::new();
    if directory.resolve(value).is_some() {
        ranges.push(0..value.len());
    } else {
        let (mut quoted, mut escaped, mut comments) = (false, false, 0usize);
        let mut chars = value.char_indices();
        while let Some((index, c)) = chars.next() {
            if escaped {
                escaped = false;
                continue;
            }
            if c == '\\' && (quoted || comments > 0) {
                escaped = true;
                continue;
            }
            if c == '"' && comments == 0 {
                quoted = !quoted;
                continue;
            }
            if quoted {
                continue;
            }
            if c == '(' {
                comments += 1;
                continue;
            }
            if c == ')' && comments > 0 {
                comments -= 1;
                continue;
            }
            if c == '<' && comments == 0 {
                if let Some((end, _)) = chars.by_ref().find(|(_, c)| *c == '>') {
                    let range = index + 1..end;
                    if directory.resolve(&value[range.clone()]).is_some() {
                        ranges.push(range);
                    }
                }
            }
        }
    }
    if ranges.is_empty() {
        return Ok(None);
    }
    let mut resolved = String::new();
    let mut start = 0;
    for range in ranges {
        let native = &value[range.clone()];
        let mapping = directory
            .resolve(native)
            .context("missing address mapping")?;
        resolved.push_str(&value[start..range.start]);
        resolved.push_str(&mapping.smtp);
        start = range.end;
        write!(out, "X-PST-Original-Address: field={field};\r\n {native}\r\nX-PST-Address-Resolution: field={field}; item={}; property=0x{:04X}\r\n", mapping.item, mapping.property)?;
    }
    resolved.push_str(&value[start..]);
    ensure!(
        !resolved.chars().any(char::is_control),
        "control character in resolved address header"
    );
    Ok(Some(resolved))
}

fn write_recipients(
    message: &dyn Message,
    headers: &[mailparse::MailHeader<'_>],
    directory: &ExchangeDirectory,
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
        let native = text(read(RECIPIENT_EMAIL)?.as_ref())?;
        let smtp = text(read(RECIPIENT_SMTP)?.as_ref())?;
        let address_type = text(read(0x3002)?.as_ref())?;
        let (address, mapping) = recipient_address(
            address_type.as_deref(),
            native.as_deref(),
            smtp.as_deref(),
            directory,
        )?;
        if let Some(mapping) = mapping {
            write!(out, "X-PST-Original-Recipient: {} <{}>\r\nX-PST-Recipient-Resolution: field={}; item={}; property=0x{:04X}\r\n",
                ["To", "Cc", "Bcc"][index], native.as_deref().unwrap_or_default(), ["To", "Cc", "Bcc"][index], mapping.item, mapping.property)?;
        }
        ensure!(
            address.is_ascii() && !address.contains(['\r', '\n']) && address.len() < 980,
            "invalid recipient address"
        );
        recipients[index].push(address.to_owned());
    }
    for (field, addresses) in ["To", "Cc", "Bcc"].iter().zip(recipients) {
        if !addresses.is_empty() {
            write!(out, "{field}: {}\r\n", addresses.join(",\r\n "))?;
        }
    }
    Ok(())
}

fn recipient_address<'a>(
    address_type: Option<&str>,
    native: Option<&'a str>,
    smtp: Option<&'a str>,
    directory: &'a ExchangeDirectory,
) -> Result<(&'a str, Option<&'a ExchangeMapping>)> {
    if let Some(smtp) = smtp.filter(|value| smtp_valid(value)) {
        return Ok((smtp, None));
    }
    let native = native.context("missing recipient address")?;
    if address_type.is_some_and(|value| value.eq_ignore_ascii_case("SMTP")) && smtp_valid(native) {
        return Ok((native, None));
    }
    let mapping = directory
        .resolve(native)
        .context("recipient has no unambiguous SMTP address; refusing to omit it")?;
    Ok((&mapping.smtp, Some(mapping)))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn real_contacts_use_the_store_named_property_map() {
        // doc/requirements.md: preload Contacts before mail; contact LIDs must
        // resolve through PSETID_Address rather than being used as property IDs.
        let source =
            File::open(Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/mail.pst"))
                .unwrap();
        let pst = open(&source).unwrap();
        let ids = contact_property_ids(&pst).unwrap();
        assert_eq!(ids.get(&0x8083), Some(&0x8027));
        assert_eq!(ids.get(&0x8084), Some(&0x808E));
        let directory = exchange_directory(&pst);
        assert!(directory.failures.is_empty(), "{:?}", directory.failures);
        assert_eq!(directory.contact_items, 1);
        assert_eq!(directory.contacts.len(), 1);
        let contact = &directory.contacts[0];
        assert_eq!(contact.item, 2097796);
        assert_eq!(contact.smtp.as_deref(), Some("saqib.razzaq@xp.local"));
        assert_eq!(contact.address, contact.smtp);
        assert_eq!(contact.property, 0x8027);
    }

    #[test]
    fn contacts_resolve_all_slots_and_entry_ids_without_guessing() {
        // doc/requirements.md: all three contact slots, explicit EntryID aliases,
        // recipient fallback, stable ambiguity, and original-address evidence.
        let string = str::to_owned;
        let names = ["One", "Two", "Three"];
        let dns = names.map(|name| format!("/O=Example/OU=Group/CN=Recipients/CN={name}"));
        let mut ids = HashMap::new();
        let mut properties = HashMap::new();
        for (index, base) in EMAIL_SLOTS.into_iter().enumerate() {
            for offset in 2..=5 {
                ids.insert(base + offset, 0x9000 + index as u16 * 16 + offset as u16);
            }
            properties.insert(
                ids[&(base + 2)],
                string(if index == 2 { "SMTP" } else { "EX" }),
            );
            properties.insert(
                ids[&(base + 3)],
                string(if index == 2 {
                    "three@example.org"
                } else {
                    &dns[index]
                }),
            );
            properties.insert(
                ids[&(base + 4)],
                string(&format!(
                    "{}@example.org",
                    names[index].to_ascii_lowercase()
                )),
            );
        }
        let mut entry = vec![
            0, 0, 0, 0, 0xDC, 0xA7, 0x40, 0xC8, 0xC0, 0x42, 0x10, 0x1A, 0xB4, 0xB9, 8, 0, 0x2B,
            0x2F, 0xE1, 0x82, 1, 0, 0, 0, 0, 0, 0, 0,
        ];
        entry.extend_from_slice(dns[2].as_bytes());
        entry.push(0);
        let mut directory = ExchangeDirectory::default();
        directory
            .read_contact(
                42,
                &ids,
                |id| Ok(properties.get(&id).cloned()),
                |id| (id == ids[&0x80A5]).then_some(entry.as_slice()),
            )
            .unwrap();
        assert_eq!(directory.contacts.len(), 3);
        for (index, dn) in dns.iter().enumerate() {
            let (smtp, mapping) =
                recipient_address(Some("EX"), Some(dn), None, &directory).unwrap();
            assert_eq!(
                smtp,
                format!("{}@example.org", names[index].to_ascii_lowercase())
            );
            assert_eq!(mapping.unwrap().item, 42);
            let mut evidence = Vec::new();
            let value = format!("\"Name, {}\" <{dn}>, other@example.org", names[index]);
            assert_eq!(
                resolve_address_header("Cc", &value, &directory, &mut evidence).unwrap(),
                Some(format!(
                    "\"Name, {}\" <{smtp}>, other@example.org",
                    names[index]
                ))
            );
            let evidence = String::from_utf8(evidence).unwrap();
            assert!(evidence.contains(dn));
            assert!(evidence.contains("field=Cc; item=42; property=0x90"));
            let name_only = format!("\"<{dn}>\" <other@example.org> (<{dn}>)");
            assert!(
                resolve_address_header("To", &name_only, &directory, &mut Vec::new())
                    .unwrap()
                    .is_none()
            );
        }
        // Existing SMTP addresses take precedence over directory fallbacks.
        assert_eq!(
            recipient_address(
                Some("EX"),
                Some(&dns[0]),
                Some("direct@example.org"),
                &directory
            )
            .unwrap()
            .0,
            "direct@example.org"
        );
        // A conflicting contact must never silently replace the first mapping.
        properties.insert(ids[&0x8084], string("different@example.org"));
        directory
            .read_contact(
                43,
                &ids,
                |id| Ok(properties.get(&id).cloned()),
                |id| (id == ids[&0x80A5]).then_some(entry.as_slice()),
            )
            .unwrap();
        assert!(recipient_address(Some("EX"), Some(&dns[0]), None, &directory).is_err());
        assert!(directory.resolve("Someone with a similar name").is_none());
        // Truncated, foreign, unterminated, and non-mail EntryIDs are not aliases.
        for length in 0..entry.len() {
            assert!(address_book_dn(&entry[..length]).is_none());
        }
        entry[4] ^= 1;
        assert!(address_book_dn(&entry).is_none());
        entry[4] ^= 1;
        entry[25] = 1;
        assert!(address_book_dn(&entry).is_none());
    }

    #[test]
    fn exchange_mapping_requires_explicit_unambiguous_smtp_evidence() {
        // doc/requirements.md: no guessed SMTP identities; case-insensitive DN
        // matching with persistent ambiguity and source-item provenance.
        let dn = "/O=Example/OU=Group/CN=Recipients/CN=Sender";
        let mut directory = ExchangeDirectory::default();
        directory.remember(Some(dn.into()), None, 1, SENDER_SMTP);
        assert!(directory.resolve(dn).is_none());
        for invalid in [
            "not-an-address",
            "@example.org",
            "user@",
            "bad@example.org\r\nBcc: injected@example.org",
            "Name <user@example.org>",
        ] {
            directory.remember(Some(dn.into()), Some(invalid.into()), 1, SENDER_SMTP);
            assert!(directory.resolve(dn).is_none());
        }
        directory.remember(
            Some(dn.to_ascii_lowercase()),
            Some("sender@example.org".into()),
            42,
            RECIPIENT_SMTP,
        );
        let found = directory.resolve(dn).unwrap();
        assert_eq!(found.smtp, "sender@example.org");
        assert_eq!((found.item, found.property), (42, RECIPIENT_SMTP));
        directory.remember(
            Some(dn.into()),
            Some("sender@example.org".into()),
            43,
            SENDER_SMTP,
        );
        assert_eq!(directory.resolve(dn).unwrap().item, 42);
        directory.remember(
            Some(dn.into()),
            Some("different@example.org".into()),
            44,
            SENDER_SMTP,
        );
        assert!(directory.resolve(dn).is_none());
        directory.remember(
            Some(dn.into()),
            Some("sender@example.org".into()),
            45,
            SENDER_SMTP,
        );
        assert!(directory.resolve(dn).is_none());
    }

    #[test]
    fn subjects_decode_to_readable_text_and_keep_long_text_without_injection() {
        // doc/PST_IMPORTER.md: ASCII/Unicode and encoded-word subjects remain
        // readable, while folding round-trips and decoded injection is rejected.
        for (input, expected) in [
            ("\u{1}\u{5}Re: Research", "Re: Research"),
            ("\u{1}\u{4}Fw: Research", "Fw: Research"),
            ("\u{1}\u{1}Plain subject", "Plain subject"),
            (
                "Subject with trailing space ",
                "Subject with trailing space ",
            ),
            (
                "=?UTF-8?B?SGVsbG8=?= =?UTF-8?Q?_=E2=80=94_world?=",
                "Hello — world",
            ),
            (
                "The classroom is locked and I can’t get in",
                "The classroom is locked and I can’t get in",
            ),
        ] {
            let mut output = Vec::new();
            subject_header(input, &mut output).unwrap();
            assert_eq!(
                String::from_utf8(output).unwrap(),
                format!("Subject: {expected}\r\n")
            );
        }
        for value in [
            "long subject ".repeat(80).trim().to_owned(),
            "é".repeat(600),
        ] {
            let mut output = Vec::new();
            subject_header(&value, &mut output).unwrap();
            assert!(output.split(|b| *b == b'\n').all(|line| line.len() <= 999));
            assert_eq!(
                mailparse::parse_header(&output).unwrap().0.get_value(),
                value
            );
        }
        for value in [
            "hello\r\nBcc: injected@example.org",
            "=?UTF-8?B?SGkNCkJjYzogaW5qZWN0ZWQ=?=",
        ] {
            assert!(subject_header(value, &mut Vec::new()).is_err());
        }
    }

    #[test]
    fn failed_headers_keep_invalid_values_and_folds_without_body_bytes() {
        // doc/requirements.md: diagnostic headers preserve the actual failed
        // reconstruction, not the synthetic diagnostic wrapper or body.
        let headers = b"From: /O=EXCHANGELABS/OU=GROUP\r\nTo: first@example.org,\r\n second@example.org\r\n\r\n";
        let mut spool = tempfile::tempfile().unwrap();
        spool
            .write_all(b"From pst-importer Thu Jan  1 00:00:00 1970\n")
            .unwrap();
        spool.write_all(headers).unwrap();
        spool.write_all(b"body must not be included").unwrap();
        assert_eq!(
            reconstructed_headers(&mut spool).unwrap().as_deref(),
            Some(headers.as_slice())
        );
        spool.set_len(0).unwrap();
        spool.rewind().unwrap();
        spool
            .write_all(b"From envelope\nFrom: partial\r\n")
            .unwrap();
        assert!(reconstructed_headers(&mut spool).unwrap().is_none());
    }

    #[test]
    fn windows_1256_mime_preserves_bytes_and_decodes_arabic() {
        // doc/requirements.md: CP1256 bodies retain their source bytes and
        // declare windows-1256 so consumers can decode them correctly.
        let bytes = b"\xd3\xe1\xc7\xe3"; // سلام in Windows-1256
        let codepage = PropertyValue::Integer32(1256);
        let charset = byte_charset(Some(&codepage), bytes).unwrap();
        let mut output = Vec::new();
        base64_part(
            &mut output,
            "test-boundary",
            &format!("text/plain; charset={charset}"),
            None,
            None,
            bytes,
        )
        .unwrap();
        let part = mailparse::parse_mail(&output[b"--test-boundary\r\n".len()..]).unwrap();
        assert_eq!(part.get_body_raw().unwrap(), bytes);
        assert_eq!(part.get_body().unwrap(), "سلام");
        assert!(byte_charset(Some(&PropertyValue::Integer32(99999)), bytes).is_err());
    }

    #[test]
    fn meeting_classes_are_excluded_without_hiding_unknown_mail_classes() {
        // doc/requirements.md: meeting requests/responses are non-mail, not errors.
        for class in [
            "IPM.Schedule.Meeting.Request",
            "IPM.Schedule.Meeting.Resp.Pos",
            "IPM.Schedule.Meeting.Resp.Neg",
            "ipm.schedule.meeting.canceled",
        ] {
            assert!(excluded_class(class));
        }
        for class in [
            "IPM.Note",
            "IPM.Note.Custom",
            "IPM.Schedule.Meetingish",
            "unknown",
        ] {
            assert!(!excluded_class(class));
        }
    }

    #[test]
    fn library_errors_keep_their_origin_through_context() {
        // doc/requirements.md: sub-node failures must explicitly identify the library.
        let error =
            anyhow::Error::from(outlook_pst::messaging::MessagingError::MessageSubNodeTreeNotFound)
                .context("open message");
        assert_eq!(error_kind(&error), "LIBRARY ERROR (outlook-pst 1.2.0)");
        let wrapped = anyhow::Error::from(io::Error::from(
            outlook_pst::messaging::MessagingError::MessageSubNodeTreeNotFound,
        ))
        .context("open message");
        assert_eq!(error_kind(&wrapped), "LIBRARY ERROR (outlook-pst 1.2.0)");
        assert_eq!(
            error_kind(&anyhow::anyhow!("invalid From address list")),
            "ERROR"
        );
    }

    #[test]
    fn invalid_export_preserves_real_property_bytes() {
        // doc/PST_IMPORTER.md: diagnostic export preserves even unrecognized
        // encodings; Unicode evidence is the original UTF-16LE buffer.
        let file =
            File::open(Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/mail.pst"))
                .unwrap();
        let store = open(&file).unwrap();
        let message = store.message(2097316).unwrap();
        let uri = Url::parse("file:///fixture.pst#item=2097316").unwrap();
        let mut output = Vec::new();
        export_invalid(
            message.message(),
            &uri,
            2097316,
            &anyhow::anyhow!("unknown body code page"),
            None,
            &mut output,
        )
        .unwrap();
        let start = output.iter().position(|b| *b == b'\n').unwrap() + 1;
        let parsed = mailparse::parse_mail(&output[start..]).unwrap();
        for (id, name) in [(SUBJECT, "subject"), (BODY, "body")] {
            let value = message.message().properties().get(id).unwrap();
            let expected = match value {
                PropertyValue::Unicode(v) => v
                    .buffer()
                    .iter()
                    .flat_map(|c| c.to_le_bytes())
                    .collect::<Vec<_>>(),
                _ => raw(value).unwrap().into_owned(),
            };
            let part = parsed
                .subparts
                .iter()
                .find(|part| {
                    part.get_content_disposition()
                        .params
                        .get("filename")
                        .is_some_and(|filename| filename == name)
                })
                .unwrap();
            assert_eq!(part.get_body_raw().unwrap(), expected);
        }
    }
}
