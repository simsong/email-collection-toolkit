# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""Desktop processor requirements: real resume, durable edits, leases, and child provenance."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from mailarchiver.__main__ import IngestRequest, run_ingest
from mailarchiver.application import ApplicationController, ApplicationPreferencesStore
from mailarchiver.gui_app import GuiApi, IdentityPickerApi, PyWebViewApplication
from mailarchiver.gui_processing import PickerPage, registered_processors, resume_request, unfinished_work
from mailarchiver.gui_service import describe_message, search_page
from mailarchiver.owner_rules import OwnerRules
from mailarchiver.standalone_verify import verify_archive
from mailarchiver.writer_lock import ArchiveBusyError, WriterLease
from tests.test_cli_processing import HEADER

CHILD = (b"From: Child <child@sub.example.ac.uk>\r\nTo: owner@example.test\r\n"
         b"Message-ID: <gui-child@example.test>\r\nDate: Wed, 03 Jan 2024 12:00:00 +0000\r\n"
         b"Subject: GUI child\r\n\r\nchildtoken\r\n")
RAW = HEADER + (b'Content-Type: multipart/mixed; boundary="mix"\r\n\r\n'
    b'--mix\r\nContent-Type: text/plain\r\n\r\nparenttoken\r\n-- \r\nsignature@dept.example.ac.uk\r\nsender@lab.example.ac.uk\r\nsignature@dept.example.ac.uk\r\n'
    b'--mix\r\nContent-Type: message/rfc822\r\nContent-Disposition: attachment; filename="child.eml"\r\n\r\n'
    + CHILD + b'\r\n--mix--\r\n')


def build_deferred_archive(root: Path) -> Path:
    source = root / "input.eml"
    source.write_bytes(RAW)
    archive = root / "archive"
    run_ingest(IngestRequest(archive=archive, roots=[str(source)], scan_policy="not-scanned",
                            owner_rules=OwnerRules(include=["owner@example.test"]), continue_content=False), terminal=False)
    return archive


def open_headless(archive: Path, preferences: Path) -> tuple[PyWebViewApplication, GuiApi]:
    controller = ApplicationController(ApplicationPreferencesStore(preferences))
    app = PyWebViewApplication(controller)
    document = controller.open_document(archive)
    session = controller.new_search_window(document)
    return app, GuiApi(archive, application=app, document=document, search_window=session)


def wait_for_import(api: GuiApi) -> None:
    assert api.document is not None and api.document.ingest_job is not None
    job = api.document.ingest_job
    assert job.finished.wait(30), "GUI background processing did not finish"


def test_gui_resumes_cli_content_and_displays_attached_provenance(tmp_path: Path) -> None:
    """GUI and CLI share durable queues and policy; content-only resume needs no input source."""
    archive = build_deferred_archive(tmp_path)
    work = unfinished_work(archive)
    assert work.content and not work.ingest and not work.source_roots
    (tmp_path / "input.eml").unlink()
    request = resume_request(archive, False, True)
    assert request.roots == [] and request.owner_rules and request.scan_policy == "not-scanned"
    app, api = open_headless(archive, tmp_path / "preferences.json")
    try:
        assert api.resume_processing(False, True)
        wait_for_import(api)
        assert not unfinished_work(archive).incomplete
        assert not any(notice.severity == "error" for notice in app.notices()), app.notices()
        child = search_page(archive, "subject:GUI").results[0]
        assert child.attached_message
        view = describe_message(archive, child.message_pk)
        assert len(view.attached_origins) == 1
        parent = view.attached_origins[0]
        assert parent.part_path == (2,) and parent.parent_message_pk is not None
        assert describe_message(archive, parent.parent_message_pk).subject == "Parent"
        with sqlite3.connect(archive / "processing.sqlite3") as database:
            assert database.execute("SELECT count(*) FROM invocations WHERE kind='clamav'").fetchone() == (1,)
        assert not verify_archive(archive)
    finally:
        api.close()


def test_picker_edits_filters_unique_counts_and_replay(tmp_path: Path) -> None:
    """Real saved decisions survive processing, filters count observations, and writers exclude edits."""
    archive = build_deferred_archive(tmp_path)
    run_ingest(resume_request(archive, False, True), terminal=False)
    picker = IdentityPickerApi(archive, "name")
    page = PickerPage.model_validate(picker.query({"domain": "example.ac.uk"}))
    sender = next(group for group in page.groups if group.label == "Sender")
    signature = next(group for group in page.groups if group.addresses[0].address.startswith("signature@"))
    assert signature.messages == 0 and signature.signature_messages == 1
    assert sender.messages == 1 and sender.signature_messages == 1
    with WriterLease.acquire(archive, str(archive), "test", "locked", "test"):
        with pytest.raises(ArchiveBusyError):
            picker.update({"operation": "rename-person", "subject": sender.id, "name": "Should not save"})
    picker.update({"operation": "rename-person", "subject": sender.id, "name": "Canonical Sender"})
    picker.update({"operation": "merge-person", "subject": signature.id, "target": sender.id})
    filtered = PickerPage.model_validate(picker.query({"name": "Canonical", "start": "2024-01-02", "end": "2024-01-02"}))
    assert len(filtered.groups) == 1 and len(filtered.groups[0].addresses) == 2
    assert filtered.groups[0].messages == 1
    assert filtered.groups[0].signature_messages == 1  # Channels count independently after merging.
    assert not PickerPage.model_validate(picker.query({"start": "2025-01-01"})).groups
    with pytest.raises(ValueError, match="start date"):
        picker.query({"start": "2025-01-01", "end": "2024-01-01"})
    institutions = PickerPage.model_validate(IdentityPickerApi(archive, "institution").query({}))
    organization = next(group for group in institutions.groups if group.label == "example.ac.uk")
    assert len(organization.addresses) == 3 and organization.messages == 2
    run_ingest(resume_request(archive, False, True).model_copy(update={"reprocess": True}), terminal=False)
    reopened = PickerPage.model_validate(IdentityPickerApi(archive, "name").query({"name": "Canonical"}))
    assert len(reopened.groups[0].addresses) == 2
    address = signature.addresses[0].address_id
    picker.update({"operation": "separate-address", "subject": address})
    assert len(PickerPage.model_validate(picker.query({"domain": "example.ac.uk"})).groups) == 3
    picker.update({"operation": "move-address", "subject": address, "target": sender.id})
    assert len(PickerPage.model_validate(picker.query({"name": "Canonical"})).groups[0].addresses) == 2
    specs = registered_processors(archive)
    assert next(spec for spec in specs if spec.kind == "clamav").rank == 1
    assert any("message/rfc822" in spec.subscribes for spec in specs)


def test_gui_resume_retains_custom_processors_and_installation_configuration(tmp_path: Path) -> None:
    """A GUI continuation must use the original plugin registry and fused installation settings."""
    from mailarchiver.archive_config import load_archive_config

    plugins = tmp_path / "plugins"
    plugin = plugins / "processors" / "gui-proof"
    plugin.mkdir(parents=True)
    (plugin / "plugin.toml").write_text('''api_version=2
plugin_type="processor"
kind="gui-proof"
name="GUI configuration proof"
implementation_version="1"
entrypoint="processor:Processor"
pipeline="content"
subscribes=["text/plain"]
rank=60
''')
    (plugin / "processor.py").write_text('''from mailarchiver.processing.api import ProcessingResult

class Processor:
    def process(self, item):
        value = item.get_my_config()["marker"]
        item.write_my_config({"marker": value, "observed": value})
        return ProcessingResult()
''')
    installation = tmp_path / "installation.yaml"
    installation.write_text("plugins:\n  gui-proof:\n    marker: installation setting\n")
    source = tmp_path / "message.eml"
    source.write_bytes(HEADER + b"\r\nContent\r\n")
    archive = tmp_path / "archive"
    run_ingest(IngestRequest(archive=archive, roots=[str(source)], owner_rules=OwnerRules(include=["owner@example.test"]),
                            scan_policy="not-scanned", continue_content=False, plugin_dir=[plugins],
                            installation_config=installation), terminal=False)
    source.unlink()
    app, api = open_headless(archive, tmp_path / "preferences.json")
    try:
        assert api.resume_processing(False, True)
        wait_for_import(api)
        assert not any(notice.severity == "error" for notice in app.notices()), app.notices()
        assert not unfinished_work(archive).incomplete
        assert load_archive_config(archive).plugins["gui-proof"]["observed"] == "installation setting"
        assert any(plugin.kind == "gui-proof" for plugin in app.about_status().processors)
    finally:
        api.close()


def test_content_only_quit_is_nonblocking_and_leaves_resumable_work(tmp_path: Path) -> None:
    """Skipping ingest must not produce an ingest warning or block the GUI while stopping content."""
    from time import monotonic, sleep

    plugins = tmp_path / "plugins"
    plugin = plugins / "processors" / "wait-for-cancel"
    plugin.mkdir(parents=True)
    (plugin / "plugin.toml").write_text('''api_version=2
plugin_type="processor"
kind="wait-for-cancel"
name="Cooperative content cancellation"
implementation_version="1"
entrypoint="processor:Processor"
pipeline="content"
subscribes=["text/plain"]
rank=60
''')
    (plugin / "processor.py").write_text('''from pathlib import Path
from time import sleep

class Processor:
    def process(self, item):
        Path(__file__).with_name("entered").touch()
        while True:
            item.check_cancelled()
            sleep(0.01)
''')
    source = tmp_path / "message.eml"
    source.write_bytes(HEADER + b"\r\nContent\r\n")
    archive = tmp_path / "archive"
    run_ingest(IngestRequest(archive=archive, roots=[str(source)], owner_rules=OwnerRules(include=["owner@example.test"]),
                            scan_policy="not-scanned", continue_content=False, plugin_dir=[plugins]), terminal=False)
    app, api = open_headless(archive, tmp_path / "preferences.json")
    assert api.document is not None
    try:
        assert api.document.ingest_job is None and not app.has_active_ingest()
        assert api.resume_processing(False, True)
        job = api.document.ingest_job
        assert job is not None and job.kind == "content"
        deadline = monotonic() + 10
        while not (plugin / "entered").exists() and monotonic() < deadline:
            sleep(0.01)
        assert (plugin / "entered").exists()
        assert not app.has_active_ingest()
        started = monotonic()
        app.request_quit()  # No native confirmation is invoked for content-only work.
        assert monotonic() - started < 1
        assert job.finished.wait(10)
        assert api.document.ingest_job is None and unfinished_work(archive).incomplete
        assert not any(notice.severity == "error" for notice in app.notices()), app.notices()
    finally:
        app.shutdown()
        api.close()


def test_quit_with_deferred_but_not_started_work_does_not_start_processing(tmp_path: Path) -> None:
    """Merely opening an archive and skipping its pending work does not create a live ingest job."""
    archive = build_deferred_archive(tmp_path)
    app, api = open_headless(archive, tmp_path / "preferences.json")
    before = unfinished_work(archive)
    try:
        assert api.document is not None and api.document.ingest_job is None
        assert not app.has_active_ingest()
        app.request_quit()
        assert unfinished_work(archive) == before
        assert api.document.ingest_job is None
    finally:
        app.shutdown()
        api.close()
