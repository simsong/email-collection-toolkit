# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Exercise processing storage and replay at forced-exit boundaries.
# Disposable archives retain real SQLite files and canonical message bytes.
# A child exits during actual schema DDL to test publication isolation.
# Read-only GUI validation rejects historical partial schemas without repair.
# SQLite tracing detects archive-wide work repeated inside message dispatch.
# No native windows or external mail accounts participate in these tests.

from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from mailarchiver.__main__ import IngestRequest, run_ingest
from mailarchiver.application import ApplicationController, ApplicationPreferencesStore, InvalidArchiveError
from mailarchiver.owner_rules import OwnerRules
from mailarchiver.processing.contracts import ProcessingPolicy
from mailarchiver.processing.production import ProductionPipeline
from mailarchiver.processing.store import connect
from mailarchiver.writer_lock import WriterLease


def test_processing_initialization_crash_does_not_publish_partial_schema(tmp_path: Path) -> None:
    """Quit during first-import DDL leaves the optional database absent and initialization retryable."""
    controller = ApplicationController(ApplicationPreferencesStore(tmp_path / "preferences.json"))
    archive = tmp_path / "archive"
    controller.create_document(archive)
    # SQLite tracing injects a process exit at an exact real DDL boundary.
    code = """
import os, sqlite3, sys
from pathlib import Path
from mailarchiver.processing.store import connect
original = sqlite3.connect
def observed(*args, **kwargs):
    database = original(*args, **kwargs)
    def trace(sql):
        if sql.lstrip().startswith('CREATE TABLE jobs'):
            os._exit(23)
    database.set_trace_callback(trace)
    return database
sqlite3.connect = observed
connect(Path(sys.argv[1]), create=True, production=True)
"""
    result = subprocess.run([sys.executable, "-c", code, str(archive)], check=False, timeout=10)
    assert result.returncode == 23
    assert not (archive / "processing.sqlite3").exists()
    assert controller.open_document(archive).path == archive
    database = connect(archive, create=True, production=True)
    try:
        assert database.execute("SELECT count(*) FROM jobs").fetchone() == (0,)
        assert database.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    finally:
        database.close()
    assert controller.open_document(archive).path == archive


def test_gui_rejects_version_only_processing_schema(tmp_path: Path) -> None:
    """An interrupted old initializer's version marker cannot masquerade as a usable queue."""
    controller = ApplicationController(ApplicationPreferencesStore(tmp_path / "preferences.json"))
    archive = tmp_path / "archive"
    controller.create_document(archive)
    with sqlite3.connect(archive / "processing.sqlite3") as database:
        database.executescript("CREATE TABLE schema_info(version INTEGER); INSERT INTO schema_info VALUES(2);")
    original = (archive / "processing.sqlite3").read_bytes()
    with pytest.raises(InvalidArchiveError, match="incomplete processing schema"):
        controller.open_document(archive)
    with pytest.raises(ValueError, match="incomplete processing schema"):
        connect(archive)
    assert (archive / "processing.sqlite3").read_bytes() == original


def test_replay_dispatch_does_not_rescan_invocation_history(tmp_path: Path) -> None:
    """Message-boundary replay preserves filing without computing an archive report per message."""
    source = tmp_path / "source"
    source.mkdir()
    for number in range(3):
        (source / f"{number}.eml").write_bytes(
            f"From: owner@example.test\nDate: Tue, 02 Jan 2024 10:00:00 +0000\nMessage-ID: <{number}@example.test>\n\n{number}\n".encode())
    archive = tmp_path / "archive"
    run_ingest(IngestRequest(archive=archive, roots=[str(source)], scan_policy="not-scanned", continue_content=False,
                            owner_rules=OwnerRules(include=["owner@example.test"])), terminal=False)
    catalog = sqlite3.connect(archive / "archive.sqlite3")
    search = sqlite3.connect(archive / "search.sqlite3")
    database = connect(archive)
    policy = ProcessingPolicy.model_validate_json(database.execute("SELECT value FROM processing_settings WHERE name='policy'").fetchone()[0])
    database.close()

    def reject_duplicate_publication(*_args) -> int:
        raise AssertionError("replay must reuse the already published message")

    try:
        with WriterLease.acquire(archive, str(archive), "replay probe", "test", "test"):
            pipeline = ProductionPipeline(archive, catalog, search, policy, reject_duplicate_publication)
            try:
                pipeline.reprocess()
                statements: list[str] = []
                pipeline.database.set_trace_callback(statements.append)
                pipeline.resume_ingest()
                assert not any("FROM invocations WHERE kind=" in sql or "interrupted worker" in sql for sql in statements)
                assert pipeline.database.execute("SELECT count(*) FROM jobs WHERE status='completed'").fetchone()[0] >= 3
                assert pipeline.database.execute("SELECT count(*) FROM jobs WHERE status='pending' AND json_extract(item_json,'$.pipeline') IN ('ingest','message')").fetchone() == (0,)
                assert catalog.execute("SELECT count(*) FROM messages").fetchone() == (3,)
                pipeline.statistics()
                assert any("FROM invocations WHERE kind=" in sql for sql in statements)
            finally:
                pipeline.close()
    finally:
        catalog.close()
        search.close()
