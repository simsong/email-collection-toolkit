# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Requirement (#216): on macOS, fsync() does not flush the drive's write cache.
# Canonical durability points use F_FULLFSYNC there (falling back to fsync when
# a file system refuses it), and SQLite connections enable fullfsync.
"""Durability primitives request a full drive flush where the platform needs one."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from mailarchiver import storage_sync

_FSYNC = os.fsync


def _fake_darwin(monkeypatch: pytest.MonkeyPatch, calls: list[str], *, refuse: bool = False) -> None:
    def fcntl(_descriptor: int, command: int) -> int:
        calls.append(f"fcntl {command}")
        if refuse:
            raise OSError("not supported")
        return 0

    monkeypatch.setattr(storage_sync, "_FULLFSYNC", 51)
    monkeypatch.setattr(storage_sync, "fcntl", SimpleNamespace(fcntl=fcntl))
    monkeypatch.setattr(os, "fsync", lambda descriptor: (calls.append("fsync"), _FSYNC(descriptor))[1])


@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptors")
def test_full_flush_is_requested_and_falls_back_to_fsync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    _fake_darwin(monkeypatch, calls)
    storage_sync.sync_directory(tmp_path)
    assert calls == ["fcntl 51"]
    calls.clear()
    _fake_darwin(monkeypatch, calls, refuse=True)
    with (tmp_path / "data").open("wb") as output:
        storage_sync.durable_sync(output.fileno())
    assert calls == ["fcntl 51", "fsync"]


def test_sqlite_connections_enable_fullfsync_only_where_needed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(storage_sync, "_FULLFSYNC", None)
    with storage_sync.durable_sqlite(sqlite3.connect(tmp_path / "plain.sqlite3")) as database:
        assert database.execute("PRAGMA fullfsync").fetchone() == (0,)
    monkeypatch.setattr(storage_sync, "_FULLFSYNC", 51)
    with storage_sync.durable_sqlite(sqlite3.connect(tmp_path / "full.sqlite3")) as database:
        assert database.execute("PRAGMA fullfsync").fetchone() == (1,)
        assert database.execute("PRAGMA checkpoint_fullfsync").fetchone() == (1,)
