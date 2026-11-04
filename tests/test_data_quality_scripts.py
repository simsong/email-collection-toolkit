# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Verify the reusable data-quality audit's read-only and format logic."""

import sqlite3
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from scripts.data_quality.analyze_archive import (
    normalized_date,
    open_catalog,
    source_format,
)
from scripts.ocr_experiment import connection as ocr_connection


def test_audit_catalog_connection_cannot_modify_archive(tmp_path: Path) -> None:
    """Requirement: data-quality audits never modify the canonical archive catalog."""
    archive = tmp_path / "archive"
    archive.mkdir()
    database = archive / "archive.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE evidence(value TEXT NOT NULL)")
        connection.execute("INSERT INTO evidence VALUES ('preserved')")

    connection = open_catalog(archive)
    try:
        assert connection.execute("SELECT value FROM evidence").fetchone()[0] == "preserved"
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("DELETE FROM evidence")
    finally:
        connection.close()

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT value FROM evidence").fetchone()[0] == "preserved"


@pytest.mark.parametrize("missing_count", [0, 1, 2])
def test_audit_accepts_healthy_and_small_missing_sender_populations(tmp_path: Path, missing_count: int) -> None:
    """Data quality: zero results produce header-only CSV; small samples include every candidate."""
    import csv
    from scripts.data_quality.analyze_archive import analyze_bad_dates, analyze_early_source, analyze_missing_senders
    from tests.test_gui_service import make_gui_archive
    from mailarchiver.catalog import address_pk
    archive = make_gui_archive(tmp_path)
    with sqlite3.connect(archive / "archive.sqlite3") as database:
        empty = address_pk(database, "")
        database.execute("UPDATE messages SET sender_address_pk=? WHERE message_pk<=?", (empty, missing_count))
    output = tmp_path / "audit"
    output.mkdir()
    early = tmp_path / "empty-source"
    early.mkdir()
    connection = open_catalog(archive)
    try:
        dates, _path = analyze_bad_dates(connection, archive, output)
        missing, population, _path = analyze_missing_senders(connection, archive, output)
        sources = analyze_early_source(early, output)
    finally:
        connection.close()
    assert dates == [] and sources == []
    assert population == missing_count and len(missing) == missing_count
    for name, expected in (("BAD_DATES.csv", 0), ("MISSING_SENDER.csv", missing_count), ("EARLY_SOURCE_FILES.csv", 0)):
        with (output / name).open(newline="", encoding="utf-8") as stream:
            reader = csv.DictReader(stream)
            assert reader.fieldnames and len(list(reader)) == expected


def test_audit_recognizes_extensionless_babyl_and_normalizes_dates(tmp_path: Path) -> None:
    """Requirement: audit evidence recognizes Babyl signatures and compares dates in UTC."""
    babyl = tmp_path / "aliza"
    babyl.write_bytes(b"BaByL OpTiOnS:\nVersion: 5\n")

    assert source_format(babyl) == "babyl"
    normalized = normalized_date("1 Jan 1983 01:00:00 +0200")
    assert normalized is not None
    assert normalized.isoformat() == "1982-12-31T23:00:00+00:00"


@pytest.mark.parametrize("name", ["café #100% & archive", "percent%20literal",
    pytest.param("query?mode=rw", marks=pytest.mark.skipif(os.name == "nt", reason="Windows forbids ? in filenames"))])
@pytest.mark.parametrize("reader", [open_catalog, ocr_connection])
def test_ancillary_readers_escape_catalog_and_attached_paths(
    tmp_path: Path, name: str, reader: Callable[[Path], sqlite3.Connection],
) -> None:
    """Research readers must select the exact files and never create or modify a catalog."""
    archive = tmp_path / name
    archive.mkdir()
    for filename in ("archive.sqlite3", "search.sqlite3"):
        database = sqlite3.connect(archive / filename)
        try:
            with database:
                database.execute("CREATE TABLE evidence(value TEXT)")
                database.execute("INSERT INTO evidence VALUES (?)", (filename,))
        finally:
            database.close()
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    database = reader(archive)
    try:
        assert tuple(database.execute("SELECT value FROM evidence").fetchone()) == ("archive.sqlite3",)
        if reader is ocr_connection:
            assert database.execute("SELECT value FROM search.evidence").fetchone() == ("search.sqlite3",)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            database.execute("INSERT INTO evidence VALUES ('forbidden')")
    finally:
        database.close()
    assert {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before
