# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Verify that desktop writes participate in the bounded Quit contract.
# Real disposable archives and exports exercise their normal disk operations.
# Narrow scheduling gates pause between databases or before atomic replacement.
# Native dialogs are substituted because these tests have no Cocoa event loop.
# An injected exit observer proves Quit waits for publication and rejects new work.
# Separate subprocess probes test actual forced exit with contended locks.

from pathlib import Path
import sqlite3
from threading import Event, Thread
from time import monotonic
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from mailarchiver import application as application_module
from mailarchiver.application import ApplicationController, ApplicationPreferencesStore, IngestJob
from mailarchiver.gui_app import GuiApi, PyWebViewApplication, SetupApi
from mailarchiver.gui_service import describe_message
from mailarchiver.writer_lock import WriterLease
from tests.test_gui_service import MULTIPART_MESSAGE, make_gui_archive


@pytest.mark.parametrize("entry", ["setup", "new"])
def test_quit_waits_for_archive_creation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entry: str) -> None:
    """Quit gives both GUI creation paths time to finish both databases and rejects retries."""
    controller = ApplicationController(ApplicationPreferencesStore(tmp_path / "preferences.json"))
    exited, entered, release = Event(), Event(), Event()
    app = PyWebViewApplication(controller, exit_process=lambda _code: exited.set())
    destination = tmp_path / "New.mailarchive"
    destination.mkdir()
    source = tmp_path / "source"
    source.mkdir()
    real_create = application_module.create_search

    def paused_create(path: Path, *, check_same_thread: bool = True) -> sqlite3.Connection:
        assert (destination / "archive.sqlite3").exists()
        entered.set()
        assert release.wait(3)
        return real_create(path, check_same_thread=check_same_thread)

    # A deterministic interruption point is needed between the two real databases.
    monkeypatch.setattr(application_module, "create_search", paused_create)
    monkeypatch.setattr(app, "_refresh_menus", lambda: None)
    monkeypatch.setattr(app, "create_search_window", lambda session: GuiApi(destination, application=app, search_window=session))
    monkeypatch.setattr(app, "_import_document", lambda *_args, **_kwargs: False)
    setup = SetupApi(app)
    setup._source, setup._destination = source, destination
    anchor = SimpleNamespace(create_file_dialog=lambda *_args, **_kwargs: str(destination))
    errors: list[BaseException] = []

    def create() -> None:
        try:
            if entry == "setup":
                setup.start_import()
            else:
                app._create_new_document(anchor)
        except BaseException as error:
            errors.append(error)

    worker = Thread(target=create)
    worker.start()
    try:
        assert entered.wait(3)
        app.request_quit(confirm_ingest=False)
        assert not exited.wait(0.1), "Quit exited between database initializations"
    finally:
        release.set()
        worker.join(3)
    assert not worker.is_alive() and not errors
    assert exited.wait(2)
    assert controller.open_document(destination).path == destination
    if entry == "setup":
        with pytest.raises(ValueError, match="quitting"):
            setup.start_import()
    else:
        assert app._create_new_document(anchor) is None
        assert "quitting" in app.notices()[-1].message


@pytest.mark.parametrize("kind", ["message", "attachment"])
@pytest.mark.parametrize("standalone", [False, True])
def test_quit_waits_for_explicit_save(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, standalone: bool) -> None:
    """Explicit saves, including child windows, finish replacement before Quit and reject retries."""
    archive = make_gui_archive(tmp_path)
    controller = ApplicationController(ApplicationPreferencesStore(tmp_path / "preferences.json"))
    exited, entered, release = Event(), Event(), Event()
    app = PyWebViewApplication(controller, exit_process=lambda _code: exited.set())
    parent = GuiApi(archive, application=app, document=controller.open_document(archive))
    api = parent
    if standalone:
        # Only the native window is substituted; the production child bridge is used.
        monkeypatch.setattr("mailarchiver.gui_app.webview.create_window", MagicMock(return_value=MagicMock()))
        parent.window = SimpleNamespace(get_current_url=lambda: "file:///search.html")
        assert parent.open_message_window(2)
        api = parent.children[0]
        assert api.application is app
        assert api.document is parent.document
    destination = tmp_path / ("saved.eml" if kind == "message" else "saved.pdf")
    api.window = SimpleNamespace(create_file_dialog=lambda *_args, **_kwargs: str(destination))
    attachment = next(item for item in describe_message(archive, 2).attachments if item.content_type == "application/pdf")
    real_replace = Path.replace

    def paused_replace(path: Path, target: str | Path) -> Path:
        if Path(target) == destination:
            entered.set()
            assert release.wait(3)
        return real_replace(path, target)

    # Gate the real atomic replacement, after the temporary file has been fsynced.
    monkeypatch.setattr(Path, "replace", paused_replace)
    errors: list[BaseException] = []

    def save() -> None:
        try:
            if kind == "message":
                api.save_message(2)
            else:
                api.save_attachment(2, attachment.part_id)
        except BaseException as error:
            errors.append(error)

    worker = Thread(target=save)
    worker.start()
    try:
        assert entered.wait(3)
        app.request_quit(confirm_ingest=False)
        assert not exited.wait(0.1), "Quit exited before atomic export replacement"
    finally:
        release.set()
        worker.join(3)
        parent.cleanup_exports()
    assert not worker.is_alive() and not errors
    assert exited.wait(2)
    assert destination.read_bytes() == (MULTIPART_MESSAGE if kind == "message" else b"%PDF-1.4\n")
    assert not list(tmp_path.glob(f".{destination.name}.*.tmp"))
    with pytest.raises(ValueError, match="quitting"):
        if kind == "message":
            api.save_message(2)
        else:
            api.save_attachment(2, attachment.part_id)


@pytest.mark.parametrize("locked", ["application", "controller", "document"])
def test_cancel_quit_with_contended_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, locked: str) -> None:
    """An unknown job snapshot prompts without blocking and Cancel never arms forced exit."""
    controller = ApplicationController(ApplicationPreferencesStore(tmp_path / "preferences.json"))
    document = controller.create_document(tmp_path / "archive")
    exited, entered, release = Event(), Event(), Event()
    app = PyWebViewApplication(controller, exit_process=lambda _code: exited.set())
    assert document.path is not None
    job = IngestJob(operation_id="cancel-probe", owner_window_id="probe")
    lease = WriterLease.acquire(document.path, document.descriptor.identity, "cancel probe", job.operation_id, "test")
    document.begin_ingest(job, lease)
    confirmation = MagicMock(return_value=0)
    monkeypatch.setattr("mailarchiver.gui_app.macos_alert", confirmation)
    anchor = SimpleNamespace(create_confirmation_dialog=MagicMock(return_value=False))
    app._about_window = anchor
    lock = controller._lock if locked == "controller" else document._lock
    if locked == "application":
        lock = app._lock

    def hold() -> None:
        with lock:
            entered.set()
            release.wait(3)

    worker = Thread(target=hold)
    worker.start()
    prior_event = app._quit_done
    try:
        assert entered.wait(3)
        started = monotonic()
        app.request_quit()
        assert monotonic() - started < 0.5
        assert confirmation.called or anchor.create_confirmation_dialog.called
        assert not app._quitting and app._quit_done is prior_event and not exited.is_set()
        assert not job.stop.is_set() and lease.acquired
    finally:
        release.set()
        worker.join(3)
        document.finish_ingest(job.operation_id, published=False)
    assert not app.has_active_ingest()
