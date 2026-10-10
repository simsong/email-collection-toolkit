# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Build a purpose-made collection for reader and relocated-package acceptance.
# This uses low-level catalog and byte-preserving MBOX writers only for fixtures.
# It never enables Windows production ingestion or touches an existing directory.
# Raw bytes include Unicode, CRLF, From quoting and a final unterminated line.
# Reader checks compare every fixture file before and after shared service calls.
# Packaging can run these checks without a development checkout or pytest.
"""Synthetic, portable reader acceptance collection and checks."""
from __future__ import annotations

from contextlib import closing
import hashlib
from pathlib import Path

from pydantic import BaseModel, Field

from .bagit import BAGIT_DECLARATION, initialize_bag
from .catalog import address_pk, create_catalog, create_search
from .gui_service import describe_message, render_part, search_page, write_message
from .layout import mbox_directory
from .mailsearch import read_message_bytes
from .mbox import frame_message
from .search import index_message

RAW_MESSAGE = (b"From: Reader <reader@example.invalid>\r\nTo: Archive <archive@example.invalid>\r\n"
               b"Date: Mon, 07 Sep 2026 10:00:00 +0000\r\nSubject: Portable message viewer\r\n"
               b"Message-ID: <reader-check@example.invalid>\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n"
               b"Observatory caf\xc3\xa9\r\nFrom a preserved source\r\nFinal line without newline")


class ReaderReport(BaseModel):
    passed: bool = False
    checks: list[str] = Field(default_factory=list)
    installed_msix_tested: bool = False


def inventory(root: Path) -> dict[str, str]:
    """Stream hashes of all collection files, including derivative databases."""
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            with path.open("rb") as stream:
                result[path.relative_to(root).as_posix()] = hashlib.file_digest(stream, "sha256").hexdigest()
    return result


def create_fixture(archive: Path, *, messages: int = 1) -> None:
    """Create only a new synthetic directory, never import a user's collection."""
    if messages < 1:
        raise ValueError("A reader fixture must contain at least one message")
    archive.mkdir(parents=True, exist_ok=False)
    # Fast reader-only fixture assembly has no production publication durability claim.
    (archive / "bagit.txt").write_bytes(BAGIT_DECLARATION.encode("ascii"))
    initialize_bag(archive)
    path = mbox_directory(archive) / "2026-Archive1.mbox"
    with closing(create_catalog(archive / "archive.sqlite3")) as catalog, closing(
        create_search(archive / "search.sqlite3")
    ) as search, path.open("wb") as box:
        sender = address_pk(catalog, "reader@example.invalid")
        catalog.execute("INSERT INTO mbox_generations(generation_pk,filename,sha256,message_count,byte_count) VALUES (1,?,'',?,0)",
                        (path.name, messages))
        for number in range(1, messages + 1):
            raw = RAW_MESSAGE if number == 1 else RAW_MESSAGE.replace(
                b"reader-check@example.invalid", f"reader-{number}@example.invalid".encode()
            ).replace(b"Portable message viewer", f"Observatory record {number:06d}".encode())
            framed = frame_message(raw) + b"\n"
            offset = box.tell()
            box.write(framed + b"\n")
            digest = hashlib.sha256(raw).hexdigest()
            catalog.execute(
                "INSERT INTO messages(message_pk,message_id_normalized,sha256,sender_address_pk,subject,date_utc,date_source,category) "
                "VALUES (?,?,?,?,?,?,'date','Archive')",
                (number, f"reader-{number}@example.invalid", digest, sender,
                 "Portable message viewer" if number == 1 else f"Observatory record {number:06d}", "2026-09-07T10:00:00+00:00"),
            )
            catalog.execute("INSERT INTO locations(message_pk,generation_pk,byte_offset,byte_length) VALUES (?,1,?,?)",
                            (number, offset, len(framed)))
            index_message(search, raw, True)
        catalog.execute("UPDATE mbox_generations SET byte_count=?", (box.tell(),))
        catalog.commit()
        search.commit()


def check_reader(output: Path, *, installed: bool = False) -> ReaderReport:
    """Validate shared reader services and byte-exact export after relocation."""
    output.mkdir(parents=True, exist_ok=False)
    archive = output / "synthetic.mailarchive"
    create_fixture(archive)
    before = inventory(archive)
    report = ReaderReport(installed_msix_tested=installed)
    for sort in ("date", "subject", "sender"):
        for direction in ("ascending", "descending"):
            page = search_page(archive, "observatory", sort_by=sort, direction=direction)
            if len(page.results) != 1 or page.error:
                raise AssertionError(f"Reader search failed: {sort}/{direction}")
    report.checks.append("six shared search orders")
    if read_message_bytes(archive, 1) != RAW_MESSAGE:
        raise AssertionError("reader changed original message bytes")
    view = describe_message(archive, 1)
    if "caf\u00e9" not in render_part(archive, 1, view.preferred_part_id).content:
        raise AssertionError("reader failed Unicode body rendering")
    exported = output / "export.eml"
    write_message(archive, 1, exported)
    if exported.read_bytes() != RAW_MESSAGE:
        raise AssertionError("message export changed original bytes")
    report.checks.extend(["Unicode message rendering", "CRLF/mboxrd/unterminated byte-exact export"])
    if inventory(archive) != before:
        raise AssertionError("reader modified the fixture collection")
    report.checks.append("all collection files unchanged")
    report.passed = True
    (output / "report.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return report
