# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Requirement (#202): processing checkpoints may share one SQLite commit, but a
# derived database (search) written inside the group must commit before the
# processing database marks that work completed, so a crash between the two
# commits only repeats idempotent derived writes.

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from mailarchiver.processing.store import commit_first, flush_group, grouped, unit


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
