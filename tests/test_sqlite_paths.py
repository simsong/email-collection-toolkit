# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Requirement: mapped drives and UNC paths open with standard Python SQLite.
# Check URI authority handling independently of a particular network share.
# Exercise actual databases whose names contain URI-reserved characters.
# Read-only connections and attached databases must reject attempted writes.
# Missing databases must never be created by a reader or recovery connection.
# Use only temporary synthetic databases, preserving their bytes after reading.
"""Regression checks for portable SQLite archive filenames."""
from contextlib import closing
from pathlib import Path, PureWindowsPath
import sqlite3
from urllib.parse import urlsplit, unquote

import pytest

from mailarchiver.reader_fixture import check_reader
from mailarchiver.sqlite_paths import sqlite_uri


@pytest.mark.parametrize("path, expected", [
    (PureWindowsPath("V:/mail-archive.mailarchive/archive.sqlite3"), "/V:/mail-archive.mailarchive/archive.sqlite3"),
    (PureWindowsPath("//server/share/mail #100%.mailarchive/archive.sqlite3"),
     "//server/share/mail #100%.mailarchive/archive.sqlite3"),
])
def test_windows_sqlite_uri_has_no_network_authority(path: PureWindowsPath, expected: str) -> None:
    parsed = urlsplit(sqlite_uri(path))
    assert parsed.netloc == ""
    assert unquote(parsed.path) == expected
    assert parsed.query == "mode=ro"
    assert parsed.fragment == ""


def test_reserved_characters_and_read_only_attachments(tmp_path: Path) -> None:
    path = tmp_path / "café #100% & archive.sqlite3"
    with closing(sqlite3.connect(path)) as database:
        database.execute("CREATE TABLE evidence(value TEXT)")
        database.execute("INSERT INTO evidence VALUES ('preserved')")
        database.commit()
    before = path.read_bytes()
    with closing(sqlite3.connect(sqlite_uri(path), uri=True)) as database:
        database.execute("ATTACH DATABASE ? AS attached", (sqlite_uri(path),))
        assert database.execute("SELECT value FROM attached.evidence").fetchone() == ("preserved",)
        for schema in ("main", "attached"):
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                database.execute(f"DELETE FROM {schema}.evidence")
    assert path.read_bytes() == before
    missing = tmp_path / "absent.sqlite3"
    for mode in ("ro", "rw"):
        with pytest.raises(sqlite3.OperationalError):
            sqlite3.connect(sqlite_uri(missing, mode=mode), uri=True)
        assert not missing.exists()


def test_reader_archive_under_uri_reserved_path(tmp_path: Path) -> None:
    # Full search, MIME rendering and byte-exact export use the escaped path.
    assert check_reader(tmp_path / "café #100% & reader").passed
