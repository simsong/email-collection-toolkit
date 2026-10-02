# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Exercise desktop Quit in a disposable child process.
# Real threads reproduce a callback that prevents Python finalization.
# Leased synthetic jobs either finish cooperatively or remain blocked.
# The blocked case leaves an append journal and an uncommitted transaction.
# Parent tests check exit timing and recover the disposable archive.
# An optional native mode exercises Cocoa with the shipped About page.

from __future__ import annotations

import hashlib
import sqlite3
import sys
from pathlib import Path
from threading import Event, Thread
from time import sleep

import webview

from mailarchiver.__main__ import IngestRequest, run_ingest
from mailarchiver.application import ApplicationController, ApplicationPreferencesStore, IngestJob
from mailarchiver.gui_app import (
    GUI_DIRECTORY, PyWebViewApplication, SetupApi, configure_macos_application, install_macos_document_events,
)
from mailarchiver.layout import mbox_directory
from mailarchiver.loopback import LoopbackAssetServer
from mailarchiver.mbox import PendingPublication, journal_publication
from mailarchiver.owner_rules import OwnerRules
from mailarchiver.writer_lock import WriterLease


def start_job(app: PyWebViewApplication, root: Path, number: int, blocked: bool) -> None:
    """Retain real writer ownership through a cooperative or interrupted shutdown."""
    document = app.controller.create_document(root / f"archive-{number}")
    assert document.path is not None
    archive = document.path
    run_ingest(IngestRequest(archive=archive, scan_policy="not-scanned", continue_content=False,
        owner_rules=OwnerRules(include=["owner@example.test"])), terminal=False)
    window = app.controller.new_search_window(document)
    job = IngestJob(operation_id=str(number), owner_window_id=window.window_id)
    lease = WriterLease.acquire(archive, document.descriptor.identity, "quit fixture", str(number), "test")
    app.controller.begin_ingest(document.descriptor.document_id, job, lease)
    entered = Event()

    def work() -> None:
        if blocked:
            catalog = sqlite3.connect(archive / "archive.sqlite3")
            catalog.execute("INSERT INTO email_addresses(address) VALUES ('uncommitted@example.test')")
            raw = b"Message-ID: <incomplete@example.test>\n\nunfinished message\n"
            destination = mbox_directory(archive) / "2024-Archive1.mbox"
            journal_publication(archive, PendingPublication(filename=destination.name, prior_size=0,
                file_existed=False, message_id="incomplete@example.test", sha256=hashlib.sha256(raw).hexdigest()))
            destination.write_bytes(b"From sender@example.test Mon Jan  1 00:00:00 2024\n" + raw[:20])
        entered.set()
        assert job.stop.wait(20)
        (root / f"stopped-{number}").touch()
        if blocked:
            Event().wait()
        else:
            sleep(0.1)
            app.controller.finish_ingest(document.descriptor.document_id, job.operation_id, published=False)
            job.finished.set()

    worker = Thread(target=work, daemon=False)
    app._import_threads.append(worker)
    worker.start()
    assert entered.wait(10)


def main() -> None:
    mode, directory = sys.argv[1:]
    root = Path(directory)
    controller = ApplicationController(ApplicationPreferencesStore(root / "preferences.json"))
    app = PyWebViewApplication(controller)
    # This live non-daemon waiter would hang sys.exit/Py_FinalizeEx indefinitely.
    Thread(target=Event().wait, name="stranded-ui-callback", daemon=False).start()
    if mode in {"blocked", "cooperative"}:
        for number in range(2):
            start_job(app, root, number, mode == "blocked")

    def quit_app() -> None:
        print("ready", flush=True)
        if mode == "setup":
            assert SetupApi(app).cancel()
        else:
            app.request_quit(confirm_ingest=False)

    if mode == "native":
        configure_macos_application()
        app.asset_server = LoopbackAssetServer(GUI_DIRECTORY)
        install_macos_document_events(app)
        app.create_about_window(hidden=True)
        window = webview.windows[-1]

        def native_quit() -> None:
            assert window.events.loaded.wait(15)
            # Exercise real periodic status traffic before requesting Quit.
            sleep(1.2)
            quit_app()

        webview.start(native_quit, http_server=False, private_mode=True)
    else:
        quit_app()


if __name__ == "__main__":
    main()
