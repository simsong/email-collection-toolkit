# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""Headless shipped GUI with real Python bridges and processor databases, no mocked services."""
from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import Page, expect

from mailarchiver.__main__ import run_ingest
from mailarchiver.gui_app import GUI_DIRECTORY, SEARCH_BRIDGE_METHODS, IdentityPickerApi
from mailarchiver.gui_processing import PickerPage, resume_request, unfinished_work
from tests.test_gui_processing import build_deferred_archive, open_headless, wait_for_import


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
        page.get_by_role("button", name="Later", exact=True).click()
        expect(dialog).not_to_be_visible()
        assert unfinished_work(archive).content > 0
        page.reload()
        expect(dialog).to_be_visible()
        page.locator("#continue-ingest").uncheck()
        page.get_by_role("button", name="Continue", exact=True).click()
        expect(dialog).not_to_be_visible()
        if api.document and api.document.ingest_job:
            wait_for_import(api)
        assert not unfinished_work(archive).incomplete
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
