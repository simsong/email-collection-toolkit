# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""Headless shipped GUI with real Python bridges and processor databases, no mocked services."""
from __future__ import annotations

import json
from urllib.parse import urlencode
from pathlib import Path

from playwright.sync_api import Page, expect

from mailarchiver.__main__ import run_ingest
from mailarchiver.gui_app import GUI_DIRECTORY, SEARCH_BRIDGE_METHODS, GuiApi, IdentityPickerApi
from mailarchiver.gui_processing import PickerPage, resume_request, unfinished_work
from tests.test_gui_processing import build_entity_archive, build_phone_archive, build_deferred_archive, build_interrupted_archive, open_headless, wait_for_import


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
