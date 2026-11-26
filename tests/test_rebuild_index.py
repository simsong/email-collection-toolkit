# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Requirements: rebuild-index measures progress in bytes, can be stopped, and repairs enveloped mail.

`rebuild_search_index` is shared by `ect rebuild-index` and File → Rebuild Search
Index. These tests drive it on a real fixture archive: an observer (the GUI path)
must see byte totals known up front and reach them exactly; a stop request must
discard the replacement and keep the old index; and messages whose canonical bytes
start with a ">From " envelope line must be indexed from their real headers/body.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from mailarchiver.__main__ import RebuildIndexInterrupted, RebuildIndexProgress, rebuild_search_index
from mailarchiver.mbox import mbox_directory
from mailarchiver.search import prepare_search_message
from tests.test_gui_service import MULTIPART_MESSAGE, make_gui_archive

ENVELOPED = b">From sender@example.net Fri Dec  5 08:49:19 2003\n" + MULTIPART_MESSAGE.replace(
    b"<multipart@example>", b"<enveloped@example>")


def _archive(tmp_path: Path) -> Path:
    digest = hashlib.sha256(ENVELOPED).hexdigest()
    return make_gui_archive(tmp_path, ((ENVELOPED, digest, "multipart message", "2024-01-05T10:00:00+00:00"),))


def test_indexer_parses_mail_after_a_leading_envelope_line() -> None:
    """Requirement: a stored ">From " first line no longer becomes the indexed preview and blank subject."""
    prepared = prepare_search_message(ENVELOPED, False)
    assert prepared.sha256 == hashlib.sha256(ENVELOPED).hexdigest()
    assert prepared.subject == "multipart message"
    assert prepared.preview == "Plain version."


def test_rebuild_reports_byte_progress_and_repairs_previews(tmp_path: Path) -> None:
    """Requirement: progress totals are bytes known before reading, and the rebuilt index is correct."""
    archive = _archive(tmp_path)
    with sqlite3.connect(archive / "search.sqlite3") as search:
        search.execute("UPDATE message_metadata SET preview = '>From stale preview'")
    with sqlite3.connect(archive / "archive.sqlite3") as catalog:
        record_bytes = catalog.execute("SELECT SUM(byte_length) FROM locations").fetchone()[0]
    mailbox_bytes = sum(path.stat().st_size for path in mbox_directory(archive).glob("*.mbox"))
    seen: list[RebuildIndexProgress] = []
    rebuild_search_index(archive, False, 2, observer=seen.append)
    phases = {progress.phase.split(" (")[0]: progress for progress in seen}
    assert phases["Checking canonical mailboxes"].total_bytes == mailbox_bytes
    assert phases["Rebuilding search index"].total_bytes == record_bytes
    assert seen[-1].completed_bytes == seen[-1].total_bytes == record_bytes
    assert all(later.completed_bytes >= earlier.completed_bytes
               for earlier, later in zip(seen, seen[1:]) if earlier.phase == later.phase)
    with sqlite3.connect(archive / "search.sqlite3") as search:
        previews = {row[0] for row in search.execute("SELECT preview FROM message_metadata")}
    assert previews == {"The report is ready.", "Plain version."}


@pytest.mark.parametrize("stop_after", [0, 1])
def test_stop_request_discards_the_replacement(tmp_path: Path, stop_after: int) -> None:
    """Requirement: stopping (GUI Quit) leaves the previous search index in place and no temp file."""
    archive = _archive(tmp_path)
    with sqlite3.connect(archive / "search.sqlite3") as search:
        search.execute("CREATE TABLE preserved(value TEXT)")
        search.execute("INSERT INTO preserved VALUES ('old index')")
    polls: list[int] = []

    def should_stop() -> bool:
        polls.append(1)
        return len(polls) > stop_after

    with pytest.raises(RebuildIndexInterrupted) as interrupted:
        rebuild_search_index(archive, False, 1, observer=lambda _progress: None, should_stop=should_stop)
    assert not interrupted.value.published
    assert not (archive / "search.sqlite3.tmp").exists()
    with sqlite3.connect(archive / "search.sqlite3") as search:
        assert search.execute("SELECT value FROM preserved").fetchone() == ("old index",)
