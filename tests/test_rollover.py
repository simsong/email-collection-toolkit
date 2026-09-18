# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""requirements.md: configurable rollover preserves bytes, offsets and recovery."""
from contextlib import closing
import hashlib
import mailbox
from pathlib import Path
import sqlite3

import pytest

from mailarchiver.archive_config import ArchiveConfig, save_archive_config
from mailarchiver.layout import mbox_directory
from mailarchiver.mbox import (
    PendingPublication, PublicationRecovery, add_message, frame_message,
    journal_publication, read_verified_location, recover_publication, rollover_destination,
)
from mailarchiver.standalone_verify import verify_archive
from mailarchiver.mboxrd import unquote
from tests.test_cli_processing import cli
from tests.test_end_to_end import mailbox_message_bytes


@pytest.mark.parametrize("sender,category", [("owner@example.test", "Sent"), ("sender@example.test", "Archive")])
def test_small_cli_rollover_and_restart(tmp_path: Path, sender: str, category: str) -> None:
    """Four exact 6 KiB messages at 20 KiB split 3+1, survive restart and verify."""
    archive, sources = tmp_path / "archive", tmp_path / "sources"
    sources.mkdir()
    save_archive_config(archive, ArchiveConfig(mbox_max_bytes=20 * 1024))
    owners = tmp_path / "owners.txt"
    owners.write_text("owner@example.test\n")
    records = []
    for number in range(4):
        header = (f"From: {sender}\nTo: reader@example.test\nDate: Tue, 02 Jan 2024 10:00:00 +0000\n"
                  f"Message-ID: <rollover-{number}@example.test>\nSubject: message {number}\n\n").encode()
        prefix = header + b">From quoted body\n"
        raw = prefix + b"x" * (6 * 1024 - len(prefix) - 2) + b"\xff\n"
        assert len(raw) == 6 * 1024
        records.append(raw)
        (sources / f"{number}.eml").write_bytes(raw)
    arguments = ("ingest", "--no-scan", "--workers", "1", "--owner-names-file", str(owners))
    cli(archive, *arguments, *(str(sources / f"{i}.eml") for i in range(3)))
    first = mbox_directory(archive) / f"2024-{category}1.mbox"
    first_bytes = first.read_bytes()
    cli(archive, *arguments, str(sources))
    second = first.with_name(f"2024-{category}2.mbox")
    assert first.read_bytes() == first_bytes
    assert [unquote(raw) for raw in mailbox_message_bytes(first)] == records[:3]
    assert [unquote(raw) for raw in mailbox_message_bytes(second)] == records[3:]
    assert first.stat().st_size < 20 * 1024 and second.stat().st_size < 20 * 1024
    with sqlite3.connect(archive / "archive.sqlite3") as database:
        assert database.execute("SELECT count(*) FROM messages").fetchone() == (4,)
    assert not verify_archive(archive)
    cli(archive, *arguments, str(sources))
    assert first.read_bytes() == first_bytes
    assert len(list(mbox_directory(archive).glob("*.mbox"))) == 2
    assert not verify_archive(archive)


@pytest.mark.parametrize("name", ["2024-Sent1.mbox", "2024-Archive1.mbox", "INFECTED1.mbox"])
def test_rollover_framing_oversize_and_journal(tmp_path: Path, name: str) -> None:
    """Use real writes for framing thresholds, indivisible mail and orphan recovery."""
    first = tmp_path / "data" / "mbox" / name
    first.parent.mkdir(parents=True)
    raw = b"Subject: boundary\r\n\r\nFrom body\r\n>From literal"
    framed = frame_message(raw)
    with closing(mailbox.mbox(first)) as box:
        location = add_message(box, first, raw)
    record_bytes = first.stat().st_size
    assert record_bytes == len(framed) + 2
    assert rollover_destination(first, framed, 2 * record_bytes + 1) == first
    second = rollover_destination(first, framed, 2 * record_bytes)
    assert second.name == name.replace("1.mbox", "2.mbox")
    assert read_verified_location(first, location, hashlib.sha256(raw).hexdigest()) == raw
    # Even an oversized single message is preserved whole in the empty next part.
    assert rollover_destination(first, framed, 1) == second
    with closing(mailbox.mbox(second)) as box:
        add_message(box, second, raw)
    third = rollover_destination(first, framed, 1)
    assert third.name == name.replace("1.mbox", "3.mbox")
    with closing(mailbox.mbox(third)) as box:
        add_message(box, third, raw)
    journal_publication(tmp_path, PendingPublication(filename=third.name, prior_size=0,
        file_existed=False, message_id="orphan", sha256=hashlib.sha256(raw).hexdigest()))
    from mailarchiver.catalog import create_catalog, create_search
    with closing(create_catalog(tmp_path / "archive.sqlite3")) as catalog, closing(create_search(tmp_path / "search.sqlite3")) as search:
        assert recover_publication(tmp_path, catalog, search) == PublicationRecovery.ROLLED_BACK
    assert not third.exists()
    assert first.stat().st_size == second.stat().st_size == record_bytes
