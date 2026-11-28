# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Assemble the read-only "View Message Metadata" report for one archived message.

Purpose: Show, in one grouped report, everything the archive knows about a message:
catalog identity, hashes, every source observation, duplicates, MIME structure and
attachment sizes, metadata defects, and which ingest runs and processors derived it.

How it works: every database is opened read-only (``mode=ro``); optional derived
databases (search, processing) are reported as absent rather than created. MIME
facts come from the same display parse the reader uses, so the report agrees with
the message pane. Nothing here writes to the archive.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from email.message import Message
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .gui_service import AttachmentInfo, BodyPart, describe_message, parsed_message
from .mailsearch import read_message_bytes
from .message import decoded_message_header
from .mime_tree import MimeReaderLimitError, OriginalMimeParts, walk_mime
from .processing.store import DATABASE as PROCESSING_DATABASE
from .sqlite_paths import sqlite_uri

ROLE_LABELS = {"to": "To", "cc": "Cc", "bcc": "Bcc"}
RELATED_LIMIT = 50
# One joined observations/source_files/ingest_runs row, in the SELECT order below.
Observation = tuple[Any, ...]


class MetadataRow(BaseModel):
    label: str
    value: str
    monospace: bool = False


class MetadataSection(BaseModel):
    title: str
    rows: list[MetadataRow]


class MessageMetadata(BaseModel):
    message_pk: int
    subject: str
    sections: list[MetadataSection]


def _row(label: str, value: object, monospace: bool = False) -> MetadataRow:
    return MetadataRow(label=label, value="" if value is None else str(value), monospace=monospace)


def _bytes_label(count: int | None) -> str:
    return "unknown" if count is None else f"{count:,} bytes"


def message_metadata(archive: Path, message_pk: int) -> MessageMetadata:
    """Return grouped catalog, provenance, MIME and extraction facts for one message."""
    view = describe_message(archive, message_pk)
    stored = read_message_bytes(archive, message_pk)
    entity, message = parsed_message(archive, message_pk)
    with closing(sqlite3.connect(sqlite_uri(archive / "archive.sqlite3"), uri=True)) as catalog:
        row = catalog.execute(
            "SELECT m.message_id_normalized, m.sha256, a.address, m.subject, m.date_utc, m.date_source, m.category, "
            "g.filename, g.sha256, l.byte_offset, l.byte_length FROM messages m "
            "JOIN email_addresses a ON a.address_pk = m.sender_address_pk "
            "LEFT JOIN locations l USING (message_pk) LEFT JOIN mbox_generations g USING (generation_pk) "
            "WHERE m.message_pk = ?",
            (message_pk,),
        ).fetchone()
        if row is None:
            raise ValueError(f"unknown message {message_pk}")
        (normalized_id, sha256, sender, subject, date_utc, date_source, category,
         mbox_name, mbox_sha256, offset, record_length) = row
        sections = [
            _identity_section(message_pk, normalized_id, sender, subject, date_utc, date_source, category,
                              decoded_message_header(entity, message, "Message-ID"), len(stored) - len(entity), stored),
            _recipient_section(catalog, message_pk),
            MetadataSection(title="Hashes and storage", rows=[
                _row("Canonical SHA-256", sha256, True),
                _row("Canonical size", _bytes_label(len(stored))),
                _row("Archive mailbox", f"data/mbox/{mbox_name}" if mbox_name else "(no location)"),
                _row("Mailbox offset", f"{offset:,}" if offset is not None else ""),
                _row("Stored record length", _bytes_label(record_length)),
                _row("Mailbox file SHA-256", mbox_sha256, True),
            ]),
        ]
        observations = catalog.execute(
            "SELECT o.observation_pk, o.run_pk, o.disposition, o.source_offset, o.source_cursor, o.raw_sha256, "
            "o.semantic_sha256, s.source_plugin, s.source_kind, s.source_path, s.byte_length, s.sha256, s.checked_at, "
            "r.started_at, r.completed_at, r.result FROM observations o "
            "JOIN source_files s USING (source_file_pk) LEFT JOIN ingest_runs r USING (run_pk) "
            "WHERE o.message_pk = ? ORDER BY o.observation_pk",
            (message_pk,),
        ).fetchall()
        sections.extend(_source_sections(observations))
        sections.append(_duplicate_section(catalog, message_pk, normalized_id, observations))
        sections.append(_defect_section(catalog, message_pk))
        sections.append(_mime_section(entity, message, view.attachments, view.body_parts))
        sections.append(_extraction_section(archive, sha256, observations))
    return MessageMetadata(message_pk=message_pk, subject=view.subject, sections=sections)


def _identity_section(
    message_pk: int, normalized_id: str, sender: str, subject: str, date_utc: str, date_source: str,
    category: str, header_id: str, skipped: int, stored: bytes,
) -> MetadataSection:
    rows = [
        _row("Catalog number", message_pk),
        _row("Message-ID header", header_id or "(missing)", True),
        _row("Catalog message ID", normalized_id, True),
        _row("Subject", subject),
        _row("Sender", sender or "(missing)"),
        _row("Date (UTC)", date_utc),
        _row("Date source", date_source),
        _row("Mailbox category", category),
    ]
    if skipped:
        envelope = stored[:skipped].rstrip(b"\r\n").decode("latin-1")
        rows.append(_row("Display note", f"Stored bytes begin with an envelope line ({envelope}); "
                                         "the reader skips it to parse headers and body. Raw Source and "
                                         "Save Message keep it."))
    return MetadataSection(title="Message", rows=rows)


def _recipient_section(catalog: sqlite3.Connection, message_pk: int) -> MetadataSection:
    grouped: dict[str, list[str]] = {}
    for role, address in catalog.execute(
        "SELECT r.role, a.address FROM recipients r JOIN email_addresses a USING (address_pk) "
        "WHERE r.message_pk = ? ORDER BY r.role, a.address",
        (message_pk,),
    ):
        grouped.setdefault(str(role), []).append(str(address))
    rows = [_row(ROLE_LABELS.get(role, role), ", ".join(addresses)) for role, addresses in grouped.items()]
    return MetadataSection(title="Recipients", rows=rows or [_row("Recipients", "(none recorded)")])


def _source_sections(observations: list[Observation]) -> list[MetadataSection]:
    sections = []
    for index, observation in enumerate(observations, 1):
        (observation_pk, run_pk, disposition, offset, cursor, raw_sha256, semantic_sha256, plugin, kind, path,
         file_length, file_sha256, checked_at, _started, _completed, _result) = observation
        rows = [
            _row("Disposition", disposition),
            _row("Source path", f"/{path}" if str(path).startswith("Users/") else path),
            _row("Source plugin", plugin),
            _row("Source kind", kind),
            _row("Offset in source", f"{offset:,}" if offset is not None else cursor or ""),
            _row("Observed raw SHA-256", raw_sha256, True),
            _row("Semantic SHA-256", semantic_sha256 or "(not computed)", True),
            _row("Source file size", _bytes_label(file_length)),
            _row("Source file SHA-256", file_sha256 or "(not recorded)", True),
            _row("Source checked at", checked_at or "(not recorded)"),
            _row("Ingest run", f"#{run_pk} (observation {observation_pk})"),
        ]
        sections.append(MetadataSection(title=f"Source {index} of {len(observations)}", rows=rows))
    return sections or [MetadataSection(title="Sources", rows=[_row("Sources", "(no observations recorded)")])]


def _duplicate_section(
    catalog: sqlite3.Connection, message_pk: int, normalized_id: str, observations: list[Observation],
) -> MetadataSection:
    exact = sum(1 for observation in observations if observation[2] == "duplicate")
    rows = [_row("Exact duplicate copies seen", f"{exact:,}")]
    semantic = sorted({observation[6] for observation in observations if observation[6]})
    related: dict[int, str] = {}
    for digest in semantic:
        for other_pk, subject, date_utc, category in catalog.execute(
            "SELECT DISTINCT m.message_pk, m.subject, m.date_utc, m.category FROM observations o "
            "JOIN messages m USING (message_pk) WHERE o.semantic_sha256 = ? AND o.message_pk != ? LIMIT ?",
            (digest, message_pk, RELATED_LIMIT),
        ):
            related[int(other_pk)] = f"same semantic content · {date_utc} · {category} · {subject}"
    for other_pk, subject, date_utc, category in catalog.execute(
        "SELECT message_pk, subject, date_utc, category FROM messages "
        "WHERE message_id_normalized = ? AND message_pk != ? LIMIT ?",
        (normalized_id, message_pk, RELATED_LIMIT),
    ):
        related.setdefault(int(other_pk), f"same Message-ID, different bytes · {date_utc} · {category} · {subject}")
    rows.extend(_row(f"Message #{other_pk}", detail) for other_pk, detail in sorted(related.items()))
    if not related:
        rows.append(_row("Related messages", "(none)"))
    return MetadataSection(title="Duplicates", rows=rows)


def _defect_section(catalog: sqlite3.Connection, message_pk: int) -> MetadataSection:
    rows = [_row(str(field), str(detail)) for field, detail in catalog.execute(
        "SELECT field, detail FROM metadata_defects WHERE message_pk = ? ORDER BY field, detail", (message_pk,))]
    return MetadataSection(title="Metadata defects", rows=rows or [_row("Defects", "(none)")])


def _mime_section(
    entity: bytes, message: Message, attachments: list[AttachmentInfo], body_parts: list[BodyPart],
) -> MetadataSection:
    try:
        parts = list(walk_mime(message, OriginalMimeParts(entity=entity)))
        structure = ", ".join(f"{node.part_id}: {node.part.get_content_type()}" for node in parts)
        part_count = str(len(parts))
    except MimeReaderLimitError as error:
        structure, part_count = str(error), "unknown"
    known = [item.byte_length for item in attachments if item.byte_length is not None]
    rows = [
        _row("Top-level type", message.get_content_type()),
        _row("MIME parts", part_count),
        _row("Structure", structure),
        _row("Body views", ", ".join(part.label for part in body_parts)),
        _row("Attachments", f"{len(attachments)} ({_bytes_label(sum(known))} decoded)" if attachments else "0"),
    ]
    for item in attachments:
        size = item.error or _bytes_label(item.byte_length)
        rows.append(_row(f"Part {item.part_id}", f"{item.filename} · {item.content_type} · {size}"))
    return MetadataSection(title="MIME structure and attachments", rows=rows)


def _extraction_section(archive: Path, sha256: str, observations: list[Observation]) -> MetadataSection:
    rows = []
    runs = {observation[1]: observation[13:16] for observation in observations}
    for run_pk, (started, completed, result) in sorted(runs.items()):
        rows.append(_row(f"Ingest run #{run_pk}", f"started {started} · completed {completed or '(not completed)'} · "
                                                  f"{result or 'no result recorded'}"))
    rows.append(_row("Catalog extraction", "built-in header, address and date extraction during ingest"))
    rows.extend(_search_rows(archive, sha256))
    rows.extend(_processing_rows(archive, sha256))
    return MetadataSection(title="Extraction", rows=rows)


def _search_rows(archive: Path, sha256: str) -> list[MetadataRow]:
    path = archive / "search.sqlite3"
    if not path.is_file():
        return [_row("Search index", "(no search database)")]
    try:
        with closing(sqlite3.connect(sqlite_uri(path), uri=True)) as search:
            row = search.execute("SELECT attachment_count, preview FROM message_metadata WHERE sha256 = ?",
                                 (sha256,)).fetchone()
    except sqlite3.Error as error:
        return [_row("Search index", f"unreadable: {error}")]
    if row is None:
        return [_row("Search index", "not indexed")]
    return [_row("Search index", f"indexed · {row[0]} indexed attachment(s)"), _row("Indexed preview", row[1])]


def _processing_rows(archive: Path, sha256: str) -> list[MetadataRow]:
    path = archive / PROCESSING_DATABASE
    if not path.is_file():
        return [_row("Processors", "no per-message processor record (this archive has no processing database)")]
    try:
        with closing(sqlite3.connect(sqlite_uri(path), uri=True)) as processing:
            invocations = processing.execute(
                "SELECT i.kind, i.version, i.status, i.elapsed, i.started_at FROM jobs j "
                "JOIN invocations i USING (job_id) WHERE json_extract(j.item_json, '$.message_id') = ? "
                "ORDER BY i.invocation_id",
                (sha256,),
            ).fetchall()
            producers = [str(value) for (value,) in processing.execute(
                "SELECT DISTINCT producer FROM content_parts WHERE message_id = ? ORDER BY producer", (sha256,))]
            evidence = processing.execute(
                "SELECT plugin, version, count(*) FROM evidence WHERE message_id = ? GROUP BY plugin, version "
                "ORDER BY plugin", (sha256,)).fetchall()
    except sqlite3.Error as error:
        return [_row("Processors", f"processing database unreadable: {error}")]
    rows = [_row(f"Processor {kind} {version}", f"{status} · {elapsed:.3f} s · started {started}")
            for kind, version, status, elapsed, started in invocations]
    if producers:
        rows.append(_row("Text producers", ", ".join(producers)))
    rows.extend(_row(f"Evidence from {plugin} {version}", f"{count} item(s)") for plugin, version, count in evidence)
    return rows or [_row("Processors", "no processor invocations recorded for this message")]

