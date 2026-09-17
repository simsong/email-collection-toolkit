# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""Stream MIME payloads into temporary parts, retaining exact attached-message bytes."""
from __future__ import annotations

import base64
import binascii
import hashlib
import quopri
import re
from email import policy
from email.message import Message
from email.parser import BytesHeaderParser
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4
from collections.abc import Callable

from pydantic import BaseModel

from .api import ContentReference, MIME_TYPE, Scope
from .contracts import ContentMetadata
from ..message import decoded_header
from ..search import IndexedAttachment

CHUNK = 1024 * 1024


class MimePart(BaseModel):
    reference: ContentReference
    content_type: str
    part_path: tuple[int, ...]
    scope: Scope
    metadata: ContentMetadata


class MimeParts(BaseModel):
    parts: list[MimePart]
    attachments: list[IndexedAttachment]
    diagnostics: list[str]


def read_headers(source: BinaryIO) -> Message:
    """Leave the stream at the body; malformed header-looking data stays available."""
    data = bytearray()
    while len(data) < CHUNK:
        position = source.tell()
        line = source.readline(CHUNK)
        if not line or line in (b"\n", b"\r\n"):
            break
        if not line.startswith((b" ", b"\t")) and b":" not in line:
            source.seek(position)
            break
        data.extend(line)
    return BytesHeaderParser(policy=policy.compat32).parsebytes(bytes(data))


def _decoded(source: BinaryIO, destination: Path, transfer: str, check: Callable[[], None]) -> None:
    with destination.open("wb") as target:
        if transfer == "base64":
            pending = b""
            while chunk := source.read(CHUNK):
                check()
                pending += re.sub(rb"[^A-Za-z0-9+/=]", b"", chunk)
                count = len(pending) // 4 * 4
                if count:
                    target.write(base64.b64decode(pending[:count]))
                    pending = pending[count:]
            if pending:
                target.write(base64.b64decode(pending + b"=" * (-len(pending) % 4)))
        elif transfer == "quoted-printable":
            quopri.decode(source, target, header=False)
        else:
            while chunk := source.read(CHUNK):
                check()
                target.write(chunk)


def _children(source: BinaryIO, boundary: bytes, workspace: Path, check: Callable[[], None]) -> list[Path]:
    children: list[Path] = []
    output: BinaryIO | None = None
    at_line_start = True
    try:
        while chunk := source.readline(CHUNK):
            check()
            marker = chunk.rstrip(b"\r\n \t") if at_line_start else b""
            at_line_start = chunk.endswith(b"\n")
            if marker in (b"--" + boundary, b"--" + boundary + b"--"):
                if output is not None:
                    # RFC 2046: the CRLF introducing a delimiter belongs to it.
                    end = output.tell()
                    output.seek(max(0, end - 2))
                    tail = output.read()
                    output.truncate(end - (2 if tail.endswith(b"\r\n") else 1 if tail.endswith(b"\n") else 0))
                    output.close()
                    output = None
                if marker == b"--" + boundary + b"--":
                    break
                path = workspace / str(uuid4())
                children.append(path)
                output = path.open("w+b")
            elif output is not None:
                output.write(chunk)
    finally:
        if output is not None:
            output.close()
    return children


def extract_parts(path: Path, workspace: Path, *, max_depth: int = 40,
                  cancelled: Callable[[], None] | None = None) -> MimeParts:
    result = MimeParts(parts=[], attachments=[], diagnostics=[])
    next_part = 0

    def check() -> None:
        if cancelled is not None:
            cancelled()

    def visit(current: Path, part_path: tuple[int, ...], inherited: Scope, collect: bool = True) -> None:
        nonlocal next_part
        check()
        part_id = next_part
        next_part += 1
        if len(part_path) > max_depth:
            result.diagnostics.append(f"MIME depth limit reached at {part_path}")
            return
        with current.open("rb") as source:
            headers = read_headers(source)
            body_offset = source.tell()
            content_type = headers.get_content_type().lower()
            if MIME_TYPE.fullmatch(content_type) is None or content_type.startswith("application/x-mailarchiver-"):
                content_type = "application/octet-stream"
            supplied_name = headers.get_filename()
            attached = inherited == "attachment" or headers.get_content_disposition() == "attachment" or supplied_name is not None
            scope: Scope = "attachment" if attached or content_type == "message/rfc822" else "body"
            filename = decoded_header(str(supplied_name)) if supplied_name else None
            if collect and (attached or content_type == "message/rfc822"):
                ordinal = len(result.attachments) + 1
                result.attachments.append(IndexedAttachment(attachment_ordinal=ordinal, part_id=part_id,
                    filename=filename or f"attachment-{ordinal}", mime_type=content_type))
            boundary = headers.get_boundary()
            if content_type.startswith("multipart/") and boundary:
                children = _children(source, boundary.encode("ascii", "replace"), workspace, check)
                if children:
                    for index, child in enumerate(children, 1):
                        visit(child, (*part_path, index), scope, collect)
                    return
                result.diagnostics.append(f"missing MIME boundaries at {part_path}")
            if not collect and content_type != "message/rfc822":
                return
            source.seek(body_offset)
            output = workspace / str(uuid4())
            try:
                _decoded(source, output, str(headers.get("Content-Transfer-Encoding", "")).strip().lower(), check)
            except (binascii.Error, ValueError) as error:
                result.diagnostics.append(f"invalid transfer encoding at {part_path}: {error}")
                source.seek(body_offset)
                _decoded(source, output, "", check)
            with output.open("rb") as decoded:
                digest = hashlib.file_digest(decoded, "sha256").hexdigest()
            if collect:
                result.parts.append(MimePart(reference=ContentReference(path=output, sha256=digest),
                    content_type=content_type, part_path=part_path, scope=scope,
                    metadata=ContentMetadata(charset=headers.get_content_charset(), filename=filename, part_id=part_id)))
            if content_type == "message/rfc822":
                # Keep following sibling IDs aligned with the viewer's MIME walk,
                # while child bodies/attachments are processed under the child job.
                visit(output, (*part_path, 1), "attachment", False)
    visit(path, (), "body")
    return result
