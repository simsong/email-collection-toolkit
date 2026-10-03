# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Exercise desktop Quit in a disposable child process.
# Real threads reproduce a callback that prevents Python finalization.
# Leased synthetic jobs either finish cooperatively or remain blocked.
# The blocked case leaves an append journal and an uncommitted transaction.
# Parent tests check exit timing and recover the disposable archive.
# An optional native mode exercises Cocoa with the shipped About page.

from __future__ import annotations

import hashlib
import os
import sqlite3
import sys
from pathlib import Path
from threading import Event, Thread
from time import sleep

import webview

from mailarchiver.__main__ import IngestRequest, run_ingest
from mailarchiver.application import ApplicationController, ApplicationPreferencesStore, IngestJob
from mailarchiver.gui_app import (
    GUI_DIRECTORY, GuiApi, PyWebViewApplication, SetupApi, configure_macos_application, install_macos_document_events,
)
from mailarchiver.layout import mbox_directory
from mailarchiver.loopback import LoopbackAssetServer
from mailarchiver.mbox import PendingPublication, journal_publication
from mailarchiver.owner_rules import OwnerRules
from mailarchiver.owned_command import run_owned_command
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
            catalog.execute("PRAGMA cache_size=1")
            catalog.execute("INSERT INTO email_addresses(address) VALUES ('uncommitted@example.test')")
            # Force dirty pages to disk so read-only GUI open must recover a hot journal.
            catalog.executemany("INSERT INTO email_addresses(address) VALUES (?)",
                                ((f"pending-{index}@example.test",) for index in range(500)))
            search = sqlite3.connect(archive / "search.sqlite3")
            search.execute("PRAGMA cache_size=1")
            search.executemany("INSERT INTO message_fts(sha256,content) VALUES (?,?)",
                               ((str(index), "uncommitted search text " * 100) for index in range(100)))
            processing = sqlite3.connect(archive / "processing.sqlite3")
            processing.execute("PRAGMA cache_size=1")
            processing.executemany("INSERT INTO processing_settings(name,value) VALUES (?,?)",
                                   ((f"uncommitted-{index}", "pending" * 1000) for index in range(100)))
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
    if mode == "replay":
        run_ingest(IngestRequest.model_validate_json((root / "request.json").read_text()), terminal=False)
        return
    controller = ApplicationController(ApplicationPreferencesStore(root / "preferences.json"))
    app = PyWebViewApplication(controller)
    # This live non-daemon waiter would hang sys.exit/Py_FinalizeEx indefinitely.
    Thread(target=Event().wait, name="stranded-ui-callback", daemon=False).start()
    if mode in {"blocked", "cooperative"}:
        for number in range(2):
            start_job(app, root, number, mode == "blocked")
    if mode in {"exports", "blocked-exports"}:
        api = GuiApi(None)
        app._apis["fixture"] = api
        (api.temporary_directory / "private.eml").write_bytes(b"synthetic private mail")
        (root / "export-path").write_text(str(api.temporary_directory))
        if mode == "blocked-exports":
            api._drag_lock.acquire()
    if mode == "writer":
        entered = Event()

        def save() -> None:
            with app.writer_activity():
                entered.set()
                sleep(0.3)
                (root / "saved").write_text("completed")

        Thread(target=save, daemon=True).start()
        assert entered.wait(5)
    if mode in {"lock", "controller-lock", "document-lock", "setup-lock"}:
        locked = Event()
        held_lock = app._lock if mode in {"lock", "setup-lock"} else controller._lock
        if mode == "document-lock":
            held_lock = controller.create_document(root / "locked-archive")._lock

        def hold_application_lock() -> None:
            with held_lock:
                locked.set()
                Event().wait()

        Thread(target=hold_application_lock, daemon=True).start()
        assert locked.wait(5)
        assert not app.prepare_quit()
        if mode not in {"lock", "setup-lock"}:
            assert app.has_active_ingest(), "unknown state must still require confirmation"
    if mode == "startup":
        document = controller.create_document(root / "startup-archive")
        assert document.path is not None
        session = controller.new_search_window(document)
        api = GuiApi(document.path, application=app, document=document, search_window=session)
        config = document.path / "config.yaml"
        os.mkfifo(config)
        started = Event()

        def start_import() -> None:
            assert not app.start_import(api, [], owner_rules=OwnerRules(include=["owner@example.test"]), scan_policy="not-scanned")
            (root / "startup-stopped").touch()

        Thread(target=start_import, daemon=True).start()
        descriptor = os.open(config, os.O_WRONLY)

        def finish_settings() -> None:
            assert started.wait(5)
            sleep(0.3)
            os.write(descriptor, b"{}\n")
            os.close(descriptor)

        Thread(target=finish_settings, daemon=True).start()
        started.set()
    if mode == "definitions":
        def update_definitions() -> None:
            with app.definitions_activity():
                run_owned_command([sys.executable, "-c",
                    "import os,sys; from pathlib import Path; from threading import Event; "
                    "Path(sys.argv[1]).write_text(str(os.getpid())); Event().wait()",
                    str(root / "updater-pid")], timeout=60)

        Thread(target=update_definitions, daemon=True).start()
        for _ in range(500):
            if (root / "updater-pid").exists():
                break
            sleep(0.01)
        assert (root / "updater-pid").exists()
    if mode == "scanner":
        from mailarchiver.scanner import ClamScanner  # pylint: disable=import-outside-toplevel
        scanner = ClamScanner(scan_temporary_directory=root)
        scanner.__enter__()
        assert scanner.process is not None
        (root / "scanner-pid").write_text(str(scanner.process.pid))
        fifo = root / "blocked-scan"
        os.mkfifo(fifo)
        done = Event()

        def scan() -> None:
            try:
                scanner.scan(fifo)
            finally:
                done.set()

        Thread(target=scan, daemon=True).start()
        assert not done.wait(0.3), "the native scan must still be blocked when its owner exits"

    def quit_app() -> None:
        print("ready", flush=True)
        if mode in {"setup", "setup-lock"}:
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
