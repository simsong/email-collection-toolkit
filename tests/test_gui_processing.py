# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""Desktop processor requirements: real resume, durable edits, leases, and child provenance."""
from __future__ import annotations

import sqlite3
from email.header import Header
import os
from collections.abc import Callable
from pathlib import Path
from threading import Event
from datetime import UTC, datetime, timedelta

import pytest

from mailarchiver.__main__ import IngestRequest, run_ingest
from mailarchiver.application import ApplicationController, ApplicationPreferencesStore
from mailarchiver.document_options import DocumentOptions
from mailarchiver.gui_app import GuiApi, GuiIngestHistory, IdentityPickerApi, IngestWindowApi, PyWebViewApplication
from mailarchiver.gui_processing import PickerPage, PickerSearch, abort_processing, registered_processors, resume_request, unfinished_work
from mailarchiver.ingest_status import IngestCounts, IngestStatusFile, read_ingest_history
from mailarchiver.archive_config import load_archive_config, save_archive_config
from mailarchiver.imap_models import ImapAccount
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


def open_headless(archive: Path, preferences: Path, *, exit_process: Callable[[int], object] = os._exit) -> tuple[PyWebViewApplication, GuiApi]:
    controller = ApplicationController(ApplicationPreferencesStore(preferences))
    app = PyWebViewApplication(controller, exit_process=exit_process)
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
    with WriterLease.acquire(archive, str(archive), "fixture owner edit", "resume-owners", "test") as lease:
        DocumentOptions(archive).save(OwnerRules(include=["later@example.test"]), lease)
    config = (archive / "config.yaml").read_bytes()
    run_ingest(resume_request(archive, False, True), terminal=False)
    assert (archive / "config.yaml").read_bytes() == config
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
    assert (archive / "config.yaml").read_bytes() == config
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
    exited = Event()
    app, api = open_headless(archive, tmp_path / "preferences.json", exit_process=lambda _code: exited.set())
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
        assert exited.wait(5)
        assert api.document.ingest_job is None and unfinished_work(archive).incomplete
        assert not any(notice.severity == "error" for notice in app.notices()), app.notices()
    finally:
        app.shutdown()
        api.close()


def test_quit_with_deferred_but_not_started_work_does_not_start_processing(tmp_path: Path) -> None:
    """Merely opening an archive and skipping its pending work does not create a live ingest job."""
    archive = build_deferred_archive(tmp_path)
    exited = Event()
    app, api = open_headless(archive, tmp_path / "preferences.json", exit_process=lambda _code: exited.set())
    before = unfinished_work(archive)
    try:
        assert api.document is not None and api.document.ingest_job is None
        assert not app.has_active_ingest()
        app.request_quit()
        assert exited.wait(2)
        assert unfinished_work(archive) == before
        assert api.document.ingest_job is None
    finally:
        app.shutdown()
        api.close()


def build_interrupted_archive(root: Path) -> Path:
    """Real archive/queues plus recorded run snapshots, including an abandoned heartbeat."""
    archive = build_deferred_archive(root)
    config = load_archive_config(archive)
    config.imap_sources = [ImapAccount(id="saved-account", server="imap.example.test", username="sam@example.test")]
    save_archive_config(archive, config)
    template = read_ingest_history(archive).statuses[0]
    start = datetime(2026, 10, 1, 10, tzinfo=UTC)
    source = "imap://saved-account?archive=fixture&workers=8"
    for attempt, state, archived, duplicates in ((0, "completed", 100, 50), (1, "interrupted", 7, 2), (2, "running", 3, 4)):
        timestamp = start + timedelta(days=attempt)
        status = template.model_copy(update={"status_id": f"ingest-fixture-{attempt}",
            "source_roots": [source.replace("workers=8", f"workers={attempt + 1}")],
            "started_at": timestamp, "updated_at": timestamp + timedelta(hours=1),
            "completed_at": None if state == "running" else timestamp + timedelta(hours=1),
            "state": state, "counts": IngestCounts(archived=archived, duplicates=duplicates)})
        IngestStatusFile(archive, status.status_id).write(status)
    return archive


@pytest.mark.parametrize("configuration", ["present", "missing", "invalid"])
def test_history_labels_preserve_saved_imap_locators(tmp_path: Path, configuration: str) -> None:
    """Desktop Sources shows account/server labels without changing resumable history."""
    archive = build_interrupted_archive(tmp_path)
    if configuration == "missing":
        (archive / "config.yaml").unlink()
    elif configuration == "invalid":
        (archive / "config.yaml").write_text("imap_sources: invalid\n", encoding="utf-8")
    snapshots = {path: path.read_bytes() for path in (archive / "status").glob("ingest-*.json")}
    before = read_ingest_history(archive)
    history = GuiIngestHistory.model_validate(IngestWindowApi(archive).history())
    for status in history.statuses:
        original = next(item for item in before.statuses if item.status_id == status.status_id)
        assert status.source_roots == original.source_roots
        if status.status_id.startswith("ingest-fixture-"):
            expected = "IMAP: sam@example.test on imap.example.test:993" if configuration == "present" else "IMAP: account unavailable"
            assert status.source_labels == [expected]
        else:
            assert status.source_labels == original.source_roots
    assert all(path.read_bytes() == saved for path, saved in snapshots.items())


def test_saved_import_summary_abort_and_new_attempt(tmp_path: Path) -> None:
    """Desktop recovery: source identity, cumulative attempts, crash uncertainty and durable abort."""
    archive = build_interrupted_archive(tmp_path)
    work = unfinished_work(archive)
    assert len(work.imports) == 1
    saved = work.imports[0]
    assert saved.sources == ["IMAP: sam@example.test on imap.example.test:993"]
    assert saved.started_at == datetime(2026, 10, 2, 10, tzinfo=UTC)
    assert saved.interrupted_at == datetime(2026, 10, 3, 11, tzinfo=UTC)
    assert saved.interruption_estimated and saved.archived == 10 and saved.duplicates == 6
    assert saved.status_ids == ["ingest-fixture-2", "ingest-fixture-1"]
    history = {path: path.read_bytes() for path in (archive / "status").glob("ingest-*.json")}
    canonical = {path: path.read_bytes() for path in (archive / "data").rglob("*") if path.is_file()}
    with WriterLease.acquire(archive, str(archive), "fixture active import", "active", "test"):
        with pytest.raises(ArchiveBusyError):
            abort_processing(archive, saved.status_ids)
    with pytest.raises(ValueError, match="Saved imports changed"):
        abort_processing(archive, ["ingest-fixture-1"])
    assert unfinished_work(archive) == work
    abort_processing(archive, saved.status_ids)
    assert not unfinished_work(archive).incomplete
    assert all(path.read_bytes() == content for path, content in (history | canonical).items())
    with sqlite3.connect(archive / "processing.sqlite3") as database:
        assert database.execute("SELECT count(*) FROM jobs WHERE status='aborted'").fetchone()[0] > 0
    # A later, explicit import is a new continuation, not suppressed by the old abort.
    later = next(status for status in read_ingest_history(archive).statuses
                 if status.status_id == "ingest-fixture-1").model_copy(update={
        "status_id": "ingest-fixture-new", "started_at": datetime(2026, 10, 4, tzinfo=UTC),
        "state": "interrupted", "completed_at": datetime(2026, 10, 4, 1, tzinfo=UTC),
        "counts": IngestCounts(archived=1)})
    IngestStatusFile(archive, later.status_id).write(later)
    new_work = unfinished_work(archive)
    assert len(new_work.imports) == 1
    assert new_work.imports[0].archived == 1
    assert not new_work.imports[0].interruption_estimated


def test_abort_cleanup_duplicate_admission_and_explicit_reprocessing(tmp_path: Path) -> None:
    """Aborted derived jobs cannot be revived with collected inputs; reprocessing regenerates them."""
    archive = build_deferred_archive(tmp_path)
    run_ingest(resume_request(archive, False, True).model_copy(update={"max_content_jobs": 1}), terminal=False)
    assert unfinished_work(archive).content
    abort_processing(archive, [])
    with sqlite3.connect(archive / "processing.sqlite3") as database:
        cancelled = [row[0] for row in database.execute("SELECT job_id FROM jobs WHERE status='aborted'")]
    assert cancelled
    # A no-op writer performs real object collection after the abort.
    run_ingest(resume_request(archive, False, True), terminal=False)
    copied = tmp_path / "duplicate.eml"
    copied.write_bytes(RAW)
    run_ingest(IngestRequest(archive=archive, roots=[str(copied)], scan_policy="not-scanned",
                            owner_rules=OwnerRules(include=["owner@example.test"])), terminal=False)
    with sqlite3.connect(archive / "processing.sqlite3") as database:
        assert all(database.execute("SELECT status FROM jobs WHERE job_id=?", (job,)).fetchone() == ("aborted",)
                   for job in cancelled)
        assert database.execute("SELECT count(*) FROM jobs WHERE status='failed'").fetchone() == (0,)
    assert not unfinished_work(archive).incomplete
    assert not search_page(archive, "subject:GUI").results
    run_ingest(resume_request(archive, False, True).model_copy(update={"reprocess": True}), terminal=False)
    assert search_page(archive, "subject:GUI").results[0].attached_message
    assert not unfinished_work(archive).incomplete
    assert not verify_archive(archive)


def test_abort_rejects_a_write_after_quit_is_reserved(tmp_path: Path) -> None:
    """All desktop writes, including cancelling saved work, obey the application shutdown boundary."""
    archive = build_deferred_archive(tmp_path)
    exited = Event()
    app, api = open_headless(archive, tmp_path / "preferences.json", exit_process=lambda _status: exited.set())
    before = unfinished_work(archive)
    try:
        app.request_quit(confirm_ingest=False)
        assert exited.wait(2)
        with pytest.raises(ValueError, match="quitting"):
            api.abort_processing([])
        assert unfinished_work(archive) == before
    finally:
        app.shutdown()
        api.close()


def test_picker_count_search_uses_exact_filtered_header_and_signature_evidence(tmp_path: Path) -> None:
    """Desktop requirement: clicked counts select distinct evidence messages, including signatures."""
    archive = build_deferred_archive(tmp_path)
    run_ingest(resume_request(archive, False, True), terminal=False)
    picker = IdentityPickerApi(archive, "name")
    groups = PickerPage.model_validate(picker.query({})).groups
    sender = next(group for group in groups if group.label == "Sender")
    child = next(group for group in groups if group.label == "Child")
    signature = next(group for group in groups if group.label.startswith("signature@"))
    for member in (signature, child):
        picker.update({"operation": "merge-person", "subject": member.id, "target": sender.id})
    api = GuiApi(archive)
    try:
        for action, expected in (("header", 2), ("signature", 1)):
            result = PickerSearch.model_validate(picker.search({"group_id": sender.id, "action": action}))
            assert result.identity_messages is not None
            scope = result.identity_messages.model_dump(mode="json")
            rows = api.search("", identity_messages=scope)["results"]
            assert len(rows) == expected
            assert api.search_count("", identity_messages=scope)["total"] == expected
            assert len(api.search("", limit=1, offset=1, identity_messages=scope)["results"]) == expected - 1
            if action == "header":
                assert len(api.search("parenttoken", identity_messages=scope)["results"]) == 1
        selected = PickerSearch.model_validate(picker.search({"group_id": sender.id, "action": "header",
            "filters": {"domain": "lab.", "start": "2024-01-02", "end": "2024-01-02"}}))
        assert selected.identity_messages is not None
        assert selected.identity_messages.address_ids == [sender.addresses[0].address_id]
        scope = selected.identity_messages.model_dump(mode="json")
        assert [row["subject"] for row in api.search("", identity_messages=scope)["results"]] == ["Parent"]
        signature_search = PickerSearch.model_validate(picker.search({"group_id": sender.id,
            "address_id": signature.addresses[0].address_id, "action": "signature"}))
        assert signature_search.identity_messages is not None
        assert len(search_page(archive, "", identity_messages=signature_search.identity_messages).results) == 1
        with pytest.raises(ValueError, match="no longer visible"):
            picker.search({"group_id": sender.id, "address_id": 999999, "action": "header"})
        with pytest.raises(ValueError, match="no longer visible"):
            picker.search({"group_id": sender.id, "action": "header", "filters": {"start": "2025-01-01"}})
    finally:
        api.close()


def build_phone_archive(tmp_path: Path, first_address: str = "first@example.test") -> Path:
    """Purpose-made three-message phone evidence collection."""
    source=tmp_path/'phones'
    source.mkdir()
    for index,(sender,day,phones) in enumerate(((first_address,2,'+1 (617) 555-0102\n+1 617 555 0102'),
        (first_address,3,'+44 20 7946 0958'),('second@example.test',3,'+1 (617) 555-0102'))):
        (source/f'{index}.eml').write_text(f'From: Avery Morgan <{sender}>\nTo: owner@example.test\n'
            f'Message-ID: <phones-{index}@example.test>\nDate: Wed, {day:02d} Jan 2024 10:00:00 +0000\n'
            f'Subject: Phone fixture\n\nMessage body.\n-- \nAvery Morgan\n{phones}\n')
    archive=tmp_path/'archive'
    run_ingest(IngestRequest(archive=archive,roots=[str(source)],scan_policy='not-scanned',
        owner_rules=OwnerRules(include=['owner@example.test'])),terminal=False)
    return archive


def test_email_label_search_retains_visible_service_and_date_filters(tmp_path: Path) -> None:
    """DESKTOP: email-labelled group navigation retains visibility and the displayed date scope."""
    archive = build_phone_archive(tmp_path, "support@example.test")
    picker = IdentityPickerApi(archive, "name")
    filters = {"include_non_people": True, "end": "2024-01-02"}
    group = next(row for row in PickerPage.model_validate(picker.query(filters)).groups
                 if row.addresses[0].address == "support@example.test")
    picker.update({"operation": "rename-person", "subject": group.id, "name": "support@example.test"})
    selected = PickerSearch.model_validate(picker.search({"group_id": group.id, "filters": filters}))
    assert selected.identity_messages is not None
    results = search_page(archive, selected.query, identity_messages=selected.identity_messages).results
    assert len(results) == group.messages == 1
    assert len(search_page(archive, "any:support@example.test").results) == 2


def build_entity_archive(tmp_path: Path) -> Path:
    """A company and an uncertain international name, both real ingested observations."""
    source=tmp_path/'entities'
    source.mkdir()
    for index,(name,sender) in enumerate([('The SANS Institute','training@example.test'),('王小明','wang@example.test')]):
        (source/f'{index}.eml').write_text(f'From: {Header(name,"utf-8").encode()} <{sender}>\nTo: owner@example.test\n'
            f'Message-ID: <entity-{index}@example.test>\nDate: Tue, 02 Jan 2024 10:00:00 +0000\nSubject: Entity fixture\n\nBody\n')
    archive=tmp_path/'archive'
    run_ingest(IngestRequest(archive=archive,roots=[str(source)],scan_policy='not-scanned',
        owner_rules=OwnerRules(include=['owner@example.test'])),terminal=False)
    return archive


def test_name_picker_filters_companies_reversibly_without_removing_evidence(tmp_path: Path) -> None:
    """Person-only views hide recognized organizations, retain unknown names and preserve search/counts."""
    archive=build_entity_archive(tmp_path)
    picker=IdentityPickerApi(archive,'name')
    default=PickerPage.model_validate(picker.query({}))
    assert default.excluded_addresses==1
    assert any(row.label=='王小明' for row in default.groups)
    assert not any(row.label=='The SANS Institute' for row in default.groups)
    all_rows=PickerPage.model_validate(picker.query({'include_non_people':True}))
    company=next(row for row in all_rows.groups if row.label=='The SANS Institute')
    assert company.messages==1 and all_rows.total_addresses==default.total_addresses
    assert any(row.label=='example.test' for row in PickerPage.model_validate(IdentityPickerApi(archive,'institution').query({})).groups)


def test_signature_phone_numbers_follow_grouping_and_date_filters(tmp_path: Path) -> None:
    """Name picker requirement: observed phones deduplicate, follow manual groups and stay dated."""
    archive=build_phone_archive(tmp_path)
    picker=IdentityPickerApi(archive,'name')
    groups=PickerPage.model_validate(picker.query({})).groups
    first=next(group for group in groups if group.addresses[0].address=='first@example.test')
    second=next(group for group in groups if group.addresses[0].address=='second@example.test')
    assert {phone.normalized for phone in first.phone_numbers}=={'+16175550102','+442079460958'}
    assert first.signature_messages==0  # A phone is not an email-address signature appearance.
    picker.update({'operation':'merge-person','subject':second.id,'target':first.id})
    merged=PickerPage.model_validate(picker.query({'name':'Avery Morgan'})).groups[0]
    assert len(merged.phone_numbers)==2
    dated=PickerPage.model_validate(picker.query({'name':'Avery Morgan','end':'2024-01-02'})).groups[0]
    assert [phone.normalized for phone in dated.phone_numbers]==['+16175550102']
    run_ingest(resume_request(archive,False,True).model_copy(update={'reprocess':True}),terminal=False)
    assert len(PickerPage.model_validate(picker.query({'name':'Avery Morgan'})).groups[0].phone_numbers)==2


@pytest.mark.parametrize('quoted',[
    '-----Original Message-----\nFrom: Other <other@example.test>\nMessage\n-- \nOther\n+1 212 555 0199\n',
    'On Tuesday, Other wrote:\n'+'Old message line\n'*20+'+1 212 555 0199\n',
    '---------- Forwarded message ---------\nOld message\n-- \nOther\n+1 212 555 0199\n',
])
def test_phone_processor_trims_replies_before_selecting_sender_signature(tmp_path: Path, quoted: str) -> None:
    """Phone ownership boundary: quoted signatures and long replies never become sender evidence."""
    source=tmp_path/'reply.eml'
    source.write_text('From: Current Sender <sender@example.test>\nTo: owner@example.test\n'
        'Date: Tue, 02 Jan 2024 10:00:00 +0000\nMessage-ID: <reply@example.test>\n'
        'Subject: Reply\n\nCurrent body\nPhone: +1 (617) 555-0102\n'+quoted)
    archive=tmp_path/'archive'
    run_ingest(IngestRequest(archive=archive,roots=[str(source)],scan_policy='not-scanned',
        owner_rules=OwnerRules(include=['owner@example.test'])),terminal=False)
    group=PickerPage.model_validate(IdentityPickerApi(archive,'name').query({'mailbox':'sender'})).groups[0]
    assert [phone.normalized for phone in group.phone_numbers]==['+16175550102']
