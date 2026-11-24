# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Requirement (#202): processing checkpoints may share one SQLite commit, but a
# derived database (search) written inside the group must commit before the
# processing database marks that work completed, so a crash between the two
# commits only repeats idempotent derived writes.

from __future__ import annotations

import os

import sqlite3
from pathlib import Path

import pytest

from mailarchiver.processing import store
from mailarchiver.processing.store import commit_first, flush_group, grouped, snapshot_bytes, unit


def _database(path: Path, log: list[str], name: str) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE rows(value TEXT)")
    connection.commit()
    connection.set_trace_callback(lambda statement: log.append(name) if statement == "COMMIT" else None)
    return connection


def _visible(path: Path) -> list[str]:
    with sqlite3.connect(path) as reader:
        return [row[0] for row in reader.execute("SELECT value FROM rows ORDER BY rowid")]


def test_units_share_one_commit_and_dependent_commits_first(tmp_path: Path) -> None:
    log: list[str] = []
    processing = _database(tmp_path / "processing.sqlite3", log, "processing")
    search = _database(tmp_path / "search.sqlite3", log, "search")
    with grouped(search), commit_first(processing, search), grouped(processing):
        for value in ("a", "b"):
            with unit(processing):
                processing.execute("INSERT INTO rows VALUES(?)", (value,))
            with unit(search):
                search.execute("INSERT INTO rows VALUES(?)", (value,))
        assert not log
        assert _visible(tmp_path / "processing.sqlite3") == []
        flush_group(processing)
        assert log == ["search", "processing"]
        assert _visible(tmp_path / "search.sqlite3") == ["a", "b"]
        assert _visible(tmp_path / "processing.sqlite3") == ["a", "b"]
        with unit(processing):
            processing.execute("INSERT INTO rows VALUES('c')")
        with unit(search):
            search.execute("INSERT INTO rows VALUES('c')")
    assert log == ["search", "processing", "search", "processing"]
    assert _visible(tmp_path / "processing.sqlite3") == ["a", "b", "c"]


def test_failed_unit_rolls_back_alone_and_group_still_commits(tmp_path: Path) -> None:
    log: list[str] = []
    processing = _database(tmp_path / "processing.sqlite3", log, "processing")
    with pytest.raises(RuntimeError), grouped(processing):
        with unit(processing):
            processing.execute("INSERT INTO rows VALUES('kept')")
        with unit(processing):
            processing.execute("INSERT INTO rows VALUES('dropped')")
            raise RuntimeError("processor failed")
    assert _visible(tmp_path / "processing.sqlite3") == ["kept"]
    assert log == ["processing"]


def test_object_directory_is_synced_before_the_commit_that_references_it(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Objects written inside a group share one directory sync, issued before the commit."""
    log: list[str] = []
    processing = _database(tmp_path / "processing.sqlite3", log, "processing")
    monkeypatch.setattr(store, "sync_directory", lambda path: log.append(f"sync {path.name}"))
    archive = tmp_path  # The databases live in the archive directory.
    with grouped(processing):
        for payload in (b"one", b"two"):
            reference = snapshot_bytes(archive, payload)
            with unit(processing):
                processing.execute("INSERT INTO rows VALUES(?)", (reference.sha256,))
        assert not log
    # Windows uses write-through replacement per object instead of a deferred directory sync.
    assert log == (["processing"] if os.name == "nt" else ["sync objects", "processing"])
    log.clear()
    with unit(processing):  # outside a group: the old per-object sync, then commit
        processing.execute("INSERT INTO rows VALUES(?)", (snapshot_bytes(archive, b"three").sha256,))
    assert log == ["processing"]


class _FailingCommit(sqlite3.Connection):
    def commit(self) -> None:
        raise sqlite3.OperationalError("database is locked")


def test_failed_dependent_commit_rolls_back_the_processing_group(tmp_path: Path) -> None:
    """A search commit that fails must not leave jobs marked completed for a later commit."""
    log: list[str] = []
    processing = _database(tmp_path / "processing.sqlite3", log, "processing")
    search = sqlite3.connect(tmp_path / "search.sqlite3", factory=_FailingCommit)
    search.execute("CREATE TABLE rows(value TEXT)")
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        with grouped(search), commit_first(processing, search), grouped(processing):
            with unit(processing):
                processing.execute("INSERT INTO rows VALUES('completed')")
            with unit(search):
                search.execute("INSERT INTO rows VALUES('derived')")
    assert not processing.in_transaction
    with processing:  # e.g. saving the policy at close
        pass
    assert _visible(tmp_path / "processing.sqlite3") == []


@pytest.mark.skipif(os.name == "nt", reason="Windows replaces objects write-through, with no deferred sync")
def test_failed_directory_sync_stays_pending_and_rolls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log: list[str] = []
    processing = _database(tmp_path / "processing.sqlite3", log, "processing")
    archive = tmp_path  # The databases live in the archive directory.
    def failing(_path: Path) -> None:
        raise OSError("sync failed")
    monkeypatch.setattr(store, "sync_directory", failing)
    with pytest.raises(OSError, match="sync failed"):
        with grouped(processing):
            reference = snapshot_bytes(archive, b"object")
            with unit(processing):
                processing.execute("INSERT INTO rows VALUES(?)", (reference.sha256,))
    assert not processing.in_transaction and _visible(tmp_path / "processing.sqlite3") == []
    assert archive / "objects" in store._UNSYNCED_DIRECTORIES  # pylint: disable=protected-access
    monkeypatch.setattr(store, "sync_directory", lambda path: log.append(f"sync {path.name}"))
    with unit(processing):
        processing.execute("INSERT INTO rows VALUES('later')")
    assert log == ["sync objects", "processing"]
    assert not store._UNSYNCED_DIRECTORIES  # pylint: disable=protected-access


@pytest.mark.skipif(os.name == "nt", reason="Windows replaces objects write-through, with no deferred sync")
def test_one_archives_failed_sync_does_not_block_another_archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The GUI can import into two archives at once; pending syncs belong to their archive."""
    log: list[str] = []
    broken, healthy = tmp_path / "broken", tmp_path / "healthy"
    broken.mkdir()
    healthy.mkdir()
    first = _database(broken / "processing.sqlite3", log, "broken")
    second = _database(healthy / "processing.sqlite3", log, "healthy")
    def sync(path: Path) -> None:
        if path.parent == broken:
            raise OSError("sync failed")
        log.append(f"sync {path.parent.name}")
    monkeypatch.setattr(store, "sync_directory", sync)
    with pytest.raises(OSError, match="sync failed"):
        with grouped(first):
            reference = snapshot_bytes(broken, b"object")
            with unit(first):
                first.execute("INSERT INTO rows VALUES(?)", (reference.sha256,))
    with grouped(second):
        reference = snapshot_bytes(healthy, b"object")
        with unit(second):
            second.execute("INSERT INTO rows VALUES(?)", (reference.sha256,))
    assert log == ["sync healthy", "healthy"]
    assert _visible(healthy / "processing.sqlite3") == [reference.sha256]
    store._UNSYNCED_DIRECTORIES.clear()  # pylint: disable=protected-access
