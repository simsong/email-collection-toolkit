# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""Read-only PST/OST conversion; outputs standard MCT mboxrd without archive access."""
from __future__ import annotations

import base64
import hashlib
import io
import os
import re
import time
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email import policy
from email.message import Message
from email.parser import Parser
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import BinaryIO, TextIO

import pypff
from pydantic import BaseModel, ConfigDict, Field


# MAPI property identifiers, not offsets into a particular PST/OST version.
MESSAGE_CLASS = 0x001A
HAS_ATTACH = 0x0E1B
MESSAGE_FLAGS = 0x0E07
DISPLAY_TO, DISPLAY_CC, DISPLAY_BCC = 0x0E04, 0x0E03, 0x0E02
SENDER_EMAIL, INTERNET_MESSAGE_ID = 0x5D01, 0x1035
SENDER_ADDRESS_TYPE, SENDER_ADDRESS = 0x0C1E, 0x0C1F
INTERNET_CPID = 0x3FDE
ATTACH_METHOD, ATTACH_FILENAME, ATTACH_SHORT_FILENAME = 0x3705, 0x3707, 0x3704
ATTACH_MIME, ATTACH_CONTENT_ID = 0x370E, 0x3712
EXCLUDED_HEADERS = frozenset({"content-type", "content-transfer-encoding", "content-length", "mime-version",
                               "x-imported-uri", "x-importer-name", "x-importer-version"})


class PffSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timeout_seconds: float = Field(default=60, gt=0, allow_inf_nan=False)
    max_message_bytes: int = Field(default=64 * 1024 * 1024, gt=0)
    max_folder_depth: int = Field(default=64, gt=0)
    offset: int = Field(default=0, ge=0)
    limit: int | None = Field(default=None, ge=0)


class PffReceipt(BaseModel):
    source: Path
    source_sha256: str
    libpff_version: str
    process_id: int
    content_type: int | None = None
    folders: int = 0
    encountered: int = 0
    selected: int = 0
    emitted: int = 0
    non_mail: int = 0
    errors: int = 0
    complete: bool = False
    failure: str | None = None
    reconstructed_mime: bool = True


class PffDiagnostic(BaseModel):
    folder: tuple[str, ...]
    item: int | None = None
    detail: str


class _Rendered(BaseModel):
    raw: bytes
    problems: tuple[str, ...]
    date: datetime


def _property(item: pypff.item, identifier: int) -> pypff.record_entry | None:
    if item.number_of_record_sets:
        record = item.get_record_set(0)
        for index in range(record.number_of_entries):
            entry = record.get_entry(index)
            if entry.entry_type == identifier:
                return entry
    return None


def _string(item: pypff.item, identifier: int) -> str | None:
    entry = _property(item, identifier)
    return None if entry is None else entry.data_as_string


def _integer(item: pypff.item, identifier: int) -> int | None:
    entry = _property(item, identifier)
    return None if entry is None else entry.data_as_integer


def _safe(value: str) -> str:
    return " ".join(value.splitlines()).strip()


def normalized_header(value: str) -> str:
    """Match the Rust reconstruction's unfolded, whitespace-normalized fields."""
    return " ".join(value.split())


def message_id_valid(value: str) -> bool:
    if not (value.isascii() and value.startswith("<") and value.endswith(">")):
        return False
    value = value[1:-1]
    return (value.count("@") == 1 and all(value.partition("@")[index] for index in (0, 2))
            and all(not (character.isspace() or ord(character) < 32 or character in "<>")
                    for character in value))


def normalized_subject(value: str) -> str:
    if value.startswith("\x01"):
        if len(value) < 2:
            raise ValueError("missing PST subject prefix length")
        value = value[2:]
    if any(character != "\t" and ord(character) < 32 for character in value):
        raise ValueError("control character in Subject")
    decoded = str(make_header(decode_header(value)))
    if any(character != "\t" and ord(character) < 32 for character in decoded):
        raise ValueError("decoded control character in Subject")
    return decoded


def message_date(message: pypff.message, headers: Message) -> datetime:
    source = headers.get("Date")
    if source:
        try:
            date = parsedate_to_datetime(source)
            return date.replace(tzinfo=timezone.utc) if date.tzinfo is None else date
        except (TypeError, ValueError):
            pass
    date = message.client_submit_time or message.delivery_time
    if date:
        return date.replace(tzinfo=timezone.utc) if date.tzinfo is None else date
    return datetime.fromtimestamp(0, timezone.utc)


def mbox_date(date: datetime) -> str:
    date = date.astimezone(timezone.utc)
    weekdays = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    return f"{weekdays[date.weekday()]} {months[date.month - 1]} {date.day:2d} {date:%H:%M:%S} {date.year:04d}"


def body_charset(codepage: int | None, body: bytes) -> str:
    if codepage == 65001:
        return "utf-8"
    if codepage == 1252:
        return "windows-1252"
    if codepage == 1256:
        return "windows-1256"
    if codepage == 20127:
        return "us-ascii"
    if codepage == 28591:
        return "iso-8859-1"
    return "us-ascii" if body.isascii() else "windows-1252"


def rfc2822_date(date: datetime) -> str:
    offset = date.utcoffset() or timezone.utc.utcoffset(None)
    seconds = int(offset.total_seconds())
    sign = "+" if seconds >= 0 else "-"
    hours, minutes = divmod(abs(seconds) // 60, 60)
    weekdays = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    return f"{weekdays[date.weekday()]}, {date.day} {months[date.month - 1]} {date:%Y %H:%M:%S} {sign}{hours:02d}{minutes:02d}"


def is_mail_class(value: str) -> bool:
    """Skip only recognized non-mail classes; unknown classes must remain incomplete."""
    value = value.upper()
    for prefix in ("IPM.NOTE", "REPORT.IPM.NOTE"):
        if value == prefix or value.startswith(prefix + "."):
            return True
    for prefix in ("IPM.CONTACT", "IPM.DISTLIST", "IPM.APPOINTMENT", "IPM.TASK", "IPM.ACTIVITY",
                   "IPM.STICKYNOTE", "IPM.SCHEDULE.MEETING", "IPM.CONFIGURATION",
                   "IPM.MICROSOFT.SCHEDULEDATA.FREEBUSY"):
        if value == prefix or value.startswith(prefix + "."):
            return False
    raise ValueError(f"unsupported MAPI message class: {value}")


def _sender(message: pypff.message) -> str | None:
    address = _string(message, SENDER_EMAIL)
    if not address and (_string(message, SENDER_ADDRESS_TYPE) or "").upper() == "SMTP":
        address = _string(message, SENDER_ADDRESS)
    return _safe(address) if address else "unknown@invalid.invalid"


def header(out: "_MimeWriter", name: str, value: str, separator: str = ": ") -> None:
    """Write an unfolded or safely folded UTF-8 header without RFC 2047 rewriting."""
    if (not name.isascii() or not name or any(ord(character) < 33 or ord(character) > 126
                                             or character == ":" for character in name)):
        raise ValueError("invalid header name")
    if any(character in "\r\n" and not value[index + 1:index + 2].isspace()
           for index, character in enumerate(value)):
        raise ValueError("invalid header folding")
    if any(ord(character) < 32 and character not in "\r\n\t" for character in value):
        raise ValueError("control character in header")
    out.write(f"{name}{separator}{value}\r\n".encode("utf-8"))


def transport_fields(value: str) -> list[tuple[str, str]]:
    """Retain physical transport-header folding without email-package rewriting."""
    fields: list[tuple[str, list[str]]] = []
    for line in re.split(r"\r\n|\n", value):
        if line[:1].isspace() and fields:
            fields[-1][1].append(line)
        elif ":" in line:
            name, field_value = line.split(":", 1)
            fields.append((name, [field_value]))
    return [(name, "\r\n".join(lines)) for name, lines in fields]


def transport_header(out: "_MimeWriter", name: str, value: str) -> None:
    """Copy a safe source header, including the space following its colon."""
    header(out, name, value.removeprefix(" "))


class _MimeWriter:
    """Bound output per message and stream native attachment reads in base64 chunks."""

    def __init__(self, settings: PffSettings, deadline: float) -> None:
        self.settings = settings
        self.deadline = deadline
        self.output = io.BytesIO()

    def write(self, data: bytes) -> None:
        if time.monotonic() >= self.deadline:
            raise TimeoutError("libpff extraction deadline exceeded")
        if self.output.tell() + len(data) > self.settings.max_message_bytes:
            raise ValueError("libpff reconstructed message exceeds max_message_bytes")
        self.output.write(data)

    def part(self, mime_type: str, stream: BinaryIO | pypff.attachment, *, filename: str | None = None,
             content_id: str | None = None) -> None:
        mime_type = "application/octet-stream" if mime_type.lower().startswith("multipart/") else mime_type
        if "\r" in mime_type or "\n" in mime_type:
            raise ValueError("invalid attachment media type")
        self.write(f"Content-Type: {mime_type}\r\nContent-Transfer-Encoding: base64\r\n".encode("ascii"))
        if content_id:
            if (not content_id.isascii() or any(character in content_id for character in "\r\n<>")
                    or len(content_id) >= 980):
                raise ValueError("invalid attachment Content-ID")
            self.write(f"Content-ID: <{content_id}>\r\n".encode("ascii"))
        if filename:
            name = _safe(filename)
            encoded_name = name.encode("utf-8")
            self.write(f"Content-Disposition: {'inline' if content_id else 'attachment'};\r\n filename*0*=UTF-8''".encode("ascii"))
            for index, start in enumerate(range(0, len(encoded_name), 18)):
                if index:
                    self.write(f";\r\n filename*{index}*=".encode("ascii"))
                self.write(b"".join(f"%{byte:02X}".encode("ascii") for byte in encoded_name[start:start + 18]))
            self.write(b"\r\n")
        self.write(b"\r\n")
        size = 0
        while True:
            chunk = stream.read_buffer(57 * 1024) if isinstance(stream, pypff.attachment) else stream.read(57 * 1024)
            if not chunk:
                break
            size += len(chunk)
            self.write(base64.encodebytes(chunk).replace(b"\n", b"\r\n"))
        self.write(b"\r\n")
        if isinstance(stream, pypff.attachment) and size != stream.size:
            raise ValueError(f"libpff attachment length mismatch: expected {stream.size}, read {size}")


def _render(message: pypff.message, uri: str, folder: tuple[str, ...], item: int,
            writer: _MimeWriter) -> _Rendered:
    """Reconstruct deterministically; do not represent MAPI reconstruction as source RFC bytes."""
    transport = message.transport_headers or ""
    parsed = Parser(policy=policy.compat32).parsestr(transport, headersonly=True)
    date = message_date(message, parsed)
    primary = {
        "Date": rfc2822_date(date),
        "From": normalized_header(parsed.get("From") or _sender(message) or "unknown@invalid.invalid"),
    }
    subject = message.subject
    if subject is None:
        subject = parsed.get("Subject")
    if subject is not None:
        primary["Subject"] = normalized_subject(subject)
    original_id = parsed.get("Message-ID") or _string(message, INTERNET_MESSAGE_ID)
    if original_id and message_id_valid(normalized_header(original_id)):
        primary["Message-ID"] = normalized_header(original_id)
    writer.write(f"X-Imported-URI: {uri}\r\nX-Importer-Name: libpff\r\nX-Importer-Version: {pypff.get_version()}\r\n".encode("ascii"))
    header(writer, "X-Mailarchiver-Folder", _safe("/".join(folder)))
    for name in ("Date", "From", "Subject", "Message-ID"):
        if name in primary:
            header(writer, name, primary[name])
    for name, value in transport_fields(transport):
        lower = name.lower()
        if lower not in EXCLUDED_HEADERS and lower not in {"date", "from", "subject", "message-id"} and not lower.startswith("content-"):
            transport_header(writer, name, value)
    for name, value in (("To", _string(message, DISPLAY_TO)), ("Cc", _string(message, DISPLAY_CC)),
                        ("Bcc", _string(message, DISPLAY_BCC))):
        if parsed.get(name) is None and value:
            header(writer, name, _safe(value))
    boundary = f"=_mct_pst_{item:08x}"
    writer.write(f"MIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=\"{boundary}\"\r\n\r\n".encode("ascii"))
    separator = f"--{boundary}\r\n".encode()
    bodies = 0
    codepage = _integer(message, INTERNET_CPID)
    for mime_type, data in (("text/plain", message.plain_text_body), ("text/html", message.html_body),
                            ("application/rtf", message.rtf_body)):
        if data:
            bodies += 1
            writer.write(separator)
            if mime_type.startswith("text/"):
                mime_type = f"{mime_type}; charset={body_charset(codepage, data)}"
            writer.part(mime_type, io.BytesIO(data))
    if not bodies:
        writer.write(separator)
        writer.part("text/plain; charset=utf-8", io.BytesIO())
    if transport:
        writer.write(separator)
        writer.part("application/octet-stream", io.BytesIO(transport.encode("utf-8")), filename="original-transport-headers.txt")
    flag = _property(message, HAS_ATTACH)
    # libpff 20231205 can raise looking up the absent table on attachment-free messages.
    flags = _integer(message, MESSAGE_FLAGS)
    has_attachments = flag.data_as_boolean if flag is not None else flags is None or bool(flags & 0x10)
    count = message.number_of_attachments if has_attachments else 0
    problems: list[str] = []
    for index in range(count):
        position = writer.output.tell()
        try:
            attachment = message.get_attachment(index)
            method = _integer(attachment, ATTACH_METHOD)
            if method not in (1, 6):
                raise ValueError(f"libpff Python binding cannot reconstruct attachment method {method} at index {index}")
            if attachment.size > writer.settings.max_message_bytes:
                raise ValueError("libpff attachment exceeds max_message_bytes")
            writer.write(separator)
            writer.part(_safe(_string(attachment, ATTACH_MIME) or "application/octet-stream"), attachment,
                        filename=_string(attachment, ATTACH_FILENAME) or _string(attachment, ATTACH_SHORT_FILENAME) or f"attachment-{index}",
                        content_id=_string(attachment, ATTACH_CONTENT_ID))
        except TimeoutError:
            raise
        except (OSError, ValueError) as error:
            writer.output.seek(position)
            writer.output.truncate()
            problems.append(str(error)[:4096])
    writer.write(f"--{boundary}--\r\n".encode())
    raw = writer.output.getvalue()
    if problems:
        raw = raw.replace(b"\r\n\r\n", b"\r\nX-Mailarchiver-Extraction-Incomplete: attachments; see libpff receipt\r\n\r\n", 1)
        if len(raw) > writer.settings.max_message_bytes:
            raise ValueError("libpff reconstructed message exceeds max_message_bytes")
    return _Rendered(raw=raw, problems=tuple(problems), date=date)


def file_hash(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def write_record(raw: bytes, date: datetime, output: BinaryIO) -> None:
    output.write(f"From pff-converter {mbox_date(date)}\n".encode("ascii"))
    output.write(re.sub(br"(?m)^(>*From )", br">\1", raw))
    output.write(b"\n")
    output.flush()


def convert(source: Path, output: BinaryIO, diagnostics: TextIO, settings: PffSettings) -> PffReceipt:
    receipt = PffReceipt(source=source, source_sha256=file_hash(source),
                         libpff_version=pypff.get_version(), process_id=os.getpid())
    deadline = time.monotonic() + settings.timeout_seconds
    store = pypff.file()
    opened = False
    try:
        with source.open("rb") as original:
            store.open_file_object(original, "r")
            opened = True
            receipt.content_type = store.content_type
            first = store.root_folder
            if first is None:
                raise ValueError("libpff source has no root folder")
            pending: list[tuple[pypff.folder, tuple[str, ...]]] = [(first, ())]
            visited: set[int] = set()
            selection_complete = False
            while pending and not selection_complete:
                folder, parent = pending.pop()
                path = (*parent, folder.name or "Root")
                if time.monotonic() >= deadline:
                    raise TimeoutError("libpff extraction deadline exceeded")
                if len(path) > settings.max_folder_depth or folder.identifier in visited:
                    raise ValueError("libpff folder depth limit or cycle")
                visited.add(folder.identifier)
                if folder.identifier & 31 == 3:  # Search folders reference messages held elsewhere.
                    continue
                receipt.folders += 1
                messages = [folder.get_sub_message(index) for index in range(folder.number_of_sub_messages)]
                messages.sort(key=lambda message: message.identifier)
                for message in messages:
                    if settings.limit is not None and receipt.selected >= settings.limit:
                        selection_complete = True
                        break
                    if time.monotonic() >= deadline:
                        raise TimeoutError("libpff extraction deadline exceeded")
                    receipt.encountered += 1
                    if receipt.encountered <= settings.offset:
                        continue
                    receipt.selected += 1
                    node = None
                    try:
                        node = message.identifier
                        message_class = _string(message, MESSAGE_CLASS) or "IPM.Note"
                        if not is_mail_class(message_class):
                            receipt.non_mail += 1
                            continue
                        uri = f"{source.as_uri()}#item={node}"
                        rendered = _render(message, uri, path, node, _MimeWriter(settings, deadline))
                    except TimeoutError:
                        raise
                    except (OSError, ValueError) as error:
                        receipt.errors += 1
                        diagnostics.write(PffDiagnostic(folder=path, item=node, detail=str(error)[:4096]).model_dump_json() + "\n")
                        continue
                    if rendered.problems:
                        receipt.errors += 1
                        for detail in rendered.problems:
                            diagnostics.write(PffDiagnostic(folder=path, item=node, detail=detail).model_dump_json() + "\n")
                        continue
                    write_record(rendered.raw, rendered.date, output)
                    receipt.emitted += 1
                children = [folder.get_sub_folder(index) for index in range(folder.number_of_sub_folders)]
                children.sort(key=lambda child: child.identifier, reverse=True)
                pending.extend((child, path) for child in children)
            if file_hash(source) != receipt.source_sha256:
                raise ValueError("libpff source changed during extraction")
            if receipt.errors:
                receipt.failure = f"libpff extraction incomplete ({receipt.errors} items)"
            else:
                receipt.complete = True
    except Exception as error:
        receipt.failure = str(error)[:4096]
    finally:
        if opened:
            store.close()
    return receipt
