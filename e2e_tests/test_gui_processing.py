# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""Headless shipped GUI with real Python bridges and processor databases, no mocked services."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from playwright.sync_api import Page, expect

from mailarchiver.__main__ import run_ingest
from mailarchiver.gui_app import AboutApi, GUI_DIRECTORY, SEARCH_BRIDGE_METHODS, IdentityPickerApi, IngestWindowApi
from mailarchiver.gui_processing import PickerPage, resume_request, unfinished_work
from mailarchiver.ingest_status import IngestStatusFile, read_ingest_history
from tests.test_gui_processing import build_deferred_archive, build_interrupted_archive, open_headless, wait_for_import


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
