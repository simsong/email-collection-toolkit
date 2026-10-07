# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Exercise the transitional archive engine through its real private pipe protocol.
# Synthetic inputs pass through production creation, import and identity services.
# The tests verify byte preservation, owner-policy conflicts and writer exclusion.
# Pipe closure must reap the helper without a Python GUI or visible windows.
# A narrow post-import filesystem fault tests ancillary failures after real ingest.
# No real mail, scanner installation or external accounts are involved.
from __future__ import annotations

from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import errno
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
from mailarchiver import rust_engine
from mailarchiver.__main__ import IngestRequest
from mailarchiver.archive_config import remember_import_directory
from mailarchiver.ingest_status import latest_ingest_status
from mailarchiver.rust_engine import Capabilities, Engine, ImportDefaults, Reply, Request
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


def test_completed_import_retains_success_when_directory_preference_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Import outcome: an ancillary configuration failure is a warning, not data loss."""
    source = tmp_path / "source"
    source.mkdir()
    raw = b"From: alice@example.test\nSubject: completed fixture\nDate: Tue, 3 Jan 2023 12:00:00 +0000\n\nOriginal bytes.\n"
    input_path = source / "fixture.eml"
    input_path.write_bytes(raw)
    archive = tmp_path / "completed.mailarchive"
    engine = Engine(archive)
    engine.call(Request(id=1, method="create"))

    def fail_preference(destination: Path, roots: list[Path]) -> Path | None:
        # Fault injection is confined to the post-import boundary: changing this
        # path earlier could fail canonical publication instead of the preference.
        # The real filesystem and preference writer produce the actual error.
        config = destination / "config.yaml"
        saved = config.read_bytes()
        config.unlink()
        config.mkdir()
        try:
            return remember_import_directory(destination, roots)
        finally:
            config.rmdir()
            config.write_bytes(saved)

    monkeypatch.setattr(rust_engine, "remember_import_directory", fail_preference)
    engine.start(IngestRequest(archive=archive, roots=[str(source)], owner_rules=OwnerRules(include=["alice@example.test"]),
                               scan_policy="not-scanned"))
    assert engine.finished.wait(60)
    assert engine.job.error is None
    assert engine.job.warning and engine.job.warning.startswith("Import completed, but the source directory could not be saved:")
    assert not engine.job.active and engine.job.generation == 1
    status = latest_ingest_status(archive)
    assert status is not None and status.state == "completed"
    with sqlite3.connect(archive / "archive.sqlite3") as database:
        assert database.execute("SELECT sha256,category FROM messages").fetchall() == [(sha256(raw).hexdigest(), "Sent")]
    assert input_path.read_bytes() == raw


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
        capabilities = Capabilities.model_validate(engine.call("capabilities"))
        assert capabilities.available and capabilities.write_available
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
    """Owner loss: bound shutdown of blocked processing, release the writer and recover."""
    source = tmp_path / "source"
    source.mkdir()
    raws = [f"From: alice@example.test\nTo: bob@example.test\nSubject: original {i}\nMessage-ID: <cancel-{i}@example.test>\nDate: Tue, 3 Jan 2023 12:00:00 +0000\n\n".encode() + b"Preserve this original line.\n" * 100 for i in range(3)]
    for index, raw in enumerate(raws):
        (source / f"message-{index}.eml").write_bytes(raw)
    archive = tmp_path / "interrupted.mailarchive"
    plugins = tmp_path / "plugins"
    processor = plugins / "processors" / "owner-boundary"
    processor.mkdir(parents=True)
    (processor / "plugin.toml").write_text('''api_version=2
plugin_type="processor"
kind="owner-boundary"
name="Owner pipe boundary fixture"
implementation_version="1"
entrypoint="plugin:Processor"
pipeline="ingest"
rank=1
timeout_seconds=120
subscribes=["application/x-mailarchiver-raw-message"]
''')
    (processor / "plugin.py").write_text('''# Coordinate owner loss at a real unfinished message in synthetic ingest.
# The first message publishes normally; the second waits at its processor.
# Marker files let the parent observe that exact boundary without timing import.
# This same plugin is retained in the production request and replayed by the helper.
# The parent releases it only after bounded owner-loss shutdown, before recovery.
from pathlib import Path
from time import sleep
from mailarchiver.processing.api import ProcessingResult

class Processor:
    def process(self, item):
        root = Path(__file__).parents[3]
        if item.message_id == (root / "blocked-sha256").read_text():
            (root / "entered").touch()
            while not (root / "release").exists():
                item.check_cancelled()
                sleep(0.01)
        return ProcessingResult()
''')
    (tmp_path / "blocked-sha256").write_text(sha256(raws[1]).hexdigest())
    owner = tmp_path / "owner-names.txt"
    owner.write_text("alice@example.test\n")

    def wait_for_boundary(process: subprocess.Popen[str]) -> None:
        deadline = time.monotonic() + 30
        while not (tmp_path / "entered").exists():
            assert process.poll() is None, "Importer exited before the message boundary"
            assert time.monotonic() < deadline, "Importer did not reach the message boundary"
            time.sleep(.01)

    # The real CLI persists a request with the processor, then crashes at the
    # second message. The ordinary helper resume path must load that request.
    with (tmp_path / "prepare.log").open("w", encoding="utf-8") as log:
        with subprocess.Popen([sys.executable, "-m", "mailarchiver", "--archive", str(archive), "ingest",
                               "--no-scan", "--workers", "1", "--defer-content", "--plugin-dir", str(plugins),
                               "--owner-names-file", str(owner), str(source)], stdout=log, stderr=log, text=True) as child:
            try:
                wait_for_boundary(child)
            finally:
                if child.poll() is None:
                    child.kill()
                child.wait(5)
    (tmp_path / "entered").unlink()
    with peer(archive, tmp_path) as engine:
        assert engine.call("recover")
        assert engine.call("resume_processing", True, False)
        wait_for_boundary(engine.process)
        state = engine.call("job_status")
        assert isinstance(state, dict) and state["active"] is True
        with sqlite3.connect(archive / "archive.sqlite3") as database:
            assert database.execute("SELECT sha256 FROM messages").fetchall() == [(sha256(raws[0]).hexdigest(),)]
        # Context closes the real owner pipe while import is active; it must exit <=6s.
    with WriterLease.acquire(archive, str(archive), "post-exit verification", "verify", "test"):
        with sqlite3.connect(archive / "archive.sqlite3") as database:
            hashes = {row[0] for row in database.execute("SELECT sha256 FROM messages")}
            assert hashes == {sha256(raws[0]).hexdigest()}
            assert database.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    (tmp_path / "release").touch()
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
        assert {row[0] for row in database.execute("SELECT sha256 FROM messages")} == {sha256(raw).hexdigest() for raw in raws}
    assert all((source / f"message-{index}.eml").read_bytes() == raw for index, raw in enumerate(raws))


@pytest.mark.skipif(os.name != "posix", reason="uses a real FIFO to hold startup before cancellation reset")
def test_owner_eof_during_import_startup_is_latched(tmp_path: Path) -> None:
    """Owner loss during startup must not be reset or publish a new message."""
    archive = tmp_path / "startup.mailarchive"
    source = tmp_path / "source"
    source.mkdir()
    raw = b"From: alice@example.test\nSubject: startup cancellation\n\nOriginal fixture bytes.\n"
    input_path = source / "fixture.eml"
    input_path.write_bytes(raw)
    with peer(archive, tmp_path) as engine:
        assert engine.call("create")
        revision = DocumentOptions(archive).state().revision
        names = archive / "owner-names.txt"
        os.mkfifo(names)
        request = Request(id=2, method="start_import", args=[{
            "source": str(source), "include": "alice@example.test", "revision": revision,
            "scan_policy": "not-scanned",
        }])
        assert engine.process.stdin is not None
        engine.process.stdin.write(request.model_dump_json() + "\n")
        engine.process.stdin.flush()
        # The genuine options reader blocks on this FIFO inside start(), before
        # resetting cancellation. Opening its writer proves it reached that point.
        deadline = time.monotonic() + 3
        while True:
            try:
                descriptor = os.open(names, os.O_WRONLY | os.O_NONBLOCK)
                break
            except OSError as error:
                assert error.errno == errno.ENXIO
                assert engine.process.poll() is None
                assert time.monotonic() < deadline, "Import startup did not reach its owner-rule read"
                time.sleep(.01)
        with os.fdopen(descriptor, "w", encoding="utf-8"):
            engine.process.stdin.close()
            deadline = time.monotonic() + 3
            while "Owner pipe closed: cancelling archive work" not in (tmp_path / "engine.log").read_text():
                assert engine.process.poll() is None
                assert time.monotonic() < deadline, "Owner EOF was not observed while startup was held"
                time.sleep(.01)
        engine.process.wait(timeout=6)
        assert engine.process.stdout is not None
        reply = Reply.model_validate_json(engine.process.stdout.readline())
        assert reply.id == 2 and reply.error == "ValueError: The owning window has closed."
        names.unlink()
    with WriterLease.acquire(archive, str(archive), "post-startup cancellation", "verify", "test"):
        with sqlite3.connect(archive / "archive.sqlite3") as database:
            assert database.execute("SELECT count(*) FROM messages").fetchone() == (0,)
            assert database.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    assert input_path.read_bytes() == raw
    assert not list((archive / "data/mbox").glob("*.mbox"))
