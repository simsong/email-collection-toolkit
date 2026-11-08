# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Verify IMAP pause and resume through the shipped Ingests browser interface.
# A real TLS proxy stalls or disconnects traffic to a disposable pymap server.
# Synthetic mail traverses production acquisition, checkpoints and search indexing.
# Restart the application before clicking Resume to exercise durable recovery.
# Confirm source bytes and flags remain intact and saved bodies are not refetched.
# These checks establish local behavior, not live Gmail or native-window acceptance.
"""Requirements: expected IMAP interruptions pause with a working Resume action."""
from pathlib import Path
import hashlib
import sqlite3

import pytest
from playwright.sync_api import Page, expect

from e2e_tests.browser_notifications import notify_status
from mailarchiver.__main__ import IngestRequest
from mailarchiver.gui_app import GUI_DIRECTORY, IngestWindowApi
from mailarchiver.gui_processing import unfinished_work
from mailarchiver.imap_source import imap_locator
from mailarchiver.imap_cli import parser, synchronize
from mailarchiver.imap_state import totals
from mailarchiver.ingest_status import read_ingest_history
from mailarchiver.standalone_verify import verify_archive
from mailarchiver.writer_lock import WriterLease
from scripts.website_screenshots import bridge
from tests.imap_fixture import DisposableImap, ImapFault, InterruptibleImap
from tests.test_gui_processing import open_headless, wait_for_import
from tests.test_imap import assert_imap_searchable
from tests.test_imap import imap_archive as imap_archive, imap_inbox as imap_inbox, imap_messages as imap_messages
from tests.test_imap import imap_server as imap_server, imap_server_identity as imap_server_identity


@pytest.mark.parametrize("imap_server", ["interruptible"], indirect=True)
@pytest.mark.parametrize("fault,cli,workers", [
    (ImapFault(command=b"BODY.PEEK[]", mode="timeout", skip=1), False, 1),
    (ImapFault(command=b"BODY.PEEK[]", mode="partial", skip=1), False, 1),
    (ImapFault(command=b"BODY.PEEK[]", mode="disconnect", skip=1), False, 1),
    (ImapFault(command=b" LIST ", mode="timeout"), False, 1),
    (ImapFault(command=b" AUTHENTICATE PLAIN", mode="disconnect"), False, 1),
    (ImapFault(command=b" LIST ", mode="timeout"), True, 1),
    (ImapFault(command=b" AUTHENTICATE PLAIN", mode="disconnect"), True, 1),
    (ImapFault(command=b"BODY.PEEK[]", mode="timeout", skip=1), False, 3),
    (ImapFault(command=b"BODY.PEEK[]", mode="disconnect", skip=1), False, 3),
], ids=["body-timeout", "partial-body", "body-disconnect", "discovery-timeout", "login-disconnect",
        "cli-discovery", "cli-login", "parallel-timeout", "parallel-disconnect"])
def test_network_pause_and_resume_through_real_ingest_window(imap_archive: Path,
        imap_inbox: DisposableImap, imap_messages: tuple[bytes, ...], tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch, page: Page, fault: ImapFault, cli: bool, workers: int) -> None:
    """IMAP interruption must pause, retain committed bytes, and reconnect through the Resume button."""
    server = imap_inbox
    assert isinstance(server, InterruptibleImap) and server.account is not None
    with server.connect() as client:
        client.select("INBOX", readonly=True)
        before = client.uid("FETCH", "1:*", "(UID FLAGS RFC822.SIZE BODY.PEEK[])")[1]
    # Shorten only the configured socket deadline; all TLS, IMAP, and ingest code is real.
    monkeypatch.setattr("mailarchiver.imap_protocol.IMAP_TIMEOUT", 1)
    app, api = open_headless(imap_archive, tmp_path / "preferences.json", workers_override=workers)
    assert api.document is not None
    controls = IngestWindowApi(imap_archive, application=app, document=api.document)
    request = IngestRequest(archive=imap_archive, roots=[imap_locator(imap_archive, server.account, workers)],
                            workers=workers, scan_policy="not-scanned", index_attachments=True)
    try:
        server.fault = fault.model_copy()
        if cli:
            args = parser().parse_args(["--archive", str(imap_archive), "--no-scan", "--workers", str(workers)])
            assert synchronize(args) == 75
        else:
            assert app.start_import(api, [], processing_request=request)
            wait_for_import(api)
        status = read_ingest_history(imap_archive).statuses[0]
        assert status.state == "paused" and status.failure_detail is None
        assert "IMAP connection" in status.phase and "Resume" in status.phase
        assert controls.history()["can_resume"] and not controls.history()["active"]
        assert not any(notice.severity == "error" for notice in app.notices()), app.notices()
        with sqlite3.connect(imap_archive / "archive.sqlite3") as database:
            assert database.execute("SELECT result FROM ingest_runs ORDER BY run_pk DESC LIMIT 1").fetchone() == ("paused",)
            saved = database.execute("SELECT count(*) FROM imap_seen").fetchone()[0]
            if workers == 1:
                assert saved == (1 if fault.command == b"BODY.PEEK[]" else 0)
            else:
                assert 0 <= saved < 3
            assert database.execute("SELECT count(*) FROM messages").fetchone() == (saved,)
        with WriterLease.acquire(imap_archive, api.document.descriptor.identity, "pause fixture", "paused", "test"):
            pass
        app.shutdown()
        api.close()
        # Persist the pause and accepted request across a complete application restart.
        app, api = open_headless(imap_archive, tmp_path / "preferences.json", workers_override=workers)
        controls = IngestWindowApi(imap_archive, application=app, document=api.document)
        bridge(page, controls, ("history", "antivirus", "can_import_directory", "suspend_import", "resume_import"))
        page.goto((GUI_DIRECTORY / "ingests.html").as_uri())
        expect(page.locator(".state-badge")).to_have_text("Timeout" if fault.mode in {"timeout", "partial"} else "paused")
        expect(page.locator(".history-row.selected .state-dot")).to_have_text("−")
        assert page.locator(".history-row.selected .state-dot").evaluate("el => getComputedStyle(el).backgroundColor") == "rgb(244, 197, 66)"
        expect(page.get_by_role("heading", name="Failure", exact=True)).to_have_count(0)
        expect(page.locator("#import-control-status")).to_contain_text("Download paused")
        resume = page.get_by_role("button", name="Resume", exact=True)
        expect(resume).to_be_enabled()
        page.screenshot(path=str(tmp_path / "imap-paused.png"))
        server.fault = None
        server.commands.clear()
        resume.click()
        wait_for_import(api)
        notify_status(page)
        expect(page.locator(".state-badge")).to_have_text("completed", timeout=30000)
        assert totals(imap_archive).archived == 3 and totals(imap_archive).duplicates == 0
        assert not unfinished_work(imap_archive).incomplete
        assert_imap_searchable(imap_archive)
        assert not verify_archive(imap_archive)
        # Previously committed bodies are not downloaded again, even after restarting.
        assert sum(b"BODY.PEEK[]" in line for line in server.commands) == 3 - saved
        with sqlite3.connect(imap_archive / "archive.sqlite3") as database:
            assert {row[0] for row in database.execute("SELECT sha256 FROM messages")} == {
                hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest() for raw in imap_messages}
            assert database.execute("SELECT count(*) FROM imap_seen").fetchone() == (3,)
        with server.connect() as client:
            client.select("INBOX", readonly=True)
            assert client.uid("FETCH", "1:*", "(UID FLAGS RFC822.SIZE BODY.PEEK[])")[1] == before
    finally:
        server.fault = None
        app.shutdown()
        api.close()
