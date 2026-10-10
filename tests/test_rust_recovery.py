# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Exercise foreground opening through the real Rust worker and Python recovery peer.
# A crashed SQLite writer leaves genuine rollback journals in synthetic databases.
# Chromium renders the shipped opening page and sends its real Abort request.
# OS suspension holds the actual helper past the old timeout without fake replies.
# A channel-only pause after real creation exercises cancellation before acknowledgment.
# Successful repair must revalidate; abort and failures must never open a reader.
# Canonical MBOX bytes and committed database rows survive every recovery attempt.
from __future__ import annotations

from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
from queue import Queue, Empty
import shlex
import signal
import sqlite3
import subprocess
import sys
from threading import Thread
import time
from typing import Iterator, Literal

from playwright.sync_api import Page, expect
from pydantic import BaseModel, Field, JsonValue
import pytest

from mailarchiver.application import validate_archive
from mailarchiver.processing.store import connect as processing_connect
from mailarchiver.writer_lock import WriterLease
from test_mailsearch import make_archive

ROOT = Path(__file__).resolve().parents[1]
BINARY_ENV = "RUST_WEBVIEW_BINARY"
PYTHON_ENV = "ECT_RUST_ENGINE_PYTHON"


class OpeningRequest(BaseModel):
    id: int = 1
    method: str
    args: list[JsonValue] = Field(default_factory=list)


class OpeningReply(BaseModel):
    id: int
    result: JsonValue = None
    error: str | None = None


class RecentArchives(BaseModel):
    version: int = 1
    recent: list[Path]


def crash_writer(archive: Path) -> None:
    """Recovery requirement: spill genuine uncommitted catalog and FTS pages."""
    subprocess.run([sys.executable, "-c", """
import os, sqlite3, sys
from pathlib import Path
root=Path(sys.argv[1])
catalog=sqlite3.connect(root/'archive.sqlite3')
catalog.execute('PRAGMA cache_size=1')
catalog.executemany('INSERT INTO email_addresses(address) VALUES (?)',
                   ((f'pending-{i}@example.test',) for i in range(2000)))
search=sqlite3.connect(root/'search.sqlite3')
search.execute('PRAGMA cache_size=1')
search.executemany('INSERT INTO message_fts(sha256,content) VALUES (?,?)',
                  ((str(i),'uncommitted '*100) for i in range(2000)))
if (root/'processing.sqlite3').exists():
    processing=sqlite3.connect(root/'processing.sqlite3')
    processing.execute('PRAGMA cache_size=1')
    processing.executemany('INSERT INTO processing_settings(name,value) VALUES (?,?)',
                          ((f'pending-{i}', 'uncommitted '*100) for i in range(2000)))
os._exit(0)
""", str(archive)], check=True, timeout=20)
    for name in ["archive.sqlite3", "search.sqlite3", "processing.sqlite3"]:
        if not (archive / name).exists():
            continue
        assert (archive / f"{name}-journal").stat().st_size > 512
        with pytest.raises(sqlite3.OperationalError) as raised:
            with sqlite3.connect(f"{(archive / name).as_uri()}?mode=ro", uri=True) as database:
                database.execute("SELECT count(*) FROM sqlite_schema").fetchone()
        assert raised.value.sqlite_errorcode == sqlite3.SQLITE_READONLY_ROLLBACK


@contextmanager
def opener(archive: Path, tmp_path: Path, held: bool = False,
           recent: list[Path] | None = None, create: bool = False,
           before_create_reply: bool = False) -> Iterator[tuple[subprocess.Popen[str], Queue[OpeningReply]]]:
    binary = os.environ.get(BINARY_ENV)
    if not binary:
        pytest.skip("run make test-rust-recovery")
    environment = os.environ.copy()
    environment[PYTHON_ENV] = sys.executable
    arguments = [binary, "--creation-rpc" if create else "--opening-rpc"]
    if recent is None:
        arguments.append(str(archive))
    else:
        home = tmp_path / "home"
        environment["HOME"] = environment["LOCALAPPDATA"] = environment["XDG_CONFIG_HOME"] = str(home)
        settings = home / ("Library/Application Support" if sys.platform == "darwin" else ".") / "Email Collection Toolkit"
        settings.mkdir(parents=True, exist_ok=True)
        (settings / "recent-archives.json").write_text(RecentArchives(recent=recent).model_dump_json(), encoding="utf-8")
    if held:
        launcher = tmp_path / "held-python"
        launcher.write_text("#!/bin/sh\n"
            "# Run the real project interpreter after a controlled scheduling pause.\n"
            "# The parent observes this process stopped before resuming it.\n"
            "# No protocol data or recovery results are replaced.\n"
            "# SQLite journals remain genuine and are recovered by production code.\n"
            "# This private fixture tests long waits and explicit owner abort.\n"
            f"kill -STOP $$\nexec {shlex.quote(sys.executable)} \"$@\"\n", encoding="utf-8")
        launcher.chmod(0o700)
        environment[PYTHON_ENV] = str(launcher)
    if before_create_reply:
        gate = tmp_path / "creation-channel.py"
        gate.write_text('''# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Run the production private service and forward its actual typed replies.
# Pause only its stdout channel after successful real archive initialization.
# The parent observes the completed staging archive before requesting Abort.
# SIGSTOP prevents acknowledgment; the Rust supervisor must reap this peer.
# No creation, validation, reply payload or owner-pipe behavior is mocked.
# The fixture leaves destination cleanup entirely to production code.
import os
from pathlib import Path
import signal
import sys
from mailarchiver import rust_engine

marker = Path(sys.argv[1])
sys.argv = ["rust-engine", sys.argv[-1]]
channel = sys.stdout

class Output:
    def write(self, payload: str) -> int:
        reply = rust_engine.Reply.model_validate_json(payload)
        if reply.id == 2 and reply.error is None and reply.result is True:
            marker.write_text(sys.argv[1], encoding="utf-8")
            os.kill(os.getpid(), signal.SIGSTOP)
        return channel.write(payload)

    def flush(self) -> None:
        channel.flush()

sys.stdout = Output()
rust_engine.main()
''', encoding="utf-8")
        launcher = tmp_path / "held-create-python"
        launcher.write_text("#!/bin/sh\n"
            "# Launch the actual private service with a channel-only scheduling gate.\n"
            "# Preserve the production target argument and interpreter environment.\n"
            "# The gate forwards typed replies without replacing archive operations.\n"
            "# Its OS pause occurs after real creation but before acknowledgment.\n"
            "# The parent tests bounded Abort and owned-staging cleanup.\n"
            f"exec {shlex.quote(sys.executable)} {shlex.quote(str(gate))} "
            f"{shlex.quote(str(tmp_path / 'creation-ready'))} \"$@\"\n", encoding="utf-8")
        launcher.chmod(0o700)
        environment[PYTHON_ENV] = str(launcher)
    replies: Queue[OpeningReply] = Queue()
    with (tmp_path / "opening.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(arguments, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=log, text=True, env=environment)

        def read() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                replies.put(OpeningReply.model_validate_json(line))

        Thread(target=read, daemon=True).start()
        try:
            yield process, replies
        finally:
            if process.poll() is None:
                assert process.stdin is not None
                process.stdin.close()
                process.wait(timeout=7)
            if process.stdout:
                process.stdout.close()


def send(process: subprocess.Popen[str], method: str) -> None:
    assert process.stdin is not None
    process.stdin.write(OpeningRequest(method=method).model_dump_json() + "\n")
    process.stdin.flush()


def held_child(process: subprocess.Popen[str]) -> int:
    """Find only the helper owned by this test's Rust process, never another task."""
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        rows = subprocess.check_output(["ps", "-axo", "pid=,ppid=,stat="], text=True)
        for row in rows.splitlines():
            pid, parent, state = row.split()
            if int(parent) == process.pid and "T" in state:
                return int(pid)
        time.sleep(.01)
    raise AssertionError("Recovery helper never reached the controlled OS stop")


def assert_recovered(archive: Path, mboxes: list[tuple[Path, bytes]]) -> None:
    validate_archive(archive)
    with sqlite3.connect(archive / "archive.sqlite3") as database:
        assert database.execute("SELECT count(*) FROM email_addresses WHERE address LIKE 'pending-%'").fetchone() == (0,)
        assert database.execute("SELECT count(*) FROM messages").fetchone() == (1,)
    with sqlite3.connect(archive / "search.sqlite3") as database:
        assert database.execute("SELECT count(*) FROM message_fts").fetchone() == (1,)
    if (archive / "processing.sqlite3").exists():
        with sqlite3.connect(archive / "processing.sqlite3") as database:
            assert database.execute("SELECT count(*) FROM processing_settings WHERE name LIKE 'pending-%'").fetchone() == (0,)
    assert all(path.read_bytes() == content for path, content in mboxes)


def test_only_hot_journals_trigger_recovery_and_writer_failure_keeps_archive_closed(tmp_path: Path) -> None:
    """Opening requirement: distinguish invalid archives, busy writers and recovery."""
    archive, _ = make_archive(tmp_path)
    with WriterLease.acquire(archive, str(archive), "create synthetic processing schema", "processing-fixture", "test"):
        processing_connect(archive, create=True, production=True).close()
    mbox = next(archive.rglob("*.mbox"))
    mboxes = [(mbox, mbox.read_bytes())]
    with opener(archive, tmp_path) as (process, replies):
        send(process, "opening_start")
        assert replies.get(timeout=10).result is True
    crash_writer(archive)
    with WriterLease.acquire(archive, str(archive), "test active writer", "recovery-conflict", "test"):
        with opener(archive, tmp_path) as (process, replies):
            send(process, "opening_start")
            assert replies.get(timeout=10).result == "recovering"
            failed = replies.get(timeout=10)
            assert failed.result is None and failed.error and "busy with another writer" in failed.error
    assert (archive / "archive.sqlite3-journal").exists()
    with opener(archive, tmp_path) as (process, replies):
        send(process, "opening_start")
        assert replies.get(timeout=10).result == "recovering"
        assert replies.get(timeout=15).result is True
    assert_recovered(archive, mboxes)
    (archive / "archive.sqlite3").write_bytes(b"invalid database")
    with opener(archive, tmp_path, held=True) as (process, replies):
        send(process, "opening_start")
        failed = replies.get(timeout=10)
        assert failed.id == 1 and failed.error and failed.result is None
        assert replies.empty()


def test_startup_recovers_newest_recoverable_recent_archive(tmp_path: Path) -> None:
    """Recent-opening requirement: hot journals retain precedence over older readers."""
    newest = tmp_path / "newest"
    older = tmp_path / "older"
    newest.mkdir()
    older.mkdir()
    archive, _ = make_archive(newest)
    old_archive, _ = make_archive(older)
    with WriterLease.acquire(archive, str(archive), "create synthetic processing schema", "startup-fixture", "test"):
        processing_connect(archive, create=True, production=True).close()
    mbox = next(archive.rglob("*.mbox"))
    mboxes = [(mbox, mbox.read_bytes())]
    old_before = [(path, path.read_bytes()) for path in old_archive.rglob("*") if path.is_file()]
    corrupt = tmp_path / "corrupt"
    corrupt.mkdir()
    (corrupt / "archive.sqlite3").write_bytes(b"invalid database")
    missing = tmp_path / "missing"
    crash_writer(archive)
    recent = [missing, corrupt, archive, old_archive]
    with WriterLease.acquire(archive, str(archive), "test active writer", "startup-conflict", "test"):
        with opener(archive, tmp_path, recent=recent) as (process, replies):
            send(process, "opening_start")
            assert replies.get(timeout=10).result == "recovering"
            failed = replies.get(timeout=10)
            assert failed.result is None and failed.error and "busy with another writer" in failed.error
    assert (archive / "archive.sqlite3-journal").exists()
    with opener(archive, tmp_path, recent=recent) as (process, replies):
        send(process, "opening_start")
        assert replies.get(timeout=10).result == "recovering"
        assert replies.get(timeout=15).result is True
    assert_recovered(archive, mboxes)
    assert all(path.read_bytes() == content for path, content in old_before)
    assert not missing.exists()
    assert list(corrupt.iterdir()) == [corrupt / "archive.sqlite3"]
    assert (corrupt / "archive.sqlite3").read_bytes() == b"invalid database"


@pytest.mark.skipif(os.name != "posix", reason="real SIGSTOP/SIGCONT helper scheduling")
@pytest.mark.parametrize("outcome", ["complete", "abort"])
def test_foreground_recovery_timer_long_wait_and_abort(page: Page, tmp_path: Path, outcome: Literal["complete", "abort"]) -> None:
    """Foreground requirement: real recovery outlasts 30s; Abort prevents opening."""
    if sys.platform == "win32":
        pytest.skip("POSIX process signals required")
    archive, _ = make_archive(tmp_path)
    mbox = next(archive.rglob("*.mbox"))
    mboxes = [(mbox, mbox.read_bytes())]
    crash_writer(archive)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler,
                                directory=str(ROOT / "rust/mailsearch-gui")))
    Thread(target=server.serve_forever, daemon=True).start()
    child: int | None = None
    try:
        with opener(archive, tmp_path, held=True) as (process, replies):
            page.expose_function("opening_send", lambda value: send(process, OpeningRequest.model_validate_json(value).method))

            def receive() -> list[str]:
                values = []
                while True:
                    try:
                        values.append(replies.get_nowait().model_dump_json())
                    except Empty:
                        return values

            page.expose_function("opening_receive", receive)
            page.add_init_script("""
window.ipc={postMessage:value=>window.opening_send(value)};
setInterval(async()=>{
  for(const value of await window.opening_receive()) {
    const reply=JSON.parse(value);
    if(reply.id===0) window.__rustOpening?.(); else window.__rustReply?.(reply);
  }
},25);
""")
            page.goto(f"http://127.0.0.1:{server.server_port}/opening.html")
            expect(page.get_by_role("heading")).to_have_text("Recovery in progress…")
            child = held_child(process)
            expect(page.locator("#elapsed")).to_have_text("Elapsed: 00:01", timeout=3000)
            if outcome == "complete":
                expect(page.locator("#elapsed")).to_have_text("Elapsed: 00:31", timeout=33000)
                assert process.poll() is None
                artifacts = ROOT / ".tmp/rust-gui-native"
                artifacts.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(artifacts / "foreground-recovery.png"))
                os.kill(child, signal.SIGCONT)
                child = None
                expect(page).to_have_url(f"http://127.0.0.1:{server.server_port}/index.html", timeout=15000)
                assert_recovered(archive, mboxes)
            else:
                started = time.monotonic()
                page.get_by_role("button", name="Abort", exact=True).click()
                expect(page.get_by_role("heading")).to_have_text("Recovery aborted", timeout=7000)
                assert time.monotonic() - started < 7
                process.wait(timeout=1)
                child = None  # The supervisor killed and reaped its stopped peer.
                assert page.url.endswith("/opening.html")
                expect(page.get_by_role("button", name="Close", exact=True)).to_be_visible()
                assert (archive / "archive.sqlite3-journal").exists()
                assert all(path.read_bytes() == content for path, content in mboxes)
                with opener(archive, tmp_path) as (retry, responses):
                    send(retry, "opening_start")
                    assert responses.get(timeout=10).result == "recovering"
                    assert responses.get(timeout=15).result is True
                assert_recovered(archive, mboxes)
    finally:
        if child is not None:
            try:
                os.kill(child, signal.SIGCONT)
            except ProcessLookupError:
                pass
        server.shutdown()
        server.server_close()


@pytest.mark.skipif(os.name != "posix", reason="real SIGSTOP helper scheduling")
def test_startup_creation_is_supervised_and_abort_keeps_destination_empty(tmp_path: Path) -> None:
    """Startup requirement: the actual initialization peer is interruptible off the UI thread."""
    archive = tmp_path / "new.mailarchive"
    archive.mkdir()
    with opener(archive, tmp_path, held=True, create=True) as (process, replies):
        send(process, "opening_start")
        child = held_child(process)
        start = time.monotonic()
        send(process, "opening_abort")
        failed = replies.get(timeout=7)
        assert failed.error and "aborted" in failed.error and failed.result is None
        assert time.monotonic() - start < 7
        process.wait(timeout=2)
        with pytest.raises(ProcessLookupError):
            os.kill(child, 0)
    assert not list(archive.iterdir())
    with opener(archive, tmp_path, create=True) as (process, replies):
        send(process, "opening_start")
        assert replies.get(timeout=15).result is True
    validate_archive(archive)
    with sqlite3.connect(archive / "archive.sqlite3") as database:
        assert database.execute("SELECT count(*) FROM messages").fetchone() == (0,)


@pytest.mark.skipif(os.name != "posix", reason="real SIGSTOP creation acknowledgment scheduling")
@pytest.mark.parametrize("existing", [False, True])
def test_abort_after_real_creation_before_acknowledgment_discards_only_staging(tmp_path: Path, existing: bool) -> None:
    """New cancellation: completed helper work cannot publish after pre-commit Abort."""
    archive = tmp_path / "retry.mailarchive"
    if existing:
        archive.mkdir()
    other = tmp_path / "Existing.mailarchive"
    other.mkdir()
    sentinel = other / "original.eml"
    sentinel.write_bytes(b"Original unrelated archive bytes\n")
    with opener(archive, tmp_path, create=True, before_create_reply=True) as (process, replies):
        send(process, "opening_start")
        child = held_child(process)
        prepared = Path((tmp_path / "creation-ready").read_text(encoding="utf-8"))
        validate_archive(prepared)  # Actual complete catalog/index, no synthetic reply.
        assert prepared != archive and prepared.parent.name.startswith(".ect-create-")
        assert not archive.exists() or not list(archive.iterdir())
        send(process, "opening_abort")
        failed = replies.get(timeout=7)
        assert failed.error and "aborted" in failed.error and failed.result is None
        process.wait(timeout=2)
        with pytest.raises(ProcessLookupError):
            os.kill(child, 0)
    assert not archive.exists() or not list(archive.iterdir())
    assert not list(tmp_path.glob(".ect-create-*"))
    assert sentinel.read_bytes() == b"Original unrelated archive bytes\n"
    with opener(archive, tmp_path, create=True) as (process, replies):
        send(process, "opening_start")
        assert replies.get(timeout=15).result is True
    validate_archive(archive)


@pytest.mark.skipif(os.name != "posix", reason="real SIGSTOP creation acknowledgment scheduling")
def test_creation_commit_preserves_concurrent_destination_contents(tmp_path: Path) -> None:
    """Publication safety: another writer's destination files must never be replaced."""
    if sys.platform == "win32":
        pytest.skip("POSIX process signals required")
    archive = tmp_path / "concurrent.mailarchive"
    archive.mkdir()
    with opener(archive, tmp_path, create=True, before_create_reply=True) as (process, replies):
        send(process, "opening_start")
        child = held_child(process)
        prepared = Path((tmp_path / "creation-ready").read_text(encoding="utf-8"))
        validate_archive(prepared)
        original = archive / "original.eml"
        original.write_bytes(b"Concurrent user-owned bytes\n")
        os.kill(child, signal.SIGCONT)
        failed = replies.get(timeout=15)
        assert failed.error and "Publish newly created archive" in failed.error and failed.result is None
    assert original.read_bytes() == b"Concurrent user-owned bytes\n"
    assert list(archive.iterdir()) == [original]
    assert not list(tmp_path.glob(".ect-create-*"))
