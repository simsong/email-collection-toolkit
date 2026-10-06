# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Exercise the transitional archive engine through its real private pipe protocol.
# Synthetic inputs pass through production creation, import and identity services.
# The tests verify byte preservation, owner-policy conflicts and writer exclusion.
# Pipe closure must reap the helper without a Python GUI or visible windows.
# No mocking, real mail, scanner installation or external accounts are involved.
from __future__ import annotations

from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from typing import Iterator

from pydantic import JsonValue
import pytest

from mailarchiver.document_options import DocumentOptions
from mailarchiver.owner_rules import OwnerRules
from mailarchiver.rust_engine import Engine, ImportDefaults, Reply, Request
from mailarchiver.writer_lock import WriterLease


class Peer:
    """A test peer using the same strict line protocol as the Rust owner."""

    def __init__(self, process: subprocess.Popen[str]) -> None:
        self.process = process
        self.identifier = 0

    def call(self, method: str, *args: JsonValue) -> JsonValue:
        self.identifier += 1
        request = Request.model_validate({"id": self.identifier, "method": method, "args": list(args)})
        assert self.process.stdin is not None and self.process.stdout is not None
        self.process.stdin.write(request.model_dump_json() + "\n")
        self.process.stdin.flush()
        reply = Reply.model_validate_json(self.process.stdout.readline())
        assert reply.id == self.identifier
        if reply.error:
            raise ValueError(reply.error)
        return reply.result


@pytest.mark.skipif(os.name != "posix", reason="real FIFO coordinates the configuration read")
def test_import_defaults_keep_the_revision_of_the_displayed_rules(tmp_path: Path) -> None:
    """Owner policy: a concurrent edit while reading source defaults must block import."""
    source = tmp_path / "source"
    source.mkdir()
    names = source / "owner-names.txt"
    os.mkfifo(names)
    engine = Engine(tmp_path / "snapshot.mailarchive")
    engine.call(Request(id=1, method="create"))
    options = DocumentOptions(engine.archive)
    revision = options.state().revision
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(engine.call, Request(id=2, method="import_defaults", args=[str(source)]))
        # Opening the real FIFO waits until config.yaml has already been read.
        with names.open("w", encoding="utf-8") as output:
            with WriterLease.acquire(engine.archive, str(engine.archive), "concurrent edit", "snapshot", "test") as lease:
                options.save(OwnerRules(include=["new@example.test"]), lease, revision)
            output.write("old@example.test\n")
        defaults = ImportDefaults.model_validate(result.result(timeout=10))
    assert defaults.include == ["old@example.test"]
    assert defaults.revision == revision
    with pytest.raises(ValueError, match="changed in another window"):
        engine.call(Request(id=3, method="start_import", args=[{
            "source": str(source), "include": "old@example.test", "revision": defaults.revision,
            "scan_policy": "not-scanned",
        }]))
    assert options.state().include == ["new@example.test"]
    assert not engine.job.active


@contextmanager
def peer(archive: Path, tmp_path: Path) -> Iterator[Peer]:
    with (tmp_path / "engine.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen([sys.executable, "-m", "mailarchiver.rust_engine", str(archive)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True,
                                   start_new_session=os.name == "posix")
        try:
            yield Peer(process)
        finally:
            assert process.stdin is not None
            process.stdin.close()
            process.wait(timeout=6)
            if process.stdout is not None:
                process.stdout.close()


def test_archive_engine_import_policy_identity_and_shutdown(tmp_path: Path) -> None:
    """Rust migration: the real engine preserves canonical bytes and serialized edits."""
    source = tmp_path / "source"
    source.mkdir()
    raw = b"From: Alice Example <alice@example.test>\r\nTo: bob@example.test\r\nSubject: preservation fixture\r\nMessage-ID: <rust-engine@example.test>\r\nDate: Tue, 3 Jan 2023 12:00:00 +0000\r\n\r\nOriginal bytes.\r\nFrom quoting.\r\n"
    input_path = source / "message.eml"
    input_path.write_bytes(raw)
    archive = tmp_path / "new.mailarchive"
    with peer(archive, tmp_path) as engine:
        assert engine.call("ping") is True
        assert engine.call("create") is True
        defaults = engine.call("import_defaults", str(source))
        assert isinstance(defaults, dict)
        revision = defaults["revision"]
        assert isinstance(revision, str)
        with pytest.raises(ValueError, match="neither may contain"):
            engine.call("import_defaults", str(archive))
        assert engine.call("start_import", {"source": str(source), "include": "alice@example.test", "exclude": "",
                          "revision": revision, "scan_policy": "not-scanned", "index_attachments": True}) is True
        deadline = time.monotonic() + 60
        while True:
            state = engine.call("job_status")
            assert isinstance(state, dict)
            if not state["active"]:
                assert state["error"] is None, state
                break
            assert time.monotonic() < deadline, state
            time.sleep(.05)
        with sqlite3.connect(archive / "archive.sqlite3") as database:
            assert database.execute("SELECT sha256,category FROM messages").fetchall() == [(sha256(raw).hexdigest(), "Sent")]
        assert input_path.read_bytes() == raw
        assert engine.call("history")
        names = engine.call("identity_query", "name", {})
        assert isinstance(names, dict) and isinstance(names["groups"], list) and names["groups"]
        group = names["groups"][0]
        assert isinstance(group, dict)
        assert engine.call("identity_update", {"operation": "rename-person", "subject": group["id"], "name": "Authoritative Fixture"})
        names = engine.call("identity_query", "name", {"name": "Authoritative Fixture"})
        assert isinstance(names, dict) and isinstance(names["groups"], list) and len(names["groups"]) == 1
        with pytest.raises(ValueError, match="changed in another window"):
            engine.call("options_update", "new@example.test", "", revision)
        with WriterLease.acquire(archive, str(archive), "test peer", "test-peer", "test"):
            state = engine.call("options_status")
            assert isinstance(state, dict)
            with pytest.raises(ValueError, match="ArchiveBusyError"):
                engine.call("options_update", "new@example.test", "", state["revision"])
        assert engine.call("stop_import")
    assert input_path.read_bytes() == raw


def test_pipe_close_cancels_import_and_allows_recovery(tmp_path: Path) -> None:
    """Owner loss: stop between messages, release the writer, and resume preserved inputs."""
    source = tmp_path / "source"
    source.mkdir()
    raws = [f"From: alice@example.test\nTo: bob@example.test\nSubject: original {i}\nMessage-ID: <cancel-{i}@example.test>\nDate: Tue, 3 Jan 2023 12:00:00 +0000\n\n".encode() + b"Preserve this original line.\n" * 100 for i in range(300)]
    for index, raw in enumerate(raws):
        (source / f"message-{index}.eml").write_bytes(raw)
    archive = tmp_path / "interrupted.mailarchive"
    with peer(archive, tmp_path) as engine:
        engine.call("create")
        defaults = engine.call("import_defaults", str(source))
        assert isinstance(defaults, dict)
        engine.call("start_import", {"source": str(source), "include": "alice@example.test", "revision": defaults["revision"], "scan_policy": "not-scanned"})
        deadline = time.monotonic() + 15
        while True:
            history = engine.call("ingest_overview")
            assert isinstance(history, dict)
            status = history["status"]
            if isinstance(status, dict) and int(str(status["processed_messages"])) > 0:
                assert int(str(status["processed_messages"])) < len(raws)
                break
            assert time.monotonic() < deadline
            time.sleep(.01)
        # Context closes the real owner pipe while import is active; it must exit <=6s.
    with WriterLease.acquire(archive, str(archive), "post-exit verification", "verify", "test"):
        with sqlite3.connect(archive / "archive.sqlite3") as database:
            hashes = {row[0] for row in database.execute("SELECT sha256 FROM messages")}
            assert hashes <= {sha256(raw).hexdigest() for raw in raws}
            assert database.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    with peer(archive, tmp_path) as engine:
        assert engine.call("recover")
        assert engine.call("resume_processing", True, True)
        deadline = time.monotonic() + 60
        while True:
            state = engine.call("job_status")
            assert isinstance(state, dict)
            if not state["active"]:
                assert state["error"] is None, state
                break
            assert time.monotonic() < deadline
            time.sleep(.05)
    with sqlite3.connect(archive / "archive.sqlite3") as database:
        assert database.execute("SELECT count(*) FROM messages").fetchone() == (len(raws),)
    assert all((source / f"message-{index}.eml").read_bytes() == raw for index, raw in enumerate(raws))
