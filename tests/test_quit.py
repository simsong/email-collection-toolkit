# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Verify the bounded desktop shutdown contract from requirements.md.
# Child processes execute the real Quit handler and os._exit fallback.
# Readiness markers isolate quit latency from Python and Cocoa startup.
# Synthetic workers cover immediate, cooperative and timed-out termination.
# Recovery checks inspect SQLite rollback and journaled partial MBOX bytes.
# Native coverage is opt-in and uses only a synthetic About window.

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from time import monotonic, sleep

import pytest

from mailarchiver.__main__ import IngestRequest, run_ingest
from mailarchiver.application import ApplicationController, ApplicationPreferencesStore, InvalidArchiveError, validate_archive
from mailarchiver.gui_app import DocumentOptionsApi, GuiApi, PyWebViewApplication, QUIT_TIMEOUT_SECONDS, cleanup_stale_exports
from mailarchiver.layout import mbox_directory
from mailarchiver.owner_rules import OwnerRules
from mailarchiver.standalone_verify import verify_archive
from mailarchiver.writer_lock import WriterLease


def run_quit_probe(root: Path, mode: str) -> float:
    """Bound the child externally too, so a regressed quit cannot hang pytest."""
    with (root / "stdout").open("w+") as output, (root / "stderr").open("w+") as errors:
        child = subprocess.Popen([sys.executable, "-m", "tests.quit_probe", mode, str(root)], stdout=output, stderr=errors)
        try:
            deadline = monotonic() + 30
            # A file marker avoids an unbounded blocking pipe read on startup failure.
            while monotonic() < deadline:
                output.seek(0)
                if "ready\n" in output.read():
                    break
                if child.poll() is not None:
                    errors.seek(0)
                    pytest.fail(f"Quit probe failed before readiness: {errors.read()}")
                sleep(0.01)
            else:
                pytest.fail("Quit probe did not reach readiness")
            started = monotonic()
            child.wait(timeout=QUIT_TIMEOUT_SECONDS + 4)
            elapsed = monotonic() - started
            errors.seek(0)
            assert child.returncode == 0, errors.read()
            return elapsed
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)


@pytest.mark.parametrize("mode", ["idle", "setup", "cooperative", "blocked", "writer", "exports", "blocked-exports"])
def test_quit_exits_despite_stranded_callbacks(tmp_path: Path, mode: str) -> None:
    """Issue #150: inactive work exits now; two blocked jobs share one five-second budget."""
    elapsed = run_quit_probe(tmp_path, mode)
    if mode in {"blocked", "blocked-exports"}:
        assert QUIT_TIMEOUT_SECONDS - 0.5 <= elapsed < QUIT_TIMEOUT_SECONDS + 2
    else:
        assert elapsed < 2
    if mode == "writer":
        assert (tmp_path / "saved").read_text() == "completed"
    if mode in {"exports", "blocked-exports"}:
        exported = Path((tmp_path / "export-path").read_text())
        if mode == "blocked-exports":
            assert exported.exists()
            live = GuiApi(None)
            try:
                cleanup_stale_exports()
                assert live.temporary_directory.exists()
            finally:
                live.close()
        assert not exported.exists()
    if mode not in {"cooperative", "blocked"}:
        return
    for number in range(2):
        assert (tmp_path / f"stopped-{number}").exists()
        archive = tmp_path / f"archive-{number}"
        controller = ApplicationController(ApplicationPreferencesStore(tmp_path / "reopen-preferences.json"))
        if mode == "blocked":
            with pytest.raises(InvalidArchiveError, match="readonly"):
                validate_archive(archive)
            with WriterLease.acquire(archive, str(archive), "busy recovery", "probe", "test"):
                with pytest.raises(InvalidArchiveError, match="busy"):
                    controller.open_document(archive)
            journal = archive / "archive.sqlite3-journal"
            outside = tmp_path / f"journal-{number}"
            journal.rename(outside)
            original = outside.read_bytes()
            for alias in ("symlink", "hardlink"):
                if alias == "symlink":
                    journal.symlink_to(outside)
                else:
                    os.link(outside, journal)
                with pytest.raises(InvalidArchiveError, match="unsafe SQLite recovery path"):
                    controller.open_document(archive)
                assert outside.read_bytes() == original
                journal.unlink()
            outside.rename(journal)
        assert controller.open_document(archive).path == archive
        with WriterLease.acquire(archive, str(archive), "recovery check", "probe", "test"):
            with sqlite3.connect(archive / "archive.sqlite3") as catalog:
                assert catalog.execute("PRAGMA integrity_check").fetchone() == ("ok",)
                assert catalog.execute("SELECT COUNT(*) FROM email_addresses WHERE address='uncommitted@example.test'").fetchone() == (0,)
            with sqlite3.connect(archive / "search.sqlite3") as search:
                assert search.execute("PRAGMA integrity_check").fetchone() == ("ok",)
                assert search.execute("SELECT COUNT(*) FROM message_fts").fetchone() == (0,)
        if mode == "blocked":
            assert (archive / ".mailarchiver-pending.json").exists()
            run_ingest(IngestRequest(archive=archive, scan_policy="not-scanned", continue_content=False,
                owner_rules=OwnerRules(include=["owner@example.test"])), terminal=False)
            assert not (archive / ".mailarchiver-pending.json").exists()
            assert not list(mbox_directory(archive).glob("*.mbox"))
        assert not verify_archive(archive)


def test_quit_reservation_rejects_new_document_options_write(tmp_path: Path) -> None:
    """Owner settings cannot begin saving after the desktop has reserved Quit."""
    controller = ApplicationController(ApplicationPreferencesStore(tmp_path / "preferences.json"))
    document = controller.create_document(tmp_path / "archive")
    app = PyWebViewApplication(controller, exit_process=lambda _status: None)
    options = DocumentOptionsApi(document, app)
    revision = str(options.status()["revision"])
    app.request_quit(confirm_ingest=False)
    with pytest.raises(ValueError, match="quitting"):
        options.update("owner@example.test", "", revision)


@pytest.mark.skipif(sys.platform != "darwin" or os.environ.get("MAILARCHIVER_NATIVE_GUI_E2E") != "1",
                    reason="requires the opt-in macOS GUI session")
def test_native_quit_during_status_polling(tmp_path: Path) -> None:
    """Issue #150: the real Cocoa event loop and shipped bridge cannot block idle Quit."""
    assert run_quit_probe(tmp_path, "native") < 2
