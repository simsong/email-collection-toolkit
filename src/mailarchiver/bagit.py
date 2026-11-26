# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Publish native Mailbag metadata, payload manifests, and durable tag fixity.

Purpose: Publish the archive's interoperable Mailbag metadata and fixity checkpoint.

How it works:
Stream message locations into integrity records, CSV and manifests, publishing tag fixity
last.

Fundamental dependents:

- ``__main__``: completed and interrupted ingests require a valid checkpoint.
- ``application``: new archives need this native preservation layout.
"""

from __future__ import annotations

import csv
import hashlib
import logging
import math
import re
import sqlite3
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from email import policy
from email.parser import BytesParser
from importlib.metadata import version
from pathlib import Path

from pydantic import BaseModel

from .layout import integrity_directory, integrity_path as integrity_path_for, mbox_directory
from .mbox import IntegrityMessage, write_integrity_files
from .standalone_verify import EXTRA_FILE_WARNING, INSTALLED_NAME
from .storage_sync import durable_sync, replace_file, sync_directory

BAGIT_DECLARATION = "BagIt-Version: 1.0\nTag-File-Character-Encoding: UTF-8\n"
BAG_INFO = "bag-info.txt"
PAYLOAD_MANIFEST = "manifest-sha256.txt"
TAG_MANIFEST = "tagmanifest-sha256.txt"
MAILBAG_CSV = "mailbag.csv"
MAILBAG_SPLIT_PATTERN = re.compile(r"mailbag-[1-9][0-9]*\.csv")
MAILBAG_HEADERS = (
    "Error",
    "Mailbag-Message-ID",
    "Message-ID",
    "Original-File",
    "Message-Path",
    "Derivatives-Path",
    "Attachments",
)
MAILBAG_ROW_LIMIT = 100_000


class MailbagRow(BaseModel):
    """One required Mailbag CSV record."""

    error: str = ""
    mailbag_message_id: str
    message_id: str
    original_file: str
    message_path: str = ""
    derivatives_path: str = ""
    attachments: int

    def values(self) -> tuple[str, ...]:
        return (
            self.error,
            self.mailbag_message_id,
            self.message_id,
            self.original_file,
            self.message_path,
            self.derivatives_path,
            str(self.attachments),
        )


class MailbagMessageMetadata(BaseModel):
    """Best-effort Mailbag fields derived without changing preserved bytes."""

    message_id: str
    attachments: int
    error: str = ""


def _sync_directory(path: Path) -> None:
    sync_directory(path)


def _write_atomic(path: Path, content: bytes) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("wb") as output:
            output.write(content)
            output.flush()
            durable_sync(output.fileno())
        replace_file(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def initialize_bag(archive: Path) -> None:
    """Create or verify the fixed BagIt directory declaration."""
    archive.mkdir(parents=True, exist_ok=True)
    legacy = sorted((*archive.glob("*.mbox"), *archive.glob("*.mbox.integrity")))
    if legacy:
        names = ", ".join(path.name for path in legacy)
        raise ValueError(f"unsupported root-level legacy archive output: {names}")
    for directory in (archive / "data", mbox_directory(archive), integrity_directory(archive)):
        if directory.is_symlink():
            raise ValueError(f"archive directory may not be a symlink: {directory}")
        directory.mkdir(exist_ok=True)
    declaration = archive / "bagit.txt"
    if declaration.is_symlink():
        raise ValueError(f"BagIt declaration may not be a symlink: {declaration}")
    if declaration.exists():
        if declaration.read_bytes() != BAGIT_DECLARATION.encode("ascii"):
            raise ValueError(f"unsupported BagIt declaration: {declaration}")
    else:
        _write_atomic(declaration, BAGIT_DECLARATION.encode("ascii"))


def _mailbag_message_id(message: IntegrityMessage) -> str:
    identifier = (message.message_id or "").encode("utf-8")
    digest = hashlib.sha256(identifier + b"\0" + message.raw_sha256.encode("ascii")).hexdigest()
    return f"m-{digest[:32]}"


def _mailbag_metadata(raw: bytes, fallback_message_id: str) -> MailbagMessageMetadata:
    try:
        message = BytesParser(policy=policy.compat32).parsebytes(raw)
        message_id = " ".join(part.strip() for part in str(message.get("Message-ID") or "").splitlines())
        parts = message.walk() if message.is_multipart() else (message,)
        attachments = sum(
            part.get_content_disposition() == "attachment" or part.get_filename() is not None
            for part in parts
        )
        defects = "; ".join(type(defect).__name__ for defect in message.defects)
        return MailbagMessageMetadata(message_id=message_id, attachments=attachments, error=defects)
    except Exception as error:
        logging.getLogger(__name__).debug("Best-effort operation failed", exc_info=True)
        return MailbagMessageMetadata(
            message_id=fallback_message_id,
            attachments=0,
            error=f"{type(error).__name__}: {error}",
        )


class MailbagCsvWriter:
    """Stream one or more RFC 4180 Mailbag CSV tag files."""

    def __init__(self, archive: Path, message_count: int):
        self.archive = archive
        self.message_count = message_count
        self.file_count = max(1, math.ceil(message_count / MAILBAG_ROW_LIMIT))
        self.width = len(str(self.file_count))
        self.paths = [self._path(index) for index in range(1, self.file_count + 1)]
        self.temporary_paths = [path.with_name(f".{path.name}.tmp") for path in self.paths]
        self.output = None
        self.writer = None
        self.rows = 0
        self.identifiers: set[str] = set()

    def _path(self, index: int) -> Path:
        if self.file_count == 1:
            return self.archive / MAILBAG_CSV
        return self.archive / f"mailbag-{index:0{self.width}d}.csv"

    def _open(self, index: int) -> None:
        self.output = self.temporary_paths[index].open("w", encoding="utf-8", newline="")
        self.writer = csv.writer(self.output, lineterminator="\r\n")
        if index == 0:
            self.writer.writerow(MAILBAG_HEADERS)

    def write(self, filename: str, _ordinal: int, message: IntegrityMessage) -> None:
        index = self.rows // MAILBAG_ROW_LIMIT
        if self.output is None or self.rows % MAILBAG_ROW_LIMIT == 0:
            self._close_current()
            self._open(index)
        identifier = _mailbag_message_id(message)
        folded = identifier.casefold()
        if folded in self.identifiers:
            raise ValueError(f"duplicate Mailbag-Message-ID: {identifier}")
        self.identifiers.add(folded)
        metadata = _mailbag_metadata(message.raw, message.message_id or "")
        row = MailbagRow(
            error=metadata.error,
            mailbag_message_id=identifier,
            message_id=metadata.message_id,
            original_file=filename,
            attachments=metadata.attachments,
        )
        assert self.writer is not None
        self.writer.writerow(row.values())
        self.rows += 1

    def write_row(self, values: list[str]) -> None:
        """Copy one row from the previous checkpoint for an unchanged MBOX."""
        index = self.rows // MAILBAG_ROW_LIMIT
        if self.output is None or self.rows % MAILBAG_ROW_LIMIT == 0:
            self._close_current()
            self._open(index)
        folded = values[1].casefold()
        if folded in self.identifiers:
            raise ValueError(f"duplicate Mailbag-Message-ID: {values[1]}")
        self.identifiers.add(folded)
        assert self.writer is not None
        self.writer.writerow(values)
        self.rows += 1

    def _close_current(self) -> None:
        if self.output is None:
            return
        self.output.flush()
        durable_sync(self.output.fileno())
        self.output.close()
        self.output = None
        self.writer = None

    def finish(self) -> list[Path]:
        if self.output is None and self.rows == 0:
            self._open(0)
        self._close_current()
        if self.rows != self.message_count:
            raise ValueError(f"expected {self.message_count} Mailbag rows, received {self.rows}")
        for temporary, destination in zip(self.temporary_paths, self.paths, strict=True):
            replace_file(temporary, destination)
        desired = {path.name for path in self.paths}
        for path in self.archive.glob("mailbag*.csv"):
            if path.name != MAILBAG_CSV and not MAILBAG_SPLIT_PATTERN.fullmatch(path.name):
                continue
            if path.name not in desired:
                path.unlink()
        _sync_directory(self.archive)
        return self.paths

    def abort(self) -> None:
        if self.output is not None:
            self.output.close()
            self.output = None
        for path in self.temporary_paths:
            path.unlink(missing_ok=True)


def _manifest_path(path: Path, archive: Path) -> str:
    relative = path.relative_to(archive).as_posix()
    return relative.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _manifest_bytes(entries: list[tuple[str, str]]) -> bytes:
    return "".join(f"{digest}  {pathname}\n" for digest, pathname in entries).encode("utf-8")


def _sha256_file(path: Path) -> str:
    if path.is_symlink():
        raise ValueError(f"BagIt manifests may not reference a symlink: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _payload_files(archive: Path, warnings: list[str]) -> list[Path]:
    payloads: list[Path] = []
    for path in sorted((archive / "data").rglob("*")):
        if path.is_symlink():
            raise ValueError(f"payload symlink is not supported: {path.relative_to(archive)}")
        if path.is_file():
            if path.parent != mbox_directory(archive) or path.suffix != ".mbox":
                warnings.append(EXTRA_FILE_WARNING.format(path=path.relative_to(archive).as_posix()))
                continue
            payloads.append(path)
    return payloads


def _write_payload_manifest(archive: Path, mbox_digests: dict[str, str], warnings: list[str]) -> tuple[int, int]:
    entries: list[tuple[str, str]] = []
    byte_count = 0
    payloads = _payload_files(archive, warnings)
    for path in payloads:
        byte_count += path.stat().st_size
        digest = mbox_digests.get(path.name) if path.parent == mbox_directory(archive) else None
        entries.append((digest or _sha256_file(path), _manifest_path(path, archive)))
    _write_atomic(archive / PAYLOAD_MANIFEST, _manifest_bytes(entries))
    return byte_count, len(payloads)


def _read_external_identifier(path: Path) -> str | None:
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("External-Identifier: "):
            return line.removeprefix("External-Identifier: ")
    return None


def _write_bag_info(archive: Path, byte_count: int, file_count: int, packaged_at: datetime) -> None:
    if packaged_at.tzinfo is None:
        raise ValueError("Bagging-Timestamp must be timezone-aware")
    packaged_at = packaged_at.astimezone(UTC)
    identifier = _read_external_identifier(archive / BAG_INFO) or str(uuid.uuid4())
    content = "\n".join(
        (
            "Bag-Type: Mailbag",
            "Mailbag-Source: mbox",
            "Mailbag-Specification-Version: 1.0",
            "Original-Included: False",
            f"Bagging-Timestamp: {packaged_at.isoformat()}",
            f"Bagging-Date: {packaged_at.date().isoformat()}",
            f"External-Identifier: {identifier}",
            "Mailbag-Agent: mailarchiver",
            f"Mailbag-Agent-Version: {version('mailarchiver')}",
            f"Payload-Oxum: {byte_count}.{file_count}",
            "MBOX-Format-Details: mboxrd; pre-mboxrd mailarchiver records may use mboxo",
            "MBOX-Agent: mailarchiver mboxrd quoting with Python mailbox",
            "Mailarchiver-Message-Newline-Policy: preserve-source; add-final-LF-for-MBOX-framing",
        )
    ) + "\n"
    _write_atomic(archive / BAG_INFO, content.encode("utf-8"))


def _tag_files(archive: Path, csv_paths: list[Path]) -> list[Path]:
    files = [
        archive / "bagit.txt",
        archive / BAG_INFO,
        archive / PAYLOAD_MANIFEST,
        *csv_paths,
        *sorted(integrity_directory(archive).glob("*.mbox.integrity")),
    ]
    verifier = archive / INSTALLED_NAME
    if verifier.is_file():
        files.append(verifier)
    return sorted(files, key=lambda path: path.relative_to(archive).as_posix())


def _write_tag_manifest(archive: Path, csv_paths: list[Path]) -> None:
    entries = [(_sha256_file(path), _manifest_path(path, archive)) for path in _tag_files(archive, csv_paths)]
    _write_atomic(archive / TAG_MANIFEST, _manifest_bytes(entries))


def refresh_tag_manifest(archive: Path) -> None:
    """Refresh tag-file fixity after an intentional operational tag update."""
    csv_paths = sorted(archive.glob("mailbag*.csv"))
    _write_tag_manifest(archive, csv_paths)


# Files modified this close to the previous checkpoint's start are reread anyway.
CHECKPOINT_STAMP_MARGIN_NS = 2_000_000_000


class _PreviousCheckpoint:
    """What the last completed checkpoint recorded, for skipping unchanged MBOX files.

    An MBOX is reused only when it was last modified before that checkpoint began,
    its size, message count and SHA-256 agree across the catalog and the previous
    payload manifest, its integrity tag exists, and the previous Mailbag CSV holds
    exactly one row per message. Anything else is reread and reverified.
    """

    def __init__(self, archive: Path, catalog: sqlite3.Connection, stamp_ns: int,
                 manifest: dict[str, str], csv_paths: list[Path]) -> None:
        self.archive, self.catalog, self.stamp_ns, self.manifest = archive, catalog, stamp_ns, manifest
        self._rows = self._read_rows(csv_paths)
        self._pending: list[str] | None = next(self._rows, None)

    @classmethod
    def load(cls, archive: Path, catalog: sqlite3.Connection) -> _PreviousCheckpoint | None:
        try:
            info = (archive / BAG_INFO).read_text(encoding="utf-8").splitlines()
            stamp = next(line.removeprefix("Bagging-Timestamp: ") for line in info
                         if line.startswith("Bagging-Timestamp: "))
            stamped = datetime.fromisoformat(stamp)
            manifest = {}
            for line in (archive / PAYLOAD_MANIFEST).read_text(encoding="utf-8").splitlines():
                digest, _separator, name = line.partition("  ")
                manifest[name] = digest
        except (OSError, StopIteration, ValueError):
            return None
        if stamped.tzinfo is None or not (archive / TAG_MANIFEST).is_file():
            return None
        csv_paths = sorted(archive.glob("mailbag*.csv"), key=lambda path: (len(path.name), path.name))
        stamp_ns = int(stamped.timestamp() * 1_000_000_000)
        return cls(archive, catalog, stamp_ns, manifest, csv_paths)

    @staticmethod
    def _read_rows(paths: list[Path]) -> Iterator[list[str]]:
        for index, path in enumerate(paths):
            with path.open(encoding="utf-8", newline="") as source:
                reader = csv.reader(source)
                if index == 0 and next(reader, None) != list(MAILBAG_HEADERS):
                    return
                yield from reader

    def _take_rows(self, filename: str) -> list[list[str]]:
        """Advance the old CSV (in MBOX-name order) and return this file's rows."""
        rows: list[list[str]] = []
        while self._pending is not None and len(self._pending) == len(MAILBAG_HEADERS) and self._pending[3] < filename:
            self._pending = next(self._rows, None)
        while self._pending is not None and len(self._pending) == len(MAILBAG_HEADERS) and self._pending[3] == filename:
            rows.append(self._pending)
            self._pending = next(self._rows, None)
        return rows

    def reusable(self, path: Path, message_count: int) -> tuple[str, list[list[str]]] | None:
        rows = self._take_rows(path.name)
        stat = path.stat()
        if stat.st_mtime_ns >= self.stamp_ns - CHECKPOINT_STAMP_MARGIN_NS or len(rows) != message_count:
            return None
        if not integrity_path_for(self.archive, path.name).is_file():
            return None
        row = self.catalog.execute(
            "SELECT sha256, message_count, byte_count FROM mbox_generations WHERE filename = ?", (path.name,)
        ).fetchone()
        if row is None or not row[0] or row[1] != message_count or row[2] != stat.st_size:
            return None
        if self.manifest.get(_manifest_path(path, self.archive)) != row[0]:
            return None
        return str(row[0]), rows


class CheckpointInterrupted(Exception):
    """A requested stop abandoned a checkpoint; the next completed import repairs it."""


def write_bag_checkpoint(
    archive: Path,
    catalog: sqlite3.Connection,
    packaged_at: datetime | None = None,
    *,
    should_stop: Callable[[], bool] | None = None,
    full: bool = False,
) -> list[str]:
    """Publish fixity in dependency order; return warnings for untouched non-mail extras.

    ``should_stop`` is polled before each message; when it returns true the
    checkpoint stops with :class:`CheckpointInterrupted` before writing manifests,
    leaving the detectably stale state that an interrupted import already allows.
    Unless ``full`` is set, MBOX files unmodified since the previous checkpoint
    reuse its verified digests and CSV rows instead of being reread.
    """
    started = datetime.now(UTC)
    initialize_bag(archive)
    count_row = catalog.execute("SELECT COUNT(*) FROM messages").fetchone()
    assert count_row is not None
    previous = _PreviousCheckpoint.load(archive, catalog) if not full else None
    writer = MailbagCsvWriter(archive, int(count_row[0]))

    def reuse(path: Path, message_count: int) -> str | None:
        if previous is None:
            return None
        if should_stop is not None and should_stop():
            raise CheckpointInterrupted("checkpoint stopped on request")
        found = previous.reusable(path, message_count)
        if found is None:
            return None
        digest, rows = found
        for row in rows:
            writer.write_row(row)
        return digest

    def visit(filename: str, ordinal: int, message: IntegrityMessage) -> None:
        if should_stop is not None and should_stop():
            raise CheckpointInterrupted("checkpoint stopped on request")
        writer.write(filename, ordinal, message)

    try:
        mbox_digests = write_integrity_files(archive, catalog, visit, reuse)
        csv_paths = writer.finish()
    except BaseException:
        writer.abort()
        raise
    warnings: list[str] = []
    byte_count, file_count = _write_payload_manifest(archive, mbox_digests, warnings)
    # The timestamp is when this checkpoint began, so the next one can skip
    # MBOX files that have not been modified since.
    _write_bag_info(archive, byte_count, file_count, packaged_at or started)
    _write_tag_manifest(archive, csv_paths)
    return warnings
