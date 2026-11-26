# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Requirements: leading envelope lines do not break the reader; View Message Metadata is complete.

Older ingests kept a source envelope (often mboxo-quoted as ">From ") as the first
line of canonical bytes. The reader must skip that line when parsing headers and
MIME, while Raw Source and Save Message keep the exact stored bytes. The metadata
report must group catalog identity, hashes, sources, duplicates, defects, MIME and
attachment sizes, and extraction facts, reading every database without writing.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from mailarchiver.gui_metadata import MessageMetadata, message_metadata
from mailarchiver.gui_service import RAW_PART_ID, describe_message, display_entity, render_part, write_message
from tests.test_gui_service import MULTIPART_MESSAGE, make_gui_archive

ENVELOPE = b">From sender@example.net Fri Dec  5 08:49:19 2003\n"
ENVELOPED_MESSAGE = ENVELOPE + MULTIPART_MESSAGE.replace(b"<multipart@example>", b"<enveloped@example>")
ENVELOPED_PK = 3


def _archive(tmp_path: Path) -> Path:
    digest = hashlib.sha256(ENVELOPED_MESSAGE).hexdigest()
    return make_gui_archive(tmp_path, ((ENVELOPED_MESSAGE, digest, "multipart message", "2024-01-05T10:00:00+00:00"),))


def _rows(report: MessageMetadata, title: str) -> dict[str, str]:
    section = next(section for section in report.sections if section.title == title)
    return {row.label: row.value for row in section.rows}


@pytest.mark.parametrize("envelope", [b">From a@b Fri Dec  5 08:49:19 2003\n", b">>From a@b x\r\n", b"From a@b x\n"])
def test_display_entity_skips_only_a_leading_envelope_line(envelope: bytes) -> None:
    """Requirement: only a first-line envelope is skipped; header and body bytes are untouched."""
    entity = b"Subject: s\n\n>From body line\n"
    assert display_entity(envelope + entity) == entity
    assert display_entity(entity) == entity
    assert display_entity(b"X-From: a@b\n" + entity) == b"X-From: a@b\n" + entity


def test_enveloped_message_displays_mime_body_and_keeps_raw_source(tmp_path: Path) -> None:
    """Requirement: a stored ">From " first line no longer turns the whole message into part 0 text."""
    archive = _archive(tmp_path)
    view = describe_message(archive, ENVELOPED_PK)
    assert [part.label.split(" — ")[0] for part in view.body_parts] == ["Plain Text", "HTML", "Raw Source"]
    assert {header.name for header in view.headers} >= {"Message-ID", "From", "Subject", "Content-Type"}
    assert [item.filename for item in view.attachments] == ["logo.png", "report.pdf", "notes.txt"]
    plain = next(part.part_id for part in view.body_parts if part.content_type == "text/plain")
    assert render_part(archive, ENVELOPED_PK, plain).content == "Plain version."
    assert render_part(archive, ENVELOPED_PK, RAW_PART_ID).content.startswith(ENVELOPE.decode("ascii"))
    exported = tmp_path / "export.eml"
    write_message(archive, ENVELOPED_PK, exported)
    assert exported.read_bytes() == ENVELOPED_MESSAGE


def test_metadata_report_groups_identity_hashes_mime_and_extraction(tmp_path: Path) -> None:
    """Requirement: the report shows identity, hashes, attachment count/sizes, defects and extraction runs."""
    archive = _archive(tmp_path)
    before = {path.name: path.read_bytes() for path in archive.glob("*.sqlite3")}
    report = message_metadata(archive, ENVELOPED_PK)
    titles = [section.title for section in report.sections]
    assert titles == ["Message", "Recipients", "Hashes and storage", "Sources", "Duplicates",
                      "Metadata defects", "MIME structure and attachments", "Extraction"]
    message = _rows(report, "Message")
    assert message["Message-ID header"] == "<enveloped@example>"
    assert ENVELOPE.decode("ascii").strip() in message["Display note"]
    assert _rows(report, "Recipients") == {"To": "recipient@example.net"}
    hashes = _rows(report, "Hashes and storage")
    assert hashes["Canonical SHA-256"] == hashlib.sha256(ENVELOPED_MESSAGE).hexdigest()
    assert hashes["Canonical size"] == f"{len(ENVELOPED_MESSAGE):,} bytes"
    mime = _rows(report, "MIME structure and attachments")
    assert mime["Top-level type"] == "multipart/mixed"
    sizes = (len(b"image"), len(b"%PDF-1.4\n"), len(b"Appendixquartz appears only in this attachment."))
    assert mime["Attachments"] == f"3 ({sum(sizes)} bytes decoded)"
    assert mime["Part 5"] == f"report.pdf · application/pdf · {sizes[1]} bytes"
    extraction = _rows(report, "Extraction")
    assert extraction["Search index"].startswith("indexed")
    assert "no processing database" in extraction["Processors"]
    assert {path.name: path.read_bytes() for path in archive.glob("*.sqlite3")} == before


def test_metadata_report_lists_sources_and_semantic_duplicates(tmp_path: Path) -> None:
    """Requirement: every source observation and each message sharing semantic content is listed."""
    archive = _archive(tmp_path)
    with sqlite3.connect(archive / "archive.sqlite3") as catalog:
        semantic, source_file, run = catalog.execute(
            "SELECT semantic_sha256, source_file_pk, run_pk FROM observations WHERE message_pk = 1").fetchone()
        catalog.executemany(
            "INSERT INTO observations(run_pk, message_pk, source_file_pk, source_offset, raw_sha256, semantic_sha256, "
            "disposition, detail) VALUES (?, ?, ?, ?, ?, ?, ?, 'Archive')",
            [(run, ENVELOPED_PK, source_file, 500, "e" * 64, semantic, "archived"),
             (run, 1, source_file, 900, "f" * 64, semantic, "duplicate")],
        )
    report = message_metadata(archive, 1)
    sources = [section for section in report.sections if section.title.startswith("Source ")]
    assert [section.title for section in sources] == ["Source 1 of 2", "Source 2 of 2"]
    assert {row.value for row in sources[1].rows if row.label == "Disposition"} == {"duplicate"}
    duplicates = _rows(report, "Duplicates")
    assert duplicates["Exact duplicate copies seen"] == "1"
    assert duplicates[f"Message #{ENVELOPED_PK}"].startswith("same semantic content")
    assert _rows(report, "Extraction")["Ingest run #1"].startswith("started 2026-08-23T00:00:00+00:00")
