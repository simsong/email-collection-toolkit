# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Exercise the packaged Python reader without opening a native window.
# Create a disposable catalogue, FTS index and MBOX from known message bytes.
# Production validation, search, completion and retrieval consume that fixture.
# Hash inventories before and after reading establish preservation of the fixture.
# MSIX install/upgrade jobs invoke this through the real frozen entry point.
# This verifies reading; it does not certify Windows ingest or antivirus support.
"""Headless Python desktop acceptance for installed Windows packages."""
from __future__ import annotations

from contextlib import closing
import hashlib
from pathlib import Path
import sys

from pydantic import BaseModel

from .application import validate_archive
from .catalog import address_pk, create_catalog, create_search
from .gui_service import search_page
from .layout import mbox_directory
from .mailsearch import read_message_bytes
from .mbox import PreservingMbox, add_message
from .search import index_message
from .search_completion import search_suggestions
from .plugin_loader import builtin_plugin_directory
from .processing.registry import load_processors


class FileDigest(BaseModel):
    path: str
    sha256: str


class WindowsReport(BaseModel):
    implementation: str = "python"
    installed_msix_tested: bool
    search_count: int
    completion_count: int
    processor_count: int
    archive_sha256: list[FileDigest]


def inventory(root: Path) -> list[FileDigest]:
    """Keep content comparisons deterministic and streaming."""
    result = []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            with path.open("rb") as stream:
                result.append(FileDigest(path=str(path.relative_to(root)),
                                         sha256=hashlib.file_digest(stream, "sha256").hexdigest()))
    return result


def exercise(output: Path, *, installed: bool = False) -> WindowsReport:
    """Use real packaged schemas and reader APIs on a purpose-made archive."""
    output.mkdir(parents=True, exist_ok=False)
    archive = output / "synthetic.mailarchive"
    mbox_directory(archive).mkdir(parents=True)
    raw = (b"From: Reader <reader@example.invalid>\nTo: Test <test@example.invalid>\n"
           b"Date: Wed, 03 Jan 2024 10:00:00 +0000\nSubject: Observatory fixture\n"
           b"Message-ID: <msix-fixture@example.invalid>\n\nHello observatory.\n")
    path = mbox_directory(archive) / "2024-Archive1.mbox"
    box = PreservingMbox(path, create=True)
    try:
        location = add_message(box, path, raw)
    finally:
        box.close()
    with closing(create_catalog(archive / "archive.sqlite3")) as catalog:
        sender = address_pk(catalog, "reader@example.invalid")
        cursor = catalog.execute(
            "INSERT INTO messages(message_id_normalized, sha256, sender_address_pk, subject, date_utc, date_source, category) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("msix-fixture@example.invalid", hashlib.sha256(raw).hexdigest(), sender,
             "Observatory fixture", "2024-01-03T10:00:00+00:00", "date", "Archive"))
        message_pk = cursor.lastrowid
        assert message_pk is not None
        generation = catalog.execute(
            "INSERT INTO mbox_generations(filename, sha256, message_count, byte_count) VALUES (?, '', 1, ?)",
            (path.name, path.stat().st_size)).lastrowid
        catalog.execute("INSERT INTO locations(message_pk, generation_pk, byte_offset, byte_length) VALUES (?, ?, ?, ?)",
                        (message_pk, generation, location.byte_offset, location.byte_length))
        catalog.commit()
    with closing(create_search(archive / "search.sqlite3")) as search:
        index_message(search, raw, False)
        search.commit()
    processors = load_processors((builtin_plugin_directory(),))
    assert processors, "Packaged processor source files are missing"
    before = inventory(archive)
    validate_archive(archive)
    page = search_page(archive, "observatory", 0, 10, "date", "descending", False, None)
    assert len(page.results) == 1, "Packaged Python FTS search failed"
    assert read_message_bytes(archive, page.results[0].message_pk) == raw, "Message bytes changed"
    suggestions = search_suggestions(archive, "reader")
    assert suggestions.items, "Packaged Python autocomplete failed"
    assert inventory(archive) == before, "Reader modified archive files"
    report = WindowsReport(installed_msix_tested=installed or bool(getattr(sys, "frozen", False)),
                           search_count=len(page.results), completion_count=len(suggestions.items),
                           processor_count=len(processors), archive_sha256=before)
    (output / "report.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return report


def main() -> int:
    """Report failures outside the fixture directory even for windowed executables."""
    if len(sys.argv) != 3:
        return 2
    output = Path(sys.argv[2]).resolve()
    try:
        exercise(output)
    except Exception as error:  # pylint: disable=broad-exception-caught
        output.parent.mkdir(parents=True, exist_ok=True)
        output.with_suffix(".log").write_text(str(error), encoding="utf-8")
        return 1
    return 0
