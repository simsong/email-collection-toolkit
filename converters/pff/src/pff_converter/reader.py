# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""Read-only PST/OST conversion; outputs standard MCT mboxrd without archive access."""
from __future__ import annotations

import base64
import hashlib
import io
import os
import re
import time
from email import policy
from email.message import EmailMessage, Message
from email.parser import Parser
from email.utils import format_datetime, formataddr
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
ATTACH_METHOD, ATTACH_FILENAME, ATTACH_SHORT_FILENAME = 0x3705, 0x3707, 0x3704
ATTACH_MIME, ATTACH_CONTENT_ID = 0x370E, 0x3712
EXCLUDED_HEADERS = frozenset({"content-type", "content-transfer-encoding", "content-length", "mime-version",
                               "x-imported-uri", "x-importer-name", "x-importer-version"})


class PffSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timeout_seconds: float = Field(default=60, gt=0, allow_inf_nan=False)
    max_message_bytes: int = Field(default=64 * 1024 * 1024, gt=0)
    max_folder_depth: int = Field(default=64, gt=0)


class PffReceipt(BaseModel):
    source: Path
    source_sha256: str
    libpff_version: str
    process_id: int
    content_type: int | None = None
    folders: int = 0
    encountered: int = 0
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


def is_mail_class(value: str) -> bool:
    """Skip only recognized non-mail classes; unknown classes must remain incomplete."""
    value = value.upper()
    for prefix in ("IPM.NOTE", "REPORT.IPM.NOTE", "IPM.SCHEDULE.MEETING"):
        if value == prefix or value.startswith(prefix + "."):
            return True
    for prefix in ("IPM.CONTACT", "IPM.DISTLIST", "IPM.APPOINTMENT", "IPM.TASK", "IPM.ACTIVITY",
                   "IPM.STICKYNOTE", "IPM.CONFIGURATION", "IPM.MICROSOFT.SCHEDULEDATA.FREEBUSY"):
        if value == prefix or value.startswith(prefix + "."):
            return False
    raise ValueError(f"unsupported MAPI message class: {value}")


def _sender(message: pypff.message) -> str | None:
    address = _string(message, SENDER_EMAIL)
    if not address and (_string(message, SENDER_ADDRESS_TYPE) or "").upper() == "SMTP":
        address = _string(message, SENDER_ADDRESS)
    return formataddr((_safe(message.sender_name or ""), _safe(address))) if address else message.sender_name


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
        header = EmailMessage(policy=policy.SMTP)
        header["Content-Type"] = mime_type
        header["Content-Transfer-Encoding"] = "base64"
        if filename:
            header.add_header("Content-Disposition", "attachment", filename=_safe(filename))
        if content_id:
            header["Content-ID"] = _safe(content_id)
        self.write(header.as_bytes())
        size = 0
        while True:
            chunk = stream.read_buffer(57 * 1024) if isinstance(stream, pypff.attachment) else stream.read(57 * 1024)
            if not chunk:
                break
            size += len(chunk)
            self.write(base64.encodebytes(chunk).replace(b"\n", b"\r\n"))
        if isinstance(stream, pypff.attachment) and size != stream.size:
            raise ValueError(f"libpff attachment length mismatch: expected {stream.size}, read {size}")


def _render(message: pypff.message, uri: str, folder: tuple[str, ...], writer: _MimeWriter) -> _Rendered:
    """Reconstruct deterministically; do not represent MAPI reconstruction as source RFC bytes."""
    headers = Message(policy=policy.compat32.clone(linesep="\r\n"))
    headers["X-Mailarchiver-Folder"] = _safe("/".join(folder))
    transport = message.transport_headers or ""
    parsed = Parser(policy=policy.compat32).parsestr(transport, headersonly=True)
    for name, value in parsed.raw_items():
        if name.lower() not in EXCLUDED_HEADERS:
            # Original header text is also retained as evidence, including malformed fields.
            headers[name] = _safe(value)
    for name, value in (("Subject", message.subject), ("From", _sender(message)),
                        ("To", _string(message, DISPLAY_TO)), ("Cc", _string(message, DISPLAY_CC)),
                        ("Bcc", _string(message, DISPLAY_BCC)), ("Message-ID", _string(message, INTERNET_MESSAGE_ID))):
        if name not in headers and value:
            headers[name] = _safe(value)
    if "Date" not in headers:
        date = message.client_submit_time or message.delivery_time
        if date:
            headers["Date"] = format_datetime(date)
    boundary = "mailarchiver-libpff-" + hashlib.sha256(uri.encode()).hexdigest()
    headers["MIME-Version"] = "1.0"
    headers.set_type("multipart/mixed")
    headers.set_boundary(boundary)
    # The importer contract requires these three ASCII fields first and unfolded.
    writer.write(f"X-Imported-URI: {uri}\r\nX-Importer-Name: libpff\r\nX-Importer-Version: {pypff.get_version()}\r\n".encode("ascii"))
    writer.write(headers.as_bytes().split(b"\r\n\r\n", 1)[0] + b"\r\n\r\n")
    separator = f"--{boundary}\r\n".encode()
    bodies = 0
    for mime_type, data in (("text/plain; charset=utf-8", message.plain_text_body),
                            ("text/html", message.html_body), ("application/rtf", message.rtf_body)):
        if data:
            bodies += 1
            writer.write(separator)
            writer.part(mime_type, io.BytesIO(data))
    if not bodies:
        writer.write(separator)
        writer.part("text/plain; charset=utf-8", io.BytesIO())
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
    if transport:
        writer.write(separator)
        writer.part("text/plain; charset=utf-8", io.BytesIO(transport.encode("utf-8")), filename="original-transport-headers.txt")
    writer.write(f"--{boundary}--\r\n".encode())
    raw = writer.output.getvalue()
    if problems:
        raw = raw.replace(b"\r\n\r\n", b"\r\nX-Mailarchiver-Extraction-Incomplete: attachments; see libpff receipt\r\n\r\n", 1)
        if len(raw) > writer.settings.max_message_bytes:
            raise ValueError("libpff reconstructed message exceeds max_message_bytes")
    return _Rendered(raw=raw, problems=tuple(problems))


def file_hash(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def write_record(raw: bytes, output: BinaryIO) -> None:
    output.write(b"From pff-converter Thu Jan  1 00:00:00 1970\n")
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
            while pending:
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
                for index in range(folder.number_of_sub_messages):
                    if time.monotonic() >= deadline:
                        raise TimeoutError("libpff extraction deadline exceeded")
                    receipt.encountered += 1
                    node = None
                    try:
                        message = folder.get_sub_message(index)
                        node = message.identifier
                        message_class = _string(message, MESSAGE_CLASS) or "IPM.Note"
                        if not is_mail_class(message_class):
                            receipt.non_mail += 1
                            continue
                        uri = f"{source.as_uri()}#libpff/{node}"
                        rendered = _render(message, uri, path, _MimeWriter(settings, deadline))
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
                    write_record(rendered.raw, output)
                    receipt.emitted += 1
                for index in reversed(range(folder.number_of_sub_folders)):
                    pending.append((folder.get_sub_folder(index), path))
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
