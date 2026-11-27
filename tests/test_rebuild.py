# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Requirements: Rebuild Databases rebuilds derived data into new files and swaps them in safely.

`rebuild_archive` backs `ect rebuild` and File → Rebuild Databases…. On a real
fixture archive these tests check that it re-derives catalog fields from the stored
bytes (repairing messages stored with a leading ">From " envelope line), keeps message
numbers, provenance, non-parse defects and import-time context dates, builds a fresh
search index, reports byte progress, publishes by renaming the live files to `.old`,
and that stopping leaves the live databases untouched with no `.new` files behind.
"""

from __future__ import annotations

import hashlib
import os
import signal
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mailarchiver.ingest_status import read_ingest_history
from mailarchiver.mbox import mbox_directory
from mailarchiver.rebuild import RebuildInterrupted, RebuildProgress, rebuild_archive, worker_pool
from mailarchiver.search import prepare_search_message
from tests.test_gui_service import MULTIPART_MESSAGE, make_gui_archive

ENVELOPED = b">From sender@example.net Fri Dec  5 08:49:19 2003\n" + MULTIPART_MESSAGE.replace(
    b"<multipart@example>", b"<enveloped@example>")
UNDATED = b"Message-ID: <undated@example>\nFrom: sender@example.net\nSubject: undated\n\nno date here\n"
ENVELOPED_PK, UNDATED_PK = 3, 4
DATABASES = ("archive.sqlite3", "search.sqlite3")


def _archive(tmp_path: Path) -> Path:
    """Build an archive whose catalog holds the values a broken import would have recorded."""
    enveloped = hashlib.sha256(ENVELOPED).hexdigest()
    archive = make_gui_archive(tmp_path, (
        (ENVELOPED, enveloped, "multipart message", "2003-12-03T16:38:18+00:00"),
        (UNDATED, "undated@example", "undated", "1999-01-01T00:00:00+00:00"),
    ))
    with sqlite3.connect(archive / "archive.sqlite3") as catalog:
        catalog.execute("UPDATE messages SET date_source = 'previous-message' WHERE message_pk = ?", (ENVELOPED_PK,))
        catalog.execute("UPDATE messages SET date_source = 'path-year' WHERE message_pk = ?", (UNDATED_PK,))
        catalog.execute("DELETE FROM recipients WHERE message_pk = ?", (ENVELOPED_PK,))
        catalog.executemany("INSERT INTO metadata_defects(message_pk, field, detail) VALUES (?, ?, ?)", [
            (ENVELOPED_PK, "From", "used quoted embedded MBOX From header"),
            (ENVELOPED_PK, "antivirus", "not-scanned: explicitly imported without antivirus scanning"),
        ])
        source_file, run = catalog.execute("SELECT source_file_pk, run_pk FROM observations LIMIT 1").fetchone()
        catalog.execute(
            "INSERT INTO observations(run_pk, message_pk, source_file_pk, source_offset, raw_sha256, disposition, detail) "
            "VALUES (?, ?, ?, 4242, ?, 'archived', 'Archive')", (run, ENVELOPED_PK, source_file, enveloped))
    with sqlite3.connect(archive / "search.sqlite3") as search:
        search.execute("UPDATE message_metadata SET preview = '>From stale preview' WHERE sha256 = ?", (enveloped,))
    return archive


def _digests(archive: Path, suffix: str = "") -> dict[str, str]:
    return {name: hashlib.sha256((archive / f"{name}{suffix}").read_bytes()).hexdigest() for name in DATABASES}


def test_indexer_parses_mail_after_a_leading_envelope_line() -> None:
    """Requirement: a stored ">From " first line no longer becomes the indexed preview."""
    prepared = prepare_search_message(ENVELOPED, False)
    assert prepared.sha256 == hashlib.sha256(ENVELOPED).hexdigest()
    assert prepared.subject == "multipart message"
    assert prepared.preview == "Plain version."


def test_rebuild_repairs_catalog_and_index_and_keeps_old_databases(tmp_path: Path) -> None:
    """Requirement: re-derived fields come from the bytes; provenance and context survive; old files kept."""
    archive = _archive(tmp_path)
    before = _digests(archive)
    with sqlite3.connect(archive / "archive.sqlite3") as catalog:
        observations = catalog.execute("SELECT * FROM observations ORDER BY observation_pk").fetchall()
        record_bytes = catalog.execute("SELECT SUM(byte_length) FROM locations").fetchone()[0]
    mailbox_bytes = sum(path.stat().st_size for path in mbox_directory(archive).glob("*.mbox"))
    seen: list[RebuildProgress] = []

    result = rebuild_archive(archive, workers=2, observer=seen.append)

    assert (result.messages, result.failures, result.processing_database) == (4, 0, False)
    assert result.changed >= 1
    assert _digests(archive, ".old") == before
    assert not list(archive.glob("*.sqlite3.new.*"))
    with sqlite3.connect(archive / "archive.sqlite3") as catalog:
        assert catalog.execute(
            "SELECT message_id_normalized, subject, date_utc, date_source FROM messages WHERE message_pk = ?",
            (ENVELOPED_PK,)).fetchone() == ("enveloped@example", "multipart message", "2024-01-04T10:00:00+00:00", "date")
        assert catalog.execute(
            "SELECT a.address, r.role FROM recipients r JOIN email_addresses a USING (address_pk) WHERE message_pk = ?",
            (ENVELOPED_PK,)).fetchall() == [("recipient@example.net", "to")]
        assert catalog.execute("SELECT field FROM metadata_defects WHERE message_pk = ? ORDER BY field",
                               (ENVELOPED_PK,)).fetchall() == [("antivirus",)]
        assert catalog.execute("SELECT date_utc, date_source FROM messages WHERE message_pk = ?",
                               (UNDATED_PK,)).fetchone() == ("1999-01-01T00:00:00+00:00", "path-year")
        assert catalog.execute("SELECT * FROM observations ORDER BY observation_pk").fetchall() == observations
    with sqlite3.connect(archive / "search.sqlite3") as search:
        previews = dict(search.execute("SELECT sha256, preview FROM message_metadata"))
    assert previews[hashlib.sha256(ENVELOPED).hexdigest()] == "Plain version."
    assert len(previews) == 4
    phases = {progress.phase.split(" (")[0]: progress for progress in seen}
    assert phases["Copying catalog"].total_bytes == (archive / "archive.sqlite3.old").stat().st_size
    assert phases["Checking mailboxes for changes"].total_bytes == mailbox_bytes
    assert phases["Rebuilding databases"].total_bytes == record_bytes
    assert seen[-1].completed_bytes == seen[-1].total_bytes == record_bytes

    activity, = read_ingest_history(archive).statuses  # Shown in the Activity window as its own run.
    assert (activity.activity_kind, activity.state, activity.run_pk, activity.processed_messages, activity.percent) == (
        "rebuild", "completed", 0, 4, 100.0)
    assert activity.phase.startswith("Rebuilt from 4 messages")

    rebuild_archive(archive, workers=1)  # A second run replaces the earlier .old files.
    assert _digests(archive, ".old") != before
    assert len(read_ingest_history(archive).statuses) == 2


@pytest.mark.parametrize("stop_after", [0, 2, 3])
def test_stopping_keeps_the_live_databases(tmp_path: Path, stop_after: int) -> None:
    """Requirement: stopping (Ctrl-C, GUI Quit) leaves live databases unchanged and no .new files."""
    archive = _archive(tmp_path)
    before = _digests(archive)
    polls: list[int] = []

    def should_stop() -> bool:
        polls.append(1)
        return len(polls) > stop_after

    with pytest.raises(RebuildInterrupted) as interrupted:
        rebuild_archive(archive, workers=1, observer=lambda _progress: None, should_stop=should_stop)
    assert not interrupted.value.published
    assert _digests(archive) == before
    activity, = read_ingest_history(archive).statuses
    assert (activity.activity_kind, activity.state) == ("rebuild", "interrupted")
    assert not list(archive.glob("*.sqlite3.new.*")) and not list(archive.glob("*.old"))


def test_mailbox_catalog_mismatch_fails_closed(tmp_path: Path) -> None:
    """Requirement: an uncatalogued MBOX record aborts before anything is published."""
    archive = _archive(tmp_path)
    before = _digests(archive)
    mailbox = next(mbox_directory(archive).glob("*.mbox"))
    with mailbox.open("ab") as output:
        output.write(b"\nFrom extra@example.net Thu Jan  4 10:00:00 2024\nSubject: uncatalogued\n\nbody\n")
    with pytest.raises(RuntimeError, match="count mismatch"):
        rebuild_archive(archive, workers=1, observer=lambda _progress: None)
    activity, = read_ingest_history(archive).statuses
    assert activity.state == "failed" and "count mismatch" in (activity.failure_detail or "")
    assert _digests(archive) == before
    assert not list(archive.glob("*.sqlite3.new.*"))


def test_mailbox_unchanged_since_checkpoint_is_not_reread(tmp_path: Path) -> None:
    """Requirement: a mailbox untouched since the last checkpoint is trusted; Revalidate Archive is the full recheck.

    An extra record appended before the checkpoint, with the catalog's recorded size
    updated to match, would fail a full count; it is accepted because the file's
    timestamp, size and catalogued count all agree with the checkpoint.
    """
    archive = _archive(tmp_path)
    mailbox = next(mbox_directory(archive).glob("*.mbox"))
    with mailbox.open("ab") as output:
        output.write(b"\nFrom extra@example.net Thu Jan  4 10:00:00 2024\nSubject: uncatalogued\n\nbody\n")
    with sqlite3.connect(archive / "archive.sqlite3") as catalog:
        catalog.execute("UPDATE mbox_generations SET byte_count = ?, message_count = "
                        "(SELECT COUNT(*) FROM locations) WHERE filename = ?", (mailbox.stat().st_size, mailbox.name))
    checkpoint = datetime.now(UTC)
    before = (checkpoint - timedelta(hours=1)).timestamp()
    os.utime(mailbox, (before, before))
    info = archive / "bag-info.txt"
    lines = [line for line in (info.read_text(encoding="utf-8").splitlines() if info.exists() else [])
             if not line.startswith("Bagging-Timestamp:")]
    info.write_text("\n".join([*lines, f"Bagging-Timestamp: {checkpoint.isoformat()}"]) + "\n", encoding="utf-8")

    assert rebuild_archive(archive, workers=1, observer=lambda _progress: None).messages == 4

    later = (checkpoint + timedelta(minutes=1)).timestamp()  # Modified after the checkpoint: counted again.
    os.utime(archive / "data/mbox" / mailbox.name, (later, later))
    with pytest.raises(RuntimeError, match="count mismatch"):
        rebuild_archive(archive, workers=1, observer=lambda _progress: None)


def _process_id() -> int:
    return os.getpid()


def test_worker_processes_ignore_ctrl_c() -> None:
    """Requirement: Ctrl-C in the terminal does not kill or traceback rebuild workers; the parent stops them."""
    pool = worker_pool(1)
    try:
        worker = pool.submit(_process_id).result(timeout=60)
        os.kill(worker, signal.SIGINT)
        assert pool.submit(_process_id).result(timeout=60) == worker  # Same worker, still serving work.
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
