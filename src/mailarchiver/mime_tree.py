# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Share stable MIME traversal and original attachment bytes across readers.
# Walk parsed parts iteratively, retaining the IDs used by existing indexes.
# Carry attachment ancestry so child content cannot become its parent's body.
# Locate requested entities in original bytes rather than reserializing headers.
# Cache each container's boundary traversal for linear attachment descriptions.
# Bound decoded traversal and retain shared byte spans instead of subtree copies.
# Decode only their declared transfer encoding, retaining original line endings.
# This is derived reader work; canonical messages are never changed or written.
"""MIME ancestry and byte-preserving, explicitly selected attachment payloads."""
from __future__ import annotations

import base64
import codecs
from collections.abc import Iterator
from email import policy
from email.message import Message
from email.parser import BytesHeaderParser, BytesParser
import quopri
import re

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

PHYSICAL_LINE = re.compile(br"[^\r\n]*(?:\r\n|\r|\n|\Z)")
# Match stdlib header admission, including continuations and Unix From lines.
HEADER_LINE = re.compile(br"(?:From |[\x21-\x39\x3b-\x7e]*:|[ \t])")
HEADER_LIMIT = 1024 * 1024
IDENTITY_ENCODINGS = ("", "7bit", "8bit", "binary")


class MimeLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    max_expanded_bytes: int = Field(default=128 * 1024 * 1024, gt=0, strict=True)
    max_depth: int = Field(default=40, gt=0, strict=True)
    max_parts: int = Field(default=10000, gt=0, strict=True)
    max_child_messages: int = Field(default=1000, gt=0, strict=True)


class MimeReaderLimitError(ValueError):
    """Derived reader work is incomplete; original bytes remain available."""


class MimeReaderBudget(BaseModel):
    limits: MimeLimits = Field(default_factory=MimeLimits)
    decoded_bytes: int = Field(default=0, ge=0, strict=True)
    cached_parts: int = Field(default=0, ge=0, strict=True)

    @property
    def remaining(self) -> int:
        return self.limits.max_expanded_bytes - self.decoded_bytes

    def ensure_bytes(self, size: int) -> None:
        if size > self.remaining:
            raise MimeReaderLimitError("MIME reader byte limit reached; use Raw Source or Save Message")

    def part(self) -> None:
        if self.cached_parts >= self.limits.max_parts:
            raise MimeReaderLimitError("MIME reader part limit reached; use Raw Source or Save Message")
        self.cached_parts += 1


def is_attachment(part: Message) -> bool:
    return (part.get_content_disposition() == "attachment" or part.get_filename() is not None
            or part.get_content_type() == "message/rfc822")


class MimeNode(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)
    part_id: int
    part_path: tuple[int, ...]
    part: Message
    ancestor_attachment: bool

    @property
    def attachment(self) -> bool:
        return self.ancestor_attachment or is_attachment(self.part)


def walk_mime(message: Message, original: OriginalMimeParts | None = None) -> Iterator[MimeNode]:
    """Walk decoded attached-message trees when original bytes are available."""
    pending: list[tuple[Message, tuple[int, ...], bool]] = [(message, (), False)]
    limits = original.budget.limits if original is not None else MimeLimits()
    part_id = 0
    child_messages = 0
    while pending:
        part, path, ancestor = pending.pop()
        if len(path) > limits.max_depth or part_id >= limits.max_parts:
            raise MimeReaderLimitError("MIME reader depth/part limit reached; use Raw Source or Save Message")
        if part.get_content_type() == "message/rfc822":
            child_messages += 1
            if child_messages > limits.max_child_messages:
                raise MimeReaderLimitError("MIME reader child-message limit reached; use Raw Source or Save Message")
        node = MimeNode(part_id=part_id, part_path=path, part=part, ancestor_attachment=ancestor)
        part_id += 1
        yield node
        children = part.get_payload()
        if part.get_content_type() == "message/rfc822" and str(part.get("Content-Transfer-Encoding", "")).strip().lower() in (
                "base64", "quoted-printable", "x-uuencode", "uuencode", "uue", "x-uue"):
            children = []  # Transport text is opaque without successful original-byte decoding.
            if original is not None:
                if len(path) >= limits.max_depth:
                    raise MimeReaderLimitError("MIME reader depth limit reached; use Raw Source or Save Message")
                try:
                    children = [BytesParser(policy=policy.compat32).parsebytes(original.payload(path))]
                except MimeReaderLimitError:
                    raise
                except (ValueError, LookupError, RecursionError):
                    pass  # A bad attachment must not hide its readable parent or siblings.
        if isinstance(children, list):
            if children and (len(path) >= limits.max_depth or part_id + len(pending) + len(children) > limits.max_parts):
                raise MimeReaderLimitError("MIME reader depth/part limit reached; use Raw Source or Save Message")
            for index in range(len(children) - 1, -1, -1):
                child = children[index]
                if isinstance(child, Message):
                    pending.append((child, (*path, index + 1), node.attachment))


def _headers(entity: bytes, start: int, end: int) -> tuple[Message, int]:
    for match in PHYSICAL_LINE.finditer(entity, start, end):
        prefix = entity[match.start():min(match.end(), match.start() + 2)]
        if match.end() - match.start() <= 2 and prefix in (b"\r\n", b"\n", b"\r"):
            end = match.end()
            break
        if match.start() != start and entity.startswith(b"From ", match.start()):
            # A misplaced Unix envelope may be payload without a separator.
            raise ValueError("ambiguous Unix envelope inside MIME headers")
        if not HEADER_LINE.match(entity, match.start(), match.end()):
            # A missing separator starts the body at its first non-header line.
            end = match.start()
            break
        if match.end() - start > HEADER_LIMIT:
            raise MimeReaderLimitError("MIME reader header limit reached; use Raw Source or Save Message")
    return BytesHeaderParser(policy=policy.compat32).parsebytes(entity[start:end]), end


def _uu_payload(payload: bytes, limit: int) -> bytes:
    """Preflight output lengths without retaining one Python object per wire line."""
    wire = bytearray()
    started = False
    expected = 0
    for match in PHYSICAL_LINE.finditer(payload):
        line = match.group().rstrip(b"\r\n")
        if not started:
            if not line.startswith(b"begin"):
                continue
            if re.fullmatch(br"begin [0-7]+ .*", line) is None:
                raise ValueError("invalid uuencode begin line")
            started = True
        else:
            if line.strip() == b"end":
                wire.extend(b"end\n")
                return codecs.decode(bytes(wire), "uu")
            if not line or not 32 <= line[0] <= 96:
                raise ValueError("invalid uuencode data line")
            count = (line[0] - 32) & 63
            required = 1 + 4 * ((count + 2) // 3)
            if len(line) < required or any(not 32 <= char <= 96 for char in line[:required]):
                raise ValueError("truncated uuencode data line")
            expected += count
            if expected > limit:
                raise MimeReaderLimitError("MIME reader byte limit reached; use Raw Source or Save Message")
        # Normalize ASCII transport newlines only, never decoded file bytes.
        wire.extend(line)
        wire.append(10)
    raise ValueError("missing uuencode begin/end line")


def _decoded_payload(headers: Message, payload: bytes, limit: int) -> bytes:
    transfer = str(headers.get("Content-Transfer-Encoding", "")).strip().lower()
    if transfer == "base64":
        return base64.b64decode(re.sub(br"[ \t\r\n]", b"", payload), validate=True)
    if transfer == "quoted-printable":
        if re.search(br"=(?![0-9A-Fa-f]{2}|\r?\n)", payload):
            raise ValueError("invalid attached-message quoted-printable data")
        return quopri.decodestring(payload)
    if transfer in ("x-uuencode", "uuencode", "uue", "x-uue"):
        return _uu_payload(payload, limit)
    if transfer not in IDENTITY_ENCODINGS:
        raise ValueError(f"unsupported attached-message transfer encoding: {transfer}")
    return payload


def decoded_leaf_payload(part: Message) -> bytes | str | None:
    """Keep best-effort body decoding without uuencode's malformed-line zero padding."""
    transfer = str(part.get("Content-Transfer-Encoding", "")).strip().lower()
    if transfer in ("x-uuencode", "uuencode", "uue", "x-uue"):
        wire = part.get_payload()
        if not isinstance(wire, str):
            return None
        limit = MimeLimits().max_expanded_bytes
        if len(wire) > limit:
            return wire
        try:
            # The filename is not file content; avoid charset loss in ignored UU headers.
            transfer_wire = re.sub(r"(?m)^(begin [0-7]+) [^\r\n]*", r"\1 <data>", wire, count=1)
            return _uu_payload(transfer_wire.encode("ascii", "surrogateescape"), limit)
        except (ValueError, LookupError):
            return wire
    payload = part.get_payload(decode=True)
    return payload if isinstance(payload, (bytes, str)) else None


def _child_spans(entity: bytes, offset: int, boundary: str, stop: int) -> Iterator[tuple[int, int]]:
    marker = re.compile(br"(?<![^\r\n])--" + re.escape(boundary.encode("ascii")) + br"(?P<closed>--)?[ \t]*(?:\r\n|\n|\r|\Z)")
    start: int | None = None
    for match in marker.finditer(entity, offset, stop):
        if start is not None and match.start() == start:
            if match.group("closed"):
                raise ValueError("ambiguous empty MIME entity before closing boundary")
            # Stdlib ignores directly consecutive opening boundaries.
            start = match.end()
            continue
        if start is not None:
            end = match.start()
            end -= 2 if entity[max(0, end - 2):end] == b"\r\n" else 1 if entity[max(0, end - 1):end] in (b"\n", b"\r") else 0
            yield start, end
        if match.group("closed"):
            break
        start = match.end()
    else:
        if start is not None:
            yield start, stop


class OriginalMimeParts(BaseModel):
    """Retain shared byte spans and at most one bounded decode per entity."""
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)
    entity: bytes
    start: int = 0
    end: int | None = None
    depth: int = 0
    default_type: str = "text/plain"
    budget: MimeReaderBudget = Field(default_factory=MimeReaderBudget)
    _parsed: tuple[Message, int] | None = PrivateAttr(default=None)
    _children: list[OriginalMimeParts] = PrivateAttr(default_factory=list)
    _iterator: Iterator[tuple[int, int]] | None = PrivateAttr(default=None)
    _decoded: bytes | None = PrivateAttr(default=None)

    @property
    def stop(self) -> int:
        return len(self.entity) if self.end is None else self.end

    def headers(self) -> tuple[Message, int]:
        if self._parsed is None:
            headers, offset = _headers(self.entity, self.start, self.stop)
            headers.set_default_type(self.default_type)
            self._parsed = headers, offset
        return self._parsed

    def child(self, index: int) -> OriginalMimeParts:
        if index < 1:
            raise ValueError("MIME child indices start at one")
        if self.depth >= self.budget.limits.max_depth:
            raise MimeReaderLimitError("MIME reader depth limit reached; use Raw Source or Save Message")
        headers, offset = self.headers()
        boundary = headers.get_boundary()
        if headers.get_content_maintype() == "multipart" and boundary is not None:
            if self._iterator is None:
                self._iterator = _child_spans(self.entity, offset, boundary, self.stop)
            while len(self._children) < index:
                try:
                    start, end = next(self._iterator)
                except StopIteration as error:
                    raise ValueError("selected MIME entity is not present in original message bytes") from error
                default = "message/rfc822" if headers.get_content_type() == "multipart/digest" else "text/plain"
                self.budget.part()
                self._children.append(OriginalMimeParts(entity=self.entity, start=start, end=end,
                    depth=self.depth + 1, default_type=default, budget=self.budget))
            return self._children[index - 1]
        if headers.get_content_type() == "message/rfc822" and index == 1:
            if not self._children:
                self.budget.part()
                transfer = str(headers.get("Content-Transfer-Encoding", "")).strip().lower()
                child = (OriginalMimeParts(entity=self.entity, start=offset, end=self.stop,
                             depth=self.depth + 1, budget=self.budget) if transfer in IDENTITY_ENCODINGS
                         else OriginalMimeParts(entity=self.decoded(), depth=self.depth + 1, budget=self.budget))
                self._children.append(child)
            return self._children[0]
        raise ValueError("selected MIME path does not match original message bytes")

    def selected(self, part_path: tuple[int, ...]) -> OriginalMimeParts:
        if len(part_path) > self.budget.limits.max_depth:
            raise MimeReaderLimitError("MIME reader depth limit reached; use Raw Source or Save Message")
        selected = self
        for index in part_path:
            selected = selected.child(index)
        return selected

    def decoded(self) -> bytes:
        headers, offset = self.headers()
        size = self.stop - offset
        transfer = str(headers.get("Content-Transfer-Encoding", "")).strip().lower()
        if transfer in IDENTITY_ENCODINGS:
            if size > self.budget.limits.max_expanded_bytes:
                raise MimeReaderLimitError("MIME reader byte limit reached; use Raw Source or Save Message")
            return self.entity[offset:self.stop]
        if self._decoded is None:
            # Bound the transport copy before decoding; reuse it for child traversal.
            self.budget.ensure_bytes(size)
            value = _decoded_payload(headers, self.entity[offset:self.stop], self.budget.remaining)
            self.budget.ensure_bytes(len(value))
            self.budget.decoded_bytes += len(value)
            self._decoded = value
        return self._decoded

    def size(self, part_path: tuple[int, ...]) -> int:
        selected = self.selected(part_path)
        headers, offset = selected.headers()
        if str(headers.get("Content-Transfer-Encoding", "")).strip().lower() in IDENTITY_ENCODINGS:
            size = selected.stop - offset
            if size > self.budget.limits.max_expanded_bytes:
                raise MimeReaderLimitError("MIME reader byte limit reached; use Raw Source or Save Message")
            return size
        return len(selected.decoded())

    def payload(self, part_path: tuple[int, ...]) -> bytes:
        return self.selected(part_path).decoded()


def original_part_payload(raw: bytes, part_path: tuple[int, ...]) -> bytes:
    """Decode one selected MIME entity without rewriting its encapsulated message."""
    return OriginalMimeParts(entity=raw).payload(part_path)
