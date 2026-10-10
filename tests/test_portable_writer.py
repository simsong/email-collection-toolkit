# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Exercise creation and ingestion through the same services used by the desktop.
# All input is purpose-made mail outside a newly created destination archive.
# Verify original byte hashes, duplicate IDs, malformed MIME, and invalid text.
# Verify autosave exclusion, repeated-source idempotence, and complete fixity.
# These unscanned fixtures test storage; real ClamAV acceptance is separate.
"""Cross-platform writer preservation and source-safety regressions."""
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
from pathlib import Path
import sqlite3
from threading import Event

import pytest

from mailarchiver.__main__ import IngestRequest, run_ingest
from mailarchiver.application import create_empty_archive
from mailarchiver.gui_service import describe_message
from mailarchiver.mbox import MboxLocation, read_verified_location
from mailarchiver.owner_rules import OwnerRules
from mailarchiver.reader_fixture import inventory
from mailarchiver.standalone_verify import verify_archive
from mailarchiver.storage_sync import replace_file


@pytest.mark.skipif(os.name != "nt", reason="Windows DELETE-sharing publication semantics")
@pytest.mark.parametrize("release_reader", [True, False])
def test_atomic_publication_waits_for_readers_without_losing_files(tmp_path: Path, release_reader: bool) -> None:
    """GUI status polling may briefly block replacement; persistent denial must still fail."""
    source, destination = tmp_path / "complete.tmp", tmp_path / "status.json"
    source.write_bytes(b"complete replacement")
    destination.write_bytes(b"previous status")
    started = Event()
    def publish() -> None:
        started.set()
        replace_file(source, destination)
    with destination.open("rb") as reader, ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(publish)
        assert started.wait(2)
        if release_reader:
            Event().wait(0.05)
            assert not future.done()
            assert reader.read() == b"previous status"
            reader.close()
            future.result(timeout=4)
            assert destination.read_bytes() == b"complete replacement" and not source.exists()
        else:
            with pytest.raises(PermissionError):
                future.result(timeout=4)
            assert source.read_bytes() == b"complete replacement"
            assert reader.read() == b"previous status"


def test_create_import_preserves_original_bytes_and_source_idempotence(tmp_path: Path) -> None:
    source = tmp_path / "2024"  # Existing year-directory fallback for missing dates.
    source.mkdir()
    messages = (
        b"Message-ID: <duplicate@example.test>\r\nFrom: sender@example.test\r\nSubject: CRLF\r\n\r\nFrom original\r\n>From quoted\r\nunterminated",
        b"Message-ID: <duplicate@example.test>\nFrom: sender@example.test\nSubject: Different content\n\nFrom body\n>From quoted\n",
        b"From: sender@example.test\nSubject: Invalid UTF8\nContent-Type: text/plain; charset=utf-8\n\n\xff\xfe\x80\n",
        b"From: sender@example.test\nSubject: Broken MIME\nContent-Type: multipart/mixed; boundary=absent\n\nRetained malformed mail",
    )
    for index, raw in enumerate(messages):
        (source / f"{index}.eml").write_bytes(raw)
    (source / "autosave.eml").write_bytes(b"From: sender@example.test\nX-Apple-Auto-Saved: 1\nSubject: Excluded draft\n\nDraft\n")
    before = inventory(source)
    archive = tmp_path / "café collection.mailarchive"
    create_empty_archive(archive)
    request = IngestRequest(archive=archive, roots=[str(source)], scan_policy="not-scanned",
                            owner_rules=OwnerRules(include=["sender@example.test"]))
    run_ingest(request)
    run_ingest(request)
    expected = {hashlib.sha256(raw).hexdigest(): raw for raw in messages}
    with closing(sqlite3.connect(archive / "archive.sqlite3")) as database:
        rows = database.execute(
            "SELECT m.message_pk,m.sha256,g.filename,l.byte_offset,l.byte_length "
            "FROM messages m JOIN locations l USING(message_pk) JOIN mbox_generations g USING(generation_pk)"
        ).fetchall()
    assert len(rows) == len(messages)
    assert {row[1] for row in rows} == set(expected)
    for message_pk, digest, filename, offset, length in rows:
        assert read_verified_location(archive / "data/mbox" / filename,
                                      MboxLocation(byte_offset=offset, byte_length=length), digest) == expected[digest]
        assert describe_message(archive, message_pk).subject
    assert not verify_archive(archive)
    assert inventory(source) == before
