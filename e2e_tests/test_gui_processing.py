# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""Headless shipped GUI with real Python bridges and processor databases, no mocked services."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from urllib.parse import urlencode
from pathlib import Path

from playwright.sync_api import Page, expect

from mailarchiver.__main__ import IngestRequest, run_ingest
from mailarchiver.application import IngestJob
from mailarchiver.gui_app import AboutApi, GUI_DIRECTORY, SEARCH_BRIDGE_METHODS, GuiApi, IdentityPickerApi, IngestWindowApi
from mailarchiver.gui_processing import PickerPage, resume_request, unfinished_work
from mailarchiver.ingest_status import IngestStatusFile, read_ingest_history
from mailarchiver.owner_rules import OwnerRules
from mailarchiver.writer_lock import WriterLease
from tests.test_gui_processing import build_entity_archive, build_phone_archive, build_deferred_archive, build_interrupted_archive, open_headless, wait_for_import
from tests.test_gui_service import add_tree_source, make_gui_archive
from tests.test_processing import plugin
from tests.test_cli_processing import HEADER


def test_name_picker_reveals_filtered_company_without_losing_unknown_person(tmp_path: Path,page: Page) -> None:
    """Reversible entity filter uses shipped UI and real SBook-backed archive query."""
    picker=IdentityPickerApi(build_entity_archive(tmp_path),'name')
    expose(page,picker,('query','update'))
    page.goto((GUI_DIRECTORY/'identity.html').as_uri())
    expect(page.locator('tr.group').filter(has_text='王小明')).to_have_count(1)
    expect(page.locator('tr.group').filter(has_text='The SANS Institute')).to_have_count(0)
    expect(page.locator('#summary')).to_contain_text('1 non-person addresses hidden')
    page.locator('#show-non-people').check()
    expect(page.locator('tr.group').filter(has_text='The SANS Institute')).to_have_count(1)
    page.locator('#show-non-people').uncheck()
    expect(page.locator('tr.group').filter(has_text='The SANS Institute')).to_have_count(0)


def expose(page: Page, api: object, names: tuple[str, ...]) -> None:
    for name in names:
        page.expose_function(f"bridge_{name}", getattr(api, name))
    page.add_init_script(f"""window.pywebview = {{api: {{}}}};
        for (const name of {json.dumps(names)}) window.pywebview.api[name] = (...args) => window[`bridge_${{name}}`](...args);
        window.addEventListener('DOMContentLoaded', () => window.dispatchEvent(new Event('pywebviewready')));""")


def test_incomplete_dialog_resumes_content_without_sources(tmp_path: Path, page: Page) -> None:
    """Open asks again after Later; independent checked phases drive the actual background service."""
    archive = build_deferred_archive(tmp_path)
    (tmp_path / "input.eml").unlink()
    app, api = open_headless(archive, tmp_path / "preferences.json")
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    expose(page, api, SEARCH_BRIDGE_METHODS)
    try:
        page.goto((GUI_DIRECTORY / "index.html").as_uri())
        dialog = page.locator("#processing-dialog")
        expect(dialog).to_be_visible()
        expect(page.locator("#continue-ingest")).to_be_checked()
        expect(page.locator("#continue-content")).to_be_checked()
        page.get_by_role("button", name="Ask me later", exact=True).click()
        expect(dialog).not_to_be_visible()
        assert unfinished_work(archive).content > 0
        page.reload()
        expect(dialog).to_be_visible()
        page.locator("#continue-ingest").uncheck()
        page.get_by_role("button", name="Continue Import", exact=True).click()
        expect(dialog).not_to_be_visible()
        if api.document and api.document.ingest_job:
            wait_for_import(api)
        assert not unfinished_work(archive).incomplete
        recovered = read_ingest_history(archive).statuses[0]
        assert recovered.processing_jobs_completed > 0 and not recovered.processing_active
        assert not any(notice.severity == "error" for notice in app.notices())
        page.reload()
        expect(dialog).not_to_be_visible()
        page.locator("#search").fill("subject:GUI")
        page.locator("#search").press("Enter")
        tag = page.locator(".attachment-tag")
        expect(tag).to_have_text("attachment")
        expect(tag).to_have_css("background-color", "rgb(242, 242, 242)")
        page.locator(".result-subject").click()
        expect(page.locator("#message-locations")).to_contain_text("MIME path 2")
        page.get_by_role("button", name="Open parent message").click()
        expect(page.locator("#message-subject")).to_have_text("Parent")
        assert not errors
    finally:
        api.close()


def test_continue_processing_disabled_until_background_work_finishes(tmp_path: Path, page: Page) -> None:
    """Desktop processing: resume disables immediately and every window tracks real active work."""
    plugin(tmp_path, "pause-content", '(item.archive.path / "processor-entered").touch()\n'
           'while not (item.archive.path / "release-processor").exists():\n'
           '    item.check_cancelled()\n    time.sleep(0.01)\nreturn ProcessingResult()')
    manifest = tmp_path / "processors/pause-content/plugin.toml"
    manifest.write_text(manifest.read_text().replace('pipeline="ingest"', 'pipeline="content"'))
    source, archive = tmp_path / "input.eml", tmp_path / "archive"
    source.write_bytes(HEADER + b"\r\nresume button fixture\r\n")
    run_ingest(IngestRequest(archive=archive, roots=[str(source)], scan_policy="not-scanned",
        owner_rules=OwnerRules(include=["owner@example.test"]), continue_content=False, plugin_dir=[tmp_path]), terminal=False)
    app, api = open_headless(archive, tmp_path / "preferences.json")
    assert api.document is not None
    observer_api = GuiApi(archive, application=app, document=api.document,
        search_window=app.controller.new_search_window(api.document))
    observer = page.context.new_page()
    try:
        expose(page, api, SEARCH_BRIDGE_METHODS)
        expose(observer, observer_api, SEARCH_BRIDGE_METHODS)
        for window in (page, observer):
            window.goto((GUI_DIRECTORY / "index.html").as_uri())
            window.get_by_role("button", name="Ask me later", exact=True).click()
            expect(window.locator("#processing-open")).to_be_enabled()
        page.locator("#processing-open").click()
        page.locator("#continue-ingest").uncheck()
        with WriterLease.acquire(archive, api.document.descriptor.identity, "button fixture busy", "button-busy", "test"):
            page.locator("#processing-continue").click()
            expect(page.locator("#processing-error")).to_contain_text("Processing could not start")
            expect(page.locator("#processing-open")).to_be_enabled()
        page.locator("#processing-continue").evaluate("""button => button.addEventListener('click', () => {
            window.resumeDisabledAtClick = document.getElementById('processing-open').disabled;
        })""")
        page.locator("#processing-continue").click()
        assert page.evaluate("window.resumeDisabledAtClick") is True
        expect(page.locator("#processing-dialog")).not_to_be_visible()
        expect(page.locator("#processing-open")).to_be_disabled()
        expect(observer.locator("#processing-open")).to_be_disabled()
        page.reload()
        expect(page.locator("#processing-open")).to_be_disabled()
        expect(page.locator("#ingest-status-line")).to_contain_text("processing jobs finished")
        assert (archive / "processor-entered").exists()
        previews = GUI_DIRECTORY.parent / ".tmp/gui-previews"
        previews.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(previews / "continue-processing-disabled.png"))
        job = api.document.ingest_job
        assert job is not None
        observer.evaluate("""() => {
            const overview = window.pywebview.api.ingest_overview;
            window.overviewDeliveries = [];
            window.pywebview.api.ingest_overview = async () => {
                const result = await overview();
                return new Promise(resolve => window.overviewDeliveries.push({result, resolve}));
            };
        }""")
        (archive / "release-processor").touch()
        assert job.finished.wait(30), "GUI processing did not finish"
        expect(page.locator("#processing-open")).to_be_enabled()
        observer.wait_for_function("window.overviewDeliveries.filter(item => !item.result.active).length >= 2")
        observer.evaluate("""() => {
            const delivery = window.overviewDeliveries.find(item => !item.result.active);
            delivery.resolve(delivery.result);
        }""")
        expect(observer.locator("#processing-open")).to_be_enabled()
        assert not unfinished_work(archive).incomplete
    finally:
        (archive / "release-processor").touch()
        observer.close()
        observer_api.close()
        api.close()
        app.shutdown()


def test_ingest_sources_show_account_and_server(tmp_path: Path, page: Page) -> None:
    """Desktop source labels resolve saved IMAP IDs while keeping locator options out of view."""
    archive = build_interrupted_archive(tmp_path)
    expose(page, IngestWindowApi(archive), ("history", "antivirus", "can_import_directory"))
    page.goto((GUI_DIRECTORY / "ingests.html").as_uri() + "?status=ingest-fixture-2")
    sources = page.locator(".source-list")
    expect(sources).to_have_text("IMAP: sam@example.test on imap.example.test:993")
    assert "saved-account" not in sources.inner_text()
    assert "archive=" not in sources.inner_text()
    assert "workers=" not in sources.inner_text()
    sources.scroll_into_view_if_needed()
    previews = GUI_DIRECTORY.parent / ".tmp/gui-previews"
    previews.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(previews / "imap-source-label.png"))


def test_processing_history_shows_jobs_instead_of_zero_source_progress(tmp_path: Path, page: Page) -> None:
    """Recovery UI: real history bridge renders recorded processor activity and avoids a false ETA."""
    archive = build_interrupted_archive(tmp_path)
    template = read_ingest_history(archive).statuses[0]
    now = datetime.now(UTC)
    worker = template.workers[0].model_copy(update={"phase": "processing mime", "path": "content job 42",
        "activity_done": 1665, "activity_total": None, "activity_unit": "jobs"})
    status = template.model_copy(update={"status_id": "ingest-processing-fixture", "state": "running",
        "started_at": now, "updated_at": now, "completed_at": None, "phase": "recovering archived content",
        "processing_active": True, "processing_job_id": 42, "processing_processor": "mime",
        "processing_jobs_completed": 1665, "active_workers": 1, "workers": [worker], "percent": 0, "eta": "finalizing"})
    status_file = IngestStatusFile(archive, status.status_id)
    status_file.write(status)
    expose(page, IngestWindowApi(archive), ("history", "antivirus", "can_import_directory"))
    page.goto((GUI_DIRECTORY / "ingests.html").as_uri() + f"?status={status.status_id}")
    expect(page.locator(".progress-caption")).to_contain_text("1,665 jobs finished · mime")
    expect(page.locator(".progress-caption")).not_to_contain_text("finalizing")
    expect(page.locator(".worker-table")).to_contain_text("processing mime")
    expect(page.locator(".worker-table")).to_contain_text("1,665 jobs")
    bar = page.get_by_role("progressbar", name="Processing jobs")
    assert bar.get_attribute("aria-valuenow") is None
    expect(page.locator(".progress-fill.indeterminate")).to_be_visible()
    previews = GUI_DIRECTORY.parent / ".tmp/gui-previews"
    previews.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(previews / "processing-progress.png"))
    app, api = open_headless(archive, tmp_path / "preferences.json")
    search_page, about_page = page.context.new_page(), page.context.new_page()
    try:
        status_file.write(status.model_copy(update={"updated_at": datetime.now(UTC)}))
        expose(search_page, api, SEARCH_BRIDGE_METHODS)
        search_page.goto((GUI_DIRECTORY / "index.html").as_uri())
        expect(search_page.locator("#ingest-status-line")).to_contain_text("1,665 processing jobs finished · mime")
        expect(search_page.locator("#ingest-status-line")).not_to_contain_text("0.0%")
        status_file.write(status.model_copy(update={"updated_at": datetime.now(UTC)}))
        expose(about_page, AboutApi(app), ("status",))
        about_page.goto((GUI_DIRECTORY / "about.html").as_uri())
        expect(about_page.locator(".activity")).to_contain_text("1,665 processing jobs finished · mime")
        expect(about_page.locator(".activity")).not_to_contain_text("0.0%")
    finally:
        search_page.close()
        about_page.close()
        api.close()
        app.shutdown()
    (archive / "status" / f"{status.status_id}.json").write_text(
        status.model_copy(update={"state": "failed"}).model_dump_json(), encoding="utf-8")
    page.reload()
    expect(page.locator(".state-badge")).to_have_text("failed")
    expect(page.locator(".progress-fill.indeterminate")).to_have_count(0)


def test_history_startup_and_status_symbols(tmp_path: Path, page: Page) -> None:
    """Desktop accessibility: accepted startup is visible before a run snapshot; status uses symbols."""
    archive = build_interrupted_archive(tmp_path)
    app, api = open_headless(archive, tmp_path / "preferences.json")
    assert api.document is not None and api.search_window is not None
    document = api.document
    template = read_ingest_history(archive).statuses[0]
    job = IngestJob(operation_id="visible-startup", owner_window_id=api.search_window.window_id)
    lease = WriterLease.acquire(archive, document.descriptor.identity, "test", job.operation_id, "test")
    app.controller.begin_ingest(document.descriptor.document_id, job, lease)
    history_api = IngestWindowApi(archive, application=app, document=document)
    expose(page, history_api, ("history", "antivirus", "can_import_directory"))
    try:
        assert api.ingest_overview()["active"]
        page.goto((GUI_DIRECTORY / "ingests.html").as_uri())
        expect(page.get_by_role("status")).to_contain_text("Starting — preparing archive…")
        expect(page.locator("#starting-import .state-dot")).to_have_text("✓")
        for state, symbol in (("failed", "X"), ("disk-full", "X"), ("stale", "X"),
                              ("interrupted", "Ⅱ"), ("completed", "✓"), ("running", "✓")):
            now = datetime.now(UTC)
            status = template.model_copy(update={"status_id": f"ingest-symbol-{state}", "state": state,
                "started_at": now, "updated_at": now, "completed_at": None, "failure_detail": None})
            IngestStatusFile(archive, status.status_id).write(status)
            page.evaluate("window.refreshHistory()")
            dot = page.locator(f".state-dot.{state}").last
            expect(dot).to_have_text(symbol)
            expect(dot).to_have_css("width", "22px")
            expect(dot).to_have_css("height", "22px")
            expect(dot).to_have_attribute("aria-hidden", "true")
            expect(page.locator("#starting-import")).to_be_hidden()
            if state == "failed":
                expect(page.locator(".history-row.selected .state-dot")).to_have_class("state-dot failed")
            page.locator(".history-row").filter(has=page.locator(f".state-dot.{state}")).last.click()
            expect(page.locator(".state-badge")).to_have_text(state)
        app.controller.finish_ingest(document.descriptor.document_id, job.operation_id, published=False)
        assert not history_api.history()["starting"]
        next_job = IngestJob(operation_id="startup-stopped", owner_window_id=api.search_window.window_id)
        next_lease = WriterLease.acquire(archive, document.descriptor.identity, "test", next_job.operation_id, "test")
        app.controller.begin_ingest(document.descriptor.document_id, next_job, next_lease)
        page.evaluate("window.refreshHistory()")
        expect(page.get_by_role("status")).to_be_visible()
        next_job.finished.set()
        page.evaluate("window.refreshHistory()")
        expect(page.get_by_role("status")).to_be_hidden()
        app.controller.finish_ingest(document.descriptor.document_id, next_job.operation_id, published=False)
        old_failure = page.locator(".history-row").filter(has=page.locator(".state-dot.failed"))
        old_failure.click()
        page.evaluate("window.refreshHistory()")
        expect(page.locator(".state-badge")).to_have_text("failed")
        fast_job = IngestJob(operation_id="fast-startup", owner_window_id=api.search_window.window_id)
        fast_lease = WriterLease.acquire(archive, document.descriptor.identity, "test", fast_job.operation_id, "test")
        app.controller.begin_ingest(document.descriptor.document_id, fast_job, fast_lease)
        now = datetime.now(UTC)
        fast_status = template.model_copy(update={"status_id": "ingest-fast-startup", "state": "completed",
            "started_at": now, "updated_at": now, "completed_at": now, "run_pk": template.run_pk + 1})
        IngestStatusFile(archive, fast_status.status_id).write(fast_status)
        app.controller.finish_ingest(document.descriptor.document_id, fast_job.operation_id, published=False)
        # No poll observed this operation's transient starting state or active job.
        page.evaluate("window.refreshHistory()")
        expect(page.locator(".state-badge")).to_have_text("completed")
        expect(page.locator(".detail-heading h1")).to_have_text(f"Ingest {fast_status.run_pk}")
        old_failure.click()
        page.evaluate("window.refreshHistory()")
        expect(page.locator(".state-badge")).to_have_text("failed")
        previews = GUI_DIRECTORY.parent / ".tmp/gui-previews"
        previews.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(previews / "ingest-status-symbols.png"))
    finally:
        if document.ingest_job is not None:
            app.controller.finish_ingest(document.descriptor.document_id, document.ingest_job.operation_id, published=False)
        api.close()


def test_resume_summary_later_abort_and_retained_notices(tmp_path: Path, page: Page) -> None:
    """Recovery requirement: informed decisions never auto-start; collapsed errors stay readable."""
    archive = build_interrupted_archive(tmp_path)
    app, api = open_headless(archive, tmp_path / "preferences.json")
    app.add_notice("error", "Synthetic startup warning: a long explanation that must remain available.")
    expose(page, api, SEARCH_BRIDGE_METHODS)
    try:
        page.goto((GUI_DIRECTORY / "index.html").as_uri())
        dialog = page.locator("#processing-dialog")
        expect(dialog).to_be_visible()
        expect(page.locator("#processing-imports")).to_contain_text("sam@example.test on imap.example.test:993")
        expect(page.locator("#processing-imports")).to_contain_text("Import started:")
        expect(page.locator("#processing-imports")).to_contain_text("interruption time unknown")
        expect(page.locator("#processing-imports")).to_contain_text("Messages imported: 10 · Duplicates ignored: 6")
        assert api.document is not None and api.document.ingest_job is None
        previews = GUI_DIRECTORY.parent / ".tmp/gui-previews"
        previews.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(previews / "import-resume.png"))
        page.get_by_role("button", name="Ask me later", exact=True).click()
        expect(dialog).not_to_be_visible()
        expect(page.locator("#notice-pill")).to_be_visible(timeout=9000)
        page.screenshot(path=str(previews / "notice-pill.png"))
        page.get_by_role("button", name="Messages (1)", exact=True).click()
        expect(page.locator("#error")).to_contain_text("Synthetic startup warning")
        page.evaluate("window.mailArchiverNotice('Second warning with <script>literal text</script>')")
        expect(page.locator("#error")).to_contain_text("Synthetic startup warning")
        expect(page.locator("#error")).to_contain_text("Second warning with <script>literal text</script>")
        assert page.locator("#error script").count() == 0
        page.get_by_role("button", name="Collapse messages", exact=True).click()
        page.get_by_role("button", name="Messages (2)", exact=True).press("Enter")
        expect(page.locator("#error")).to_contain_text("Second warning")
        page.screenshot(path=str(previews / "notice-expanded.png"))
        page.reload()
        expect(dialog).to_be_visible()
        page.get_by_role("button", name="Abort import", exact=True).click()
        expect(dialog).not_to_be_visible()
        assert not unfinished_work(archive).incomplete
        page.reload()
        expect(dialog).not_to_be_visible()
        assert api.document.ingest_job is None
        page.screenshot(path=str(tmp_path / "retained-notice.png"))
    finally:
        api.close()


def test_selector_tiles_roles_dates_and_preserved_terms(tmp_path: Path, page: Page) -> None:
    """Real completion bridge supports every tile, name lookup and date normalization."""
    from mailarchiver.gui_app import GuiApi
    from tests.test_search_completion import completion_archive

    api = GuiApi(completion_archive(tmp_path))
    expose(page, api, SEARCH_BRIDGE_METHODS)
    try:
        page.goto((GUI_DIRECTORY / "index.html").as_uri())
        page.locator("#processing-later").click()
        search = page.locator("#search")
        search.fill("si")
        expect(page.locator("#search-suggestions")).to_be_hidden()
        search.fill("simson")
        expect(page.locator(".suggestion-option").first).to_contain_text("any:")
        page.locator(".suggestion-option").first.click()
        tile = page.locator(".search-chip")
        expect(tile.locator("select option")).to_have_text(["Any: (2)", "From: (1)", "To: (1)", "Cc: (1)"])
        expect(page.locator(".result-subject")).to_have_count(2)
        for role in ("from", "to", "cc"):
            tile.locator("select").select_option(role)
            expect(page.locator(".result-subject")).to_have_count(1)
        tile.locator("button").click()
        search.fill("bcc:hidden")
        expect(page.locator(".suggestion-option").first).to_contain_text("bcc:")
        search.press("ArrowDown")
        search.press("Enter")
        expect(tile.locator("select")).to_have_value("bcc")
        expect(page.locator(".result-subject")).to_have_count(1)
        tile.locator("button").click()
        search.fill("January 5, 2020")
        dates = page.locator(".suggestion-option").filter(has=page.locator(".suggestion-icon", has_text="date:"))
        dates.click()
        expect(tile.locator("select")).to_have_value("date")
        expect(tile.locator(".search-chip-label")).to_have_text("2020-01-05")
        expect(page.locator(".result-subject")).to_have_count(3)
        for role in ("before", "after"):
            tile.locator("select").select_option(role)
            expect(page.locator(".result-subject")).to_have_count(0)
        tile.locator("button").click()
        search.fill("subject:Simson from:simson")
        expect(page.locator(".suggestion-option").first).to_contain_text("from:")
        page.locator(".suggestion-option").first.click()
        expect(search).to_have_value("subject:Simson")
        expect(page.locator(".result-subject")).to_have_count(1)
    finally:
        api.close()


def test_archive_name_picker_saves_filtered_merge_and_rename(tmp_path: Path, page: Page) -> None:
    """Filters and real drag edits preserve hidden addresses, distinct counts and database state."""
    archive = build_deferred_archive(tmp_path)
    run_ingest(resume_request(archive, False, True), terminal=False)
    api = IdentityPickerApi(archive, "name")
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    expose(page, api, ("query", "update"))
    page.goto((GUI_DIRECTORY / "identity.html").as_uri())
    expect(page.locator("tr.group")).to_have_count(4)
    expect(page.locator("#run-matcher")).to_be_disabled()
    page.locator("#domain-filter").fill("example.ac.uk")
    expect(page.locator("tr.group")).to_have_count(3)
    groups = PickerPage.model_validate(api.query({"domain": "example.ac.uk"})).groups
    sender = next(group for group in groups if group.label == "Sender")
    signature = next(group for group in groups if group.label.startswith("signature@"))
    page.locator(f'tr.group[data-group-id="{signature.id}"]').drag_to(page.locator(f'tr.group[data-group-id="{sender.id}"]'))
    expect(page.locator("tr.group")).to_have_count(2)
    page.locator(f'tr.group[data-group-id="{sender.id}"] .row-label').click()
    page.locator("#canonical-name").fill("Saved GUI Name")
    page.get_by_role("button", name="Save name").click()
    expect(page.locator("tr.group").filter(has_text="Saved GUI Name")).to_have_count(1)
    page.locator("#name-filter").fill("Saved GUI Name")
    expect(page.locator("tr.group")).to_have_count(1)
    expect(page.locator("tr.address")).to_have_count(2)
    expect(page.locator("tr.group .number")).to_have_text(["1", "1"])
    expect(page.get_by_role("button", name="Signatures", exact=True)).to_be_visible()
    page.screenshot(path=str(tmp_path / "identity-counts.png"), full_page=True)
    page.locator("#start-date").fill("2025-01-01")
    expect(page.locator("tr.group")).to_have_count(0)
    page.reload()
    expect(page.locator("tr.group").filter(has_text="Saved GUI Name")).to_have_count(1)
    assert not errors


def test_archive_institution_picker_groups_subdomains(tmp_path: Path, page: Page) -> None:
    """The institution subclass reads the same archived addresses and saves organization names."""
    archive = build_deferred_archive(tmp_path)
    run_ingest(resume_request(archive, False, True), terminal=False)
    api = IdentityPickerApi(archive, "institution")
    expose(page, api, ("query", "update"))
    page.goto((GUI_DIRECTORY / "identity.html").as_uri() + "?kind=institution")
    expect(page.locator("tr.group")).to_have_count(2)
    group = page.locator("tr.group").filter(has_text="example.ac.uk")
    expect(group.locator(".number")).to_have_text(["2", "1"])
    group.locator(".row-label").click()
    expect(page.locator("#move")).to_be_disabled()
    page.locator("#canonical-name").fill("Example University")
    page.get_by_role("button", name="Save name").click()
    expect(page.locator("tr.group").filter(has_text="Example University")).to_have_count(1)
    page.locator("#domain-filter").fill("lab.")
    expect(page.locator("tr.address")).to_have_count(1)


def test_sender_selector_cannot_become_a_subject_filter(tmp_path: Path, page: Page) -> None:
    """Regression: from:simsong searches senders even after selecting an autocomplete suggestion."""
    from mailarchiver.__main__ import IngestRequest
    from mailarchiver.gui_app import GuiApi
    from mailarchiver.owner_rules import OwnerRules

    source = tmp_path / "source"
    source.mkdir()
    for number, (sender, subject) in enumerate((("simsong@example.test", "Sender match"),
                                              ("other@example.test", "from:simsong"))):
        (source / f"{number}.eml").write_bytes((f"From: {sender}\nTo: owner@example.test\n"
            f"Message-ID: <selector-{number}@example.test>\nDate: Tue, 02 Jan 2024 10:00:00 +0000\n"
            f"Subject: {subject}\n\nTest message\n").encode())
    archive = tmp_path / "archive"
    run_ingest(IngestRequest(archive=archive, roots=[str(source)], scan_policy="not-scanned",
                            owner_rules=OwnerRules(include=["owner@example.test"])), terminal=False)
    api = GuiApi(archive)
    expose(page, api, SEARCH_BRIDGE_METHODS)
    try:
        page.goto((GUI_DIRECTORY / "index.html").as_uri())
        search = page.locator("#search")
        search.fill("simsong")
        expect(page.locator("#search-suggestions")).to_be_visible()
        search.press("ArrowDown")
        search.fill("from:simsong")
        expect(page.locator("#search-suggestions")).to_be_visible()
        expect(page.locator(".suggestion-icon")).to_have_text(["from:", "from:"])
        search.press("Enter")
        expect(page.locator(".result-subject")).to_have_text(["Sender match"])
        expect(search).to_have_value("from:simsong")
        expect(page.locator(".search-chip")).to_have_count(0)
        search.fill('subject:"from:simsong"')
        search.press("Enter")
        expect(page.locator(".result-subject")).to_have_text(["from:simsong"])
    finally:
        api.close()


def test_picker_double_click_and_count_navigation(tmp_path: Path, page: Page) -> None:
    """Desktop requirement: actual click events open searchable names and exact count projections."""
    archive = build_deferred_archive(tmp_path)
    run_ingest(resume_request(archive, False, True), terminal=False)
    picker = IdentityPickerApi(archive, "name")
    opened: list[dict[str, object]] = []

    def search(selection: dict[str, object]) -> dict[str, object]:
        result = picker.search(selection)
        opened.append(result)
        return result

    expose(page, picker, ("query", "update"))
    page.expose_function("bridge_search", search)
    page.add_init_script("window.pywebview.api.search = (...args) => window.bridge_search(...args);")
    page.goto((GUI_DIRECTORY / "identity.html").as_uri())
    group = page.locator("tr.group").filter(has=page.locator(".row-label", has_text="Sender"))
    group.locator(".row-label").dblclick()
    expect(page.locator("#status")).to_have_text("Opened search in a new window.")
    assert opened.pop()["query"] == "any:Sender"
    group.locator(".row-label").press("Enter")
    page.wait_for_function("document.querySelector('#status').textContent === 'Opened search in a new window.'")
    page.locator("#domain-filter").fill("lab.")
    expect(page.locator("tr.group")).to_have_count(1)
    page.locator("#start-date").fill("2024-01-02")
    page.locator("#end-date").fill("2024-01-02")
    expect(page.locator("tr.group .message-count").first).to_have_text("1")
    page.locator("tr.group .message-count").first.click()
    expect(page.locator("#status")).to_have_text("Opened search in a new window.")
    result = opened[-1]
    scope = result["identity_messages"]
    assert isinstance(scope, dict) and scope["kind"] == "header"
    previews = GUI_DIRECTORY.parent / ".tmp/gui-previews"
    previews.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(previews / "identity-navigation.png"), full_page=True)
    reader = page.context.new_page()
    api = GuiApi(archive)
    expose(reader, api, SEARCH_BRIDGE_METHODS)
    try:
        reader.goto((GUI_DIRECTORY / "index.html").as_uri() + "?" + urlencode({
            "query": "", "identity-messages": json.dumps(scope)}))
        expect(reader.locator(".result-subject")).to_have_text(["Parent"])
        expect(reader.locator(".search-chip")).to_contain_text("Sender · Messages · 2024-01-02")
        reader.evaluate("window.archiveDidChange()")
        expect(reader.locator("#result-status")).to_have_text("1 message")
        reader.screenshot(path=str(previews / "identity-scoped-search.png"))
        reader.locator("#search").fill("childtoken")
        reader.locator("#search").press("Enter")
        expect(reader.locator(".result-subject")).to_have_count(0)
        reader.get_by_role("button", name="Remove identity messages filter").click()
        expect(reader.locator(".result-subject")).to_have_text(["GUI child"])
        reader.screenshot(path=str(previews / "identity-search.png"))
    finally:
        reader.close()
        api.close()


def test_name_picker_phone_disclosure_expands_rows_and_keeps_single_number_plain(tmp_path: Path,page: Page) -> None:
    """Phone requirement: actual saved signature observations display and disclosure expands the row."""
    archive=build_phone_archive(tmp_path)
    api=IdentityPickerApi(archive,'name')
    groups=PickerPage.model_validate(api.query({})).groups
    first=next(group for group in groups if group.addresses[0].address=='first@example.test')
    second=next(group for group in groups if group.addresses[0].address=='second@example.test')
    expose(page,api,('query','update','search'))
    page.goto((GUI_DIRECTORY/'identity.html').as_uri())
    multiple=page.locator(f'tr.group[data-group-id="{first.id}"]')
    single=page.locator(f'tr.group[data-group-id="{second.id}"]')
    expect(multiple.locator('summary')).to_have_text('2 phone numbers')
    collapsed=multiple.evaluate('(row)=>row.offsetHeight')
    multiple.locator('summary').click()
    expect(multiple.locator('.phone-numbers li')).to_have_text(['+1 (617) 555-0102','+44 20 7946 0958'])
    assert multiple.evaluate('(row)=>row.offsetHeight')>collapsed
    multiple.locator('summary').press('Enter')
    expect(multiple.locator('.phone-numbers li').first).to_be_hidden()
    expect(single.locator('.phone-column')).to_have_text('+1 (617) 555-0102')
    expect(single.locator('summary')).to_have_count(0)
    single.drag_to(multiple)
    expect(page.locator('tr.group').filter(has_text='Avery Morgan')).to_have_count(1)
    expect(multiple.locator('summary')).to_have_text('2 phone numbers')
    multiple.locator('summary').click()
    previews=GUI_DIRECTORY.parent/'.tmp/gui-previews'
    previews.mkdir(parents=True,exist_ok=True)
    page.screenshot(path=str(previews/'name-phones.png'),full_page=True)
    page.locator('#end-date').fill('2024-01-02')
    expect(multiple.locator('.phone-column')).to_have_text('+1 (617) 555-0102')
    expect(multiple.locator('summary')).to_have_count(0)


def test_original_imap_folder_names_are_readable_and_selectable(tmp_path: Path, page: Page) -> None:
    """Desktop requirement: real sidebar displays decoded Gmail names and filters by stored tokens."""
    archive = make_gui_archive(tmp_path)
    add_tree_source(archive, '{"stable_id":"imap-sidebar"}', "IMAP", "%5BGmail%5D/All%20Mail", "imap", [1])
    api = GuiApi(archive, e2e_directory=tmp_path, preferences_file=tmp_path / "filters.json")
    expose(page, api, SEARCH_BRIDGE_METHODS)
    try:
        page.goto((GUI_DIRECTORY / "index.html").as_uri())
        page.locator("#show-original-folders").check()
        gmail = page.locator('.mailbox-node[data-label="[Gmail]"]')
        all_mail = page.locator('.mailbox-node[data-label="All Mail"]')
        expect(gmail).to_contain_text("[Gmail] (1)")
        expect(all_mail).to_contain_text("All Mail (1)")
        expect(page.locator("#mailbox-tree")).not_to_contain_text("%20")
        all_mail.locator('input[type="checkbox"]').check()
        expect(page.locator("#result-status")).to_have_text("1 message")
        expect(page.locator(".result")).to_have_count(1)
        page.screenshot(path=str(tmp_path / "imap-folder-labels.png"))
    finally:
        api.close()


def test_shutdown_overlay_dims_content_and_blocks_interaction(tmp_path: Path, page: Page) -> None:
    """Desktop Quit requirement: confirmed shutdown stays visible over dialogs and blocks editing."""
    archive = make_gui_archive(tmp_path)
    api = GuiApi(archive, e2e_directory=tmp_path, preferences_file=tmp_path / "filters.json")
    expose(page, api, SEARCH_BRIDGE_METHODS)
    try:
        page.goto((GUI_DIRECTORY / "index.html").as_uri())
        search = page.locator("#search")
        search.fill("multipart")
        search.press("Enter")
        expect(page.locator(".result")).to_have_count(1)
        page.locator(".result").click()
        page.locator("#message-content").wait_for(state="visible")
        original_part = page.locator("#part-select").input_value()
        # Delay delivery of a real bridge result, leaving its production handler in flight.
        page.evaluate("""() => {
            const original = window.pywebview.api.saved_filter_sets;
            window.pywebview.api.saved_filter_sets = async (...args) => {
                const result = await original(...args);
                await new Promise(resolve => { window.releaseFilterReply = resolve; });
                return result;
            };
            window.filterManagerFinished = false;
            void openFilterManager().finally(() => { window.filterManagerFinished = true; });
        }""")
        page.wait_for_function("typeof window.releaseFilterReply === 'function'")
        page.evaluate("document.querySelector('#processing-dialog').showModal()")
        script = (GUI_DIRECTORY / "shutdown.js").read_text(encoding="utf-8")
        page.evaluate(script)
        overlay = page.get_by_role("dialog", name="Shutting down", exact=True)
        expect(overlay).to_be_visible()
        expect(overlay).to_have_text("Shutting down…")
        expect(overlay).to_have_css("background-color", "rgba(128, 128, 128, 0.2)")
        assert overlay.evaluate("element => getComputedStyle(element, '::backdrop').backgroundColor") == "rgba(0, 0, 0, 0)"
        expect(overlay).to_have_css("color", "rgba(0, 0, 0, 0.5)")
        assert page.locator("#processing-dialog").evaluate("element => !element.open")
        page.evaluate("window.releaseFilterReply()")
        page.wait_for_function("window.filterManagerFinished")
        expect(page.locator("#manage-filter-dialog")).not_to_be_visible()
        expect(overlay).to_be_visible()
        assert overlay.evaluate("element => { const r = element.getBoundingClientRect(); return r.x === 0 && r.y === 0 && r.width === innerWidth && r.height === innerHeight; }")
        page.keyboard.press("Escape")
        expect(overlay).to_be_visible()
        page.keyboard.press("Tab")
        page.keyboard.type("blocked")
        expect(search).to_have_value("multipart")
        for shortcut in ("Control+f", "Meta+f", "Control+2", "Meta+2"):
            page.keyboard.press(shortcut)
            expect(page.locator("#message-find")).to_be_hidden()
            expect(page.locator("#part-select")).to_have_value(original_part)
        page.evaluate(script)
        expect(page.locator("#shutdown-overlay")).to_have_count(1)
        page.screenshot(path=str(tmp_path / "shutdown-overlay.png"))
    finally:
        api.close()
