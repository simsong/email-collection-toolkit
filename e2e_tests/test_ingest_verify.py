# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

# Exercise CLI ingestion and the production desktop service with disposable mail.
# Committed fixtures cover canonical bytes, search, rendering, and import lifecycle.
# GUI bridge calls reach the real service without replacing it with a mock.
# Independent installed verification checks the resulting BagIt/Mailbag archive.
# Native acceptance is explicitly gated; headless checks do not claim native coverage.
"""Fresh-process ingest, verification, and native-search acceptance test."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from pydantic import BaseModel

from e2e_tests.eicar_fixture import write_eicar_emlx
from e2e_tests.generate_corpus import RICH_MESSAGE
from mailarchiver.application import ApplicationController, ApplicationPreferencesStore
from mailarchiver.__main__ import IngestRequest, run_ingest
from mailarchiver.catalog import address_pk, create_catalog, create_search
from mailarchiver.gui_app import (
    E2E_DRIVER,
    GUI_DIRECTORY,
    AboutApi,
    GuiApi,
    GuiE2EClientResult,
    IngestWindowApi,
    NativeSmokeApi,
    NativeSmokeController,
    NativeSmokeReport,
    PyWebViewApplication,
    ProcessorGraphApi,
)
from mailarchiver.gui_service import MessageView, PartContent, SearchPage, write_message
from mailarchiver.reader_fixture import inventory
from mailarchiver.processing.store import connect as connect_processing
from mailarchiver.search import index_message
from mailarchiver.owner_rules import OwnerRules
from mailarchiver.standalone_verify import INSTALLED_NAME

DATA = Path(__file__).parent / "data"
PROCESSED_MESSAGE_COUNT = 210
GUI_API_METHODS = (
    "activate", "attachment", "choose_archive", "delete_filter_set", "mailbox_tree", "message",
    "copy_link", "copy_source_path", "copy_visible_text", "ingest_overview", "open_attachment", "open_ingest_window", "open_link", "open_message_window", "part", "prepare_drag", "rename_filter_set",
    "request_previews", "save_attachment", "save_filter_set", "save_message",
    "saved_filter_sets", "search", "search_count", "status", "suggestions", "suggestions_start", "suggestions_status", "suggestions_cancel", "take_previews",
)
NATIVE_SMOKE_MESSAGE = (
    b"Message-ID: <native-smoke@example>\nFrom: sender@example.net\n"
    b"Subject: Native smoke\nDate: Wed, 03 Jan 2024 10:00:00 +0000\n\n"
    b"The message viewer bridge is ready.\n"
)
NATIVE_SMOKE_API_METHODS = ("status", "search", "native_smoke_complete")
PROBE_ARCHIVE_ENV = "MAILARCHIVER_NATIVE_PROBE_ARCHIVE"
PROBE_PREFERENCES_ENV = "MAILARCHIVER_NATIVE_PROBE_PREFERENCES"


class BuiltArchive(BaseModel):
    """Artifacts and captured output from one real ingest."""

    archive: Path
    source: Path
    infected_raw: bytes
    ingest_stdout: str
    ingest_stderr: str


@pytest.fixture(scope="module")
def built_archive(tmp_path_factory: pytest.TempPathFactory) -> BuiltArchive:
    """Ingest the committed corpus once, creating the antivirus sample only temporarily."""
    temporary = tmp_path_factory.mktemp("mailarchiver-e2e")
    source = temporary / "source"
    shutil.copytree(DATA / "source", source)
    owner_names = temporary / "owner-names.txt"
    owner_names.write_text("archive-owner@example.org\n", encoding="utf-8")
    infected_path = source / "Professional/Quarantine/runtime-infected.emlx"
    infected_raw = write_eicar_emlx(DATA / "infected.emlx.template", infected_path)
    archive = temporary / "archive"

    try:
        ingested = subprocess.run(
            [
                sys.executable,
                "-m",
                "mailarchiver",
                "--archive",
                str(archive),
                "ingest",
                "--owner-names-file",
                str(owner_names),
                "--clamav",
                "--index-attachments",
                "--workers",
                "2",
                str(source),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    finally:
        infected_path.unlink(missing_ok=True)

    assert not infected_path.exists()
    assert ingested.returncode == 0, ingested.stdout + ingested.stderr
    return BuiltArchive(
        archive=archive,
        source=source,
        infected_raw=infected_raw,
        ingest_stdout=ingested.stdout,
        ingest_stderr=ingested.stderr,
    )


@pytest.fixture(scope="module")
def native_smoke_archive(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build the smallest derived archive that proves a real bridge search."""
    archive = tmp_path_factory.mktemp("native-smoke-archive")
    catalog = create_catalog(archive / "archive.sqlite3")
    search = create_search(archive / "search.sqlite3")
    try:
        sender = address_pk(catalog, "sender@example.net")
        catalog.execute(
            "INSERT INTO messages(message_id_normalized, sha256, sender_address_pk, subject, date_utc, date_source, category) "
            "VALUES ('native-smoke@example', ?, ?, 'Native smoke', '2024-01-03T10:00:00+00:00', 'date', 'Archive')",
            (hashlib.sha256(NATIVE_SMOKE_MESSAGE).hexdigest(), sender),
        )
        index_message(search, NATIVE_SMOKE_MESSAGE, False)
        catalog.commit()
        search.commit()
    finally:
        catalog.close()
        search.close()
    return archive


def test_search_ui_end_to_end_without_a_window(
    built_archive: BuiltArchive, tmp_path: Path, page: Page
) -> None:
    """Drive the shipped UI headlessly while every bridge call reaches the real service."""
    page_errors: list[str] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    export_directory = tmp_path / "gui-e2e-exports"
    export_directory.mkdir()
    api = GuiApi(
        built_archive.archive, export_directory, export_directory,
        export_directory / "filter-sets.json",
    )
    try:
        for name in GUI_API_METHODS:
            page.expose_function(f"mailarchive_{name}", getattr(api, name))
        names = json.dumps(GUI_API_METHODS)
        page.add_init_script(
            f"const names = {names}; window.pywebview = {{api: {{}}}}; "
            "for (const name of names) window.pywebview.api[name] = "
            "(...args) => window[`mailarchive_${name}`](...args); "
            "window.addEventListener('DOMContentLoaded', () => "
            "window.dispatchEvent(new Event('pywebviewready')));",
        )
        page.goto((GUI_DIRECTORY / "index.html").as_uri())
        page.evaluate(E2E_DRIVER.read_text(encoding="utf-8"))
        page.wait_for_function("window.__mailarchiveE2E", timeout=90_000)
        result = GuiE2EClientResult.model_validate(page.evaluate("window.__mailarchiveE2E"))
    finally:
        api.close()

    assert result.passed, result.error
    assert not page_errors, page_errors
    assert len(result.checks) >= 30
    exports = {path.name for path in export_directory.iterdir()}
    assert {"saved-tiny.png", "saved-review.command", "filter-sets.json"} <= exports
    saved = list(export_directory.glob("saved-Rich UI message-*.eml"))
    assert len(saved) == 1 and saved[0].read_bytes() == RICH_MESSAGE
    if sys.platform == "darwin":
        dragged = list((export_directory / "drags").rglob("Rich UI message-*.eml"))
        assert len(dragged) == 1 and dragged[0].read_bytes() == RICH_MESSAGE
    else:
        assert not (export_directory / "drags").exists()


def test_message_splitter_resizes_panes_not_columns(
    built_archive: BuiltArchive, tmp_path: Path, page: Page,
) -> None:
    """Requirement: real pointer/keyboard resizing preserves results and avoids list overflow."""
    api = GuiApi(built_archive.archive, tmp_path, tmp_path, tmp_path / "filters.json")
    try:
        for name in GUI_API_METHODS:
            page.expose_function(f"mailarchive_{name}", getattr(api, name))
        page.add_init_script(
            f"const names = {json.dumps(GUI_API_METHODS)}; window.pywebview = {{api: {{}}}}; "
            "for (const name of names) window.pywebview.api[name] = "
            "(...args) => window[`mailarchive_${name}`](...args); "
            "window.addEventListener('DOMContentLoaded', () => "
            "window.dispatchEvent(new Event('pywebviewready')));",
        )
        page.set_viewport_size({"width": 1400, "height": 900})
        page.goto((GUI_DIRECTORY / "index.html").as_uri())
        expect(page.locator("#result-status")).to_have_text("Enter a search.")
        # Invalid selectors are inline feedback; correcting the query still searches.
        page.locator("#search").fill("from:")
        page.locator("#search").press("Enter")
        expect(page.locator("#result-status")).to_have_text("from: requires a value")
        page.locator("#search").fill("message")
        page.locator("#search").press("Enter")
        page.locator(".result").first.click()
        page.wait_for_function("state.selected !== null && state.view !== null")
        selection = page.evaluate("[state.selected, [...state.resultSelection], state.results.length]")
        splitter = page.get_by_role("separator", name="Message list width")
        results = page.locator("#results-pane")
        preview = page.locator("#message-pane")

        def width(selector: str) -> float:
            return page.locator(selector).evaluate("element => element.getBoundingClientRect().width")

        def assert_fitted() -> None:
            page.wait_for_function("""() => {
                const holder = document.querySelector('#result-list .tabulator-tableholder');
                const cell = document.querySelector('#result-list .tabulator-cell');
                return holder && cell && holder.scrollWidth <= holder.clientWidth
                    && Math.abs(cell.getBoundingClientRect().width - holder.clientWidth) <= 2;
            }""")
            assert preview.evaluate("element => element.getBoundingClientRect().right <= innerWidth + 1")

        before_list = width("#results-pane")
        before_preview = width("#message-pane")
        box = splitter.bounding_box()
        assert box is not None
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + 100)
        page.mouse.down()
        page.mouse.move(box["x"] + box["width"] / 2 + 150, box["y"] + 100, steps=12)
        page.mouse.up()
        assert abs(width("#results-pane") - before_list - 150) < 2
        assert abs(width("#message-pane") - before_preview + 150) < 2
        assert page.evaluate("[state.selected, [...state.resultSelection], state.results.length]") == selection
        assert_fitted()

        # The old column-edge gesture must no longer resize a Tabulator cell.
        cell = page.locator("#result-list .tabulator-cell").first.bounding_box()
        assert cell is not None
        page.mouse.move(cell["x"] + cell["width"] - 2, cell["y"] + 10)
        page.mouse.down()
        page.mouse.move(cell["x"] + cell["width"] - 100, cell["y"] + 10, steps=6)
        page.mouse.up()
        assert abs(width("#results-pane") - before_list - 150) < 2
        assert_fitted()

        splitter.focus()
        splitter.press("ArrowLeft")
        assert abs(width("#results-pane") - before_list - 140) < 2
        splitter.press("Home")
        assert abs(width("#results-pane") - 300) < 2
        splitter.press("End")
        assert abs(width("#message-pane") - 320) < 2
        assert_fitted()
        page.locator("#show-original-folders").check()
        expect(page.locator("#mailbox-browser")).to_be_visible()
        assert_fitted()
        page.set_viewport_size({"width": 800, "height": 700})
        assert_fitted()
        assert results.evaluate("element => element.getBoundingClientRect().width > 200")
        page.locator("#show-original-folders").uncheck()
        assert_fitted()
        # Standalone message windows must allocate their entire width to the preview.
        page.goto((GUI_DIRECTORY / "index.html").as_uri() + f"?standalone=1&message={selection[0]}")
        expect(splitter).to_be_hidden()
        expect(preview).to_be_visible()
        assert abs(width("#message-pane") - 800) < 2
    finally:
        api.close()


def test_ingest_history_ui_end_to_end(built_archive: BuiltArchive, page: Page) -> None:
    """Display persisted run history and every configured worker through the real service."""
    api = IngestWindowApi(built_archive.archive)
    page.expose_function("mailarchive_ingest_history", api.history)
    page.add_init_script(
        "window.pywebview = {api: {history: (...args) => "
        "window.mailarchive_ingest_history(...args)}}; "
        "window.addEventListener('DOMContentLoaded', () => "
        "window.dispatchEvent(new Event('pywebviewready')));"
    )

    page.goto((GUI_DIRECTORY / "ingests.html").as_uri())

    page.wait_for_selector(".history-row")
    assert page.locator(".history-row").count() == 1
    assert "Completed" in page.locator(".history-row").inner_text()
    assert page.locator(".worker-table tbody tr").count() == 2
    assert str(PROCESSED_MESSAGE_COUNT) in page.locator(".statistics").inner_text()


def test_about_window_displays_version_disk_and_warnings(tmp_path: Path, page: Page) -> None:
    """Requirement: the persistent About UI surfaces health and application diagnostics."""
    controller = ApplicationController(ApplicationPreferencesStore(tmp_path / "preferences.json"))
    application = PyWebViewApplication(controller)
    first = controller.create_document(tmp_path / "first.mailarchive")
    first_window = controller.new_search_window(first)
    second = controller.create_document(tmp_path / "second.mailarchive")
    controller.new_search_window(second)
    assert application.about_status().disk_path == str(second.path)
    controller.activate_window(first_window.window_id)
    assert application.about_status().disk_path == str(first.path)
    application.add_notice("warning", "Saved archive was ignored because its database was invalid.")
    api = AboutApi(application)
    page.expose_function("mailarchive_about_status", api.status)
    page.add_init_script(
        "window.pywebview = {api: {status: (...args) => window.mailarchive_about_status(...args)}}; "
        "window.addEventListener('DOMContentLoaded', () => "
        "window.dispatchEvent(new Event('pywebviewready')));"
    )

    page.goto((GUI_DIRECTORY / "about.html").as_uri())

    page.wait_for_function("document.getElementById('version').textContent.startsWith('Version ')")
    metadata = application.about_status().metadata
    expect(page.locator("#version")).to_have_text(f"Version {metadata.version}")
    assert metadata.build.git_commit is not None
    expect(page.locator("#git-commit")).to_contain_text(metadata.build.git_commit)
    if metadata.build.dirty:
        expect(page.locator("#git-commit")).to_contain_text("local changes")
    previews = GUI_DIRECTORY.parent / ".tmp/gui-previews"
    previews.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(previews / "about-build.png"), full_page=True)
    assert "available on" in page.locator("#disk").inner_text()
    assert "invalid" in page.locator("#notices").inner_text()
    expect(page.locator("#processors")).to_contain_text("clamav")
    expect(page.locator("#processors")).to_contain_text("text/plain")
    expect(page.get_by_role("button", name="Show processor graph…")).to_be_enabled()
    inventory = application.about_status()
    for processor in inventory.processors:
        expect(page.locator("#processors")).to_contain_text(f"{processor.name} ({processor.kind}) version {processor.implementation_version}")
    for plugin in inventory.acquisition_plugins:
        expect(page.locator("#acquisition-plugins")).to_contain_text(f"{plugin.name} ({plugin.kind}) version {plugin.implementation_version}")
    assert {plugin.plugin_type for plugin in inventory.acquisition_plugins} == {"source", "file"}
    assert first.path is not None
    with closing(connect_processing(first.path, create=True, production=True)) as writer:
        writer.execute("BEGIN EXCLUSIVE")
        expect(page.locator("#plugin-inventory-error")).to_contain_text("archive is busy")
        expect(page.locator("#plugin-inventory-error")).to_be_visible()
        expect(page.locator("#error")).to_be_hidden()
        assert "available on" in page.locator("#disk").inner_text()
        assert "invalid" in page.locator("#notices").inner_text()
        writer.rollback()
    expect(page.locator("#plugin-inventory-error")).to_be_hidden()
    expect(page.locator("#processors")).to_contain_text("clamav")
    expect(page.locator("#acquisition-plugins")).to_contain_text(inventory.acquisition_plugins[0].name)


def test_processor_graph_renders_registry_and_retries_busy_archive(tmp_path: Path, page: Page) -> None:
    """Processing requirement: offline DAGs include custom outputs, safe labels and busy-state retry."""
    from mailarchiver.__main__ import IngestRequest
    controller = ApplicationController(ApplicationPreferencesStore(tmp_path / "preferences.json"))
    document = controller.create_document(tmp_path / "graph.mailarchive")
    assert document.path is not None
    session = controller.new_search_window(document)
    root = tmp_path / "custom"
    plugin = root / "processors/pdf-text"
    plugin.mkdir(parents=True)
    name = "PDF <img src=x onerror=alert(1)>"
    (plugin / "plugin.toml").write_text(
        'api_version=2\nplugin_type="processor"\nkind="pdf-text"\n'
        f'name={json.dumps(name)}\nimplementation_version="1"\nentrypoint="plugin:create_plugin"\n'
        'pipeline="content"\nrank=1\nsubscribes=["application/pdf"]\nemits=["text/plain"]\n', encoding="utf-8")
    (plugin / "plugin.py").write_text("raise AssertionError('Graph must not execute plugin code')\n", encoding="utf-8")
    request = IngestRequest(archive=document.path, plugin_dir=[root])
    with closing(connect_processing(document.path, create=True, production=True)) as database, database:
        database.execute("INSERT OR REPLACE INTO processing_settings VALUES('request',?)", (request.model_dump_json(),))
    application = PyWebViewApplication(controller)
    api = ProcessorGraphApi(application)
    page.expose_function("processor_graph_status", api.status)
    page.add_init_script(
        "window.pywebview = {api: {status: () => window.processor_graph_status()}}; "
        "window.addEventListener('DOMContentLoaded', () => window.dispatchEvent(new Event('pywebviewready')));"
    )
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto((GUI_DIRECTORY / "processor-graph.html").as_uri())
    expect(page.locator("#status")).to_contain_text("11 registered processors")
    expect(page.locator("#context")).to_have_text(str(document.path))
    assert page.locator("svg").count() == 3
    content = page.locator('[data-pipeline="content"]')
    expect(content.locator('[data-node="processor:pdf-text"] title')).to_contain_text(name)
    assert content.locator("img").count() == 0
    assert content.locator('[data-source="processor:mime"][data-target="mime:application/pdf"].dynamic').count() == 1
    assert content.locator('[data-source="mime:application/pdf"][data-target="processor:pdf-text"]').count() == 1
    assert content.locator('[data-source="processor:pdf-text"][data-target="mime:text/plain"]').count() == 1
    assert content.locator('[data-node="other:mime"]').count() == 1
    assert page.evaluate("[...document.querySelectorAll('svg path.edge')].every(path => path.getAttribute('d').startsWith('M ') && !path.getAttribute('d').includes('NaN'))")
    before = content.locator("svg").get_attribute("width")
    page.get_by_role("button", name="100%", exact=True).click()
    natural = content.locator("svg").get_attribute("width")
    assert natural is not None and before is not None and float(natural) >= float(before)
    page.get_by_role("button", name="Zoom in", exact=True).click()
    enlarged = content.locator("svg").get_attribute("width")
    assert enlarged is not None and float(enlarged) > float(natural)
    page.get_by_role("button", name="Fit width", exact=True).click()
    with closing(connect_processing(document.path)) as writer:
        writer.execute("BEGIN EXCLUSIVE")
        page.get_by_role("button", name="Refresh", exact=True).click()
        expect(page.locator("#error")).to_contain_text("archive is busy")
        expect(page.locator("#status")).to_have_text("Showing the previous graph.")
        assert page.locator("svg").count() == 3
        writer.rollback()
    page.get_by_role("button", name="Refresh", exact=True).click()
    expect(page.locator("#error")).to_be_hidden()
    other = controller.create_document(tmp_path / "other.mailarchive")
    controller.new_search_window(other)
    page.get_by_role("button", name="Refresh", exact=True).click()
    expect(page.locator("#context")).to_have_text(str(other.path))
    expect(page.locator("#status")).to_contain_text("10 registered processors")
    assert page.locator('[data-node="processor:pdf-text"]').count() == 0
    controller.activate_window(session.window_id)
    page.get_by_role("button", name="Refresh", exact=True).click()
    expect(page.locator("#status")).to_contain_text("11 registered processors")
    previews = GUI_DIRECTORY.parent / ".tmp/gui-previews"
    previews.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(previews / "processor-graph.png"), full_page=True)
    assert not errors


def test_gui_import_uses_typed_ingest_service_without_a_subprocess(tmp_path: Path) -> None:
    """Requirement: GUI Import publishes searchable mail through the shared typed service."""
    source = tmp_path / "2024"
    source.mkdir()
    fixtures = Path(__file__).parents[1] / "tests/data/writer-preservation/2024"
    for name in ("duplicate-crlf.eml", "duplicate-lf.eml", "invalid-utf8.eml", "broken-mime.eml"):
        shutil.copyfile(fixtures / name, source / name)
    before = inventory(source)
    owner_names = tmp_path / "owner-names.txt"
    owner_names.write_text("sender@example.test\n", encoding="utf-8")
    controller = ApplicationController(ApplicationPreferencesStore(tmp_path / "preferences.json"))
    document = controller.create_document(tmp_path / "GUI.mailarchive")
    session = controller.new_search_window(document)
    application = PyWebViewApplication(controller)
    api = GuiApi(
        document.path,
        temporary_directory=tmp_path / "exports",
        application=application,
        document=document,
        search_window=session,
    )
    try:
        # Storage acceptance is unscanned; real ClamAV routing has its own corpus.
        assert application.start_import(api, [source], owner_names, scan_policy="not-scanned")
        deadline = time.monotonic() + 90
        while document.ingest_job is not None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert document.ingest_job is None
        assert document.path is not None
        database = sqlite3.connect(document.path / "archive.sqlite3")
        try:
            assert database.execute("SELECT COUNT(*) FROM messages").fetchone() == (4,)
            assert database.execute(
                "SELECT COUNT(*) FROM messages WHERE message_id_normalized = ?",
                ("duplicate@example.test",),
            ).fetchone() == (2,)
        finally:
            database.close()
        # Requirements: every fixture is searchable/readable and exports original bytes.
        found = SearchPage.model_validate(api.search("from:sender@example.test"))
        assert found.error is None and not found.has_more and len(found.results) == 4
        for name, subject, body in (
            ("duplicate-crlf.eml", "CRLF", "unterminated"),
            ("duplicate-lf.eml", "Different content", "From body"),
            ("invalid-utf8.eml", "Invalid UTF8", None),
            ("broken-mime.eml", "Broken MIME", "Retained malformed mail"),
        ):
            result = SearchPage.model_validate(api.search(f'subject:"{subject}"'))
            assert result.error is None and not result.has_more and len(result.results) == 1
            view = MessageView.model_validate(api.message(result.results[0].message_pk))
            assert view.subject == subject and view.date_source == "path-year"
            part = PartContent.model_validate(api.part(view.message_pk, view.preferred_part_id))
            assert part.content and part.kind == ("raw" if name == "broken-mime.eml" else "text")
            if body is not None:
                assert body in part.content
            exported = tmp_path / f"exported-{name}"
            write_message(document.path, view.message_pk, exported)
            assert exported.read_bytes() == (fixtures / name).read_bytes()
        verified = subprocess.run(
            [sys.executable, "-I", str(document.path / INSTALLED_NAME), str(document.path)],
            check=False, capture_output=True, text=True, timeout=30,
        )
        assert verified.returncode == 0, verified.stdout + verified.stderr
        assert inventory(source) == before
        status = application.about_status()
        assert status.ingests[0].status is not None
        assert status.ingests[0].status.state == "completed"
        assert status.notices[-1].message.startswith("Import completed")
    finally:
        api.close()


def test_native_smoke_page_reports_one_real_search(
    native_smoke_archive: Path, tmp_path: Path, page: Page
) -> None:
    """Requirement: the one-shot native page reports its real bridge search to Python."""
    report_path = tmp_path / "native-smoke.json"
    controller = NativeSmokeController(report_path)
    api = GuiApi(native_smoke_archive)
    bridge = NativeSmokeApi(api, controller)
    methods = {
        name for name in dir(bridge)
        if not name.startswith("_") and callable(getattr(bridge, name))
    }
    assert methods == set(NATIVE_SMOKE_API_METHODS)
    javascript_errors: list[str] = []
    page.on("pageerror", lambda error: javascript_errors.append(str(error)))
    try:
        for name in NATIVE_SMOKE_API_METHODS:
            page.expose_function(f"mailarchive_{name}", getattr(bridge, name))
        names = json.dumps(NATIVE_SMOKE_API_METHODS)
        page.add_init_script(
            f"const names = {names}; window.pywebview = {{api: {{}}}}; "
            "for (const name of names) window.pywebview.api[name] = "
            "(...args) => window[`mailarchive_${name}`](...args); "
            "window.addEventListener('DOMContentLoaded', () => "
            "window.dispatchEvent(new Event('pywebviewready')));"
        )
        page.goto((GUI_DIRECTORY / "index.html").as_uri() + "?native-smoke=1")
        page.wait_for_function("window.__mailarchiveNativeSmoke", timeout=5_000)
        assert controller.wait_for_result(0), "\n".join(javascript_errors) or "native smoke callback did not run"
    finally:
        api.close()

    report = NativeSmokeReport.model_validate_json(report_path.read_text(encoding="utf-8"))
    assert report.passed
    assert [phase.name for phase in report.phases][-1] == "bridge-passed"


def test_native_smoke_report_keeps_the_primary_failure(tmp_path: Path) -> None:
    """Requirement: shutdown diagnostics must not replace the bridge failure."""
    report_path = tmp_path / "native-smoke.json"
    controller = NativeSmokeController(report_path)
    controller.complete(False, "bridge timeout")
    controller._abort("Cocoa shutdown timeout")  # pylint: disable=protected-access

    report = NativeSmokeReport.model_validate_json(report_path.read_text(encoding="utf-8"))
    assert report.error == "bridge timeout"
    assert [phase.name for phase in report.phases][-1] == "watchdog-abort"


def terminate_process_group(process: subprocess.Popen[str]) -> tuple[str, str]:
    """Stop a timed-out smoke subprocess and collect its buffered output."""
    try:
        if sys.platform == "win32":
            process.terminate()
        else:
            os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        return process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            if sys.platform == "win32":
                process.kill()
            else:
                os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        return process.communicate()


def sample_process(process: subprocess.Popen[str], destination: Path) -> None:
    """Capture a macOS stack sample before terminating an unbounded native process."""
    sampler = Path("/usr/bin/sample")
    if not sampler.is_file():
        return
    try:
        sampled = subprocess.run(
            [str(sampler), str(process.pid), "5", "-file", str(destination)],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired as error:
        destination.write_text(f"process sampler timed out: {error}\n", encoding="utf-8")
        return
    if sampled.returncode and not destination.exists():
        destination.write_text(sampled.stdout + sampled.stderr, encoding="utf-8")


def run_native_smoke(native_smoke_archive: Path, tmp_path: Path, *, html_find: bool = False) -> None:
    """Run a bounded Cocoa/WKWebView smoke subprocess and retain its diagnostics."""
    configured_artifacts = os.environ.get("MAILARCHIVER_NATIVE_GUI_ARTIFACT_DIR")
    artifacts = Path(configured_artifacts) if configured_artifacts else tmp_path
    artifacts.mkdir(parents=True, exist_ok=True)
    name = "native-html-find-smoke" if html_find else "native-smoke"
    report_path = artifacts / f"{name}-report.json"
    sample_path = artifacts / f"{name}-sample.txt"
    report_path.unlink(missing_ok=True)
    sample_path.unlink(missing_ok=True)
    command = [
        sys.executable,
        "-m",
        "mailarchiver.gui_app",
        "--archive",
        str(native_smoke_archive),
        "--smoke-test",
    ]
    if html_find:
        command.append("--smoke-html-find")
    command.extend(("--smoke-report", str(report_path)))
    tested = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    timed_out = False
    try:
        stdout, stderr = tested.communicate(timeout=40)
    except subprocess.TimeoutExpired:
        timed_out = True
        sample_process(tested, sample_path)
        stdout, stderr = terminate_process_group(tested)

    report_exists = report_path.is_file()
    report_text = report_path.read_text(encoding="utf-8") if report_exists else "missing smoke report"
    detail = stdout + stderr + "\n" + report_text
    assert not timed_out, detail
    assert report_exists, detail
    assert tested.returncode == 0, detail
    assert stdout.endswith("GUI bridge smoke test passed\n")
    report = NativeSmokeReport.model_validate_json(report_text)
    assert report.completed and report.passed and report.error is None, detail
    assert {"window-loaded", "bridge-passed", "destroy-requested", "event-loop-returned"} <= {
        phase.name for phase in report.phases
    }


@pytest.mark.skipif(
    sys.platform != "darwin" or os.environ.get("MAILARCHIVER_NATIVE_GUI_E2E") != "1",
    reason="set MAILARCHIVER_NATIVE_GUI_E2E=1 to exercise the native macOS WKWebView",
)
def test_native_search_ui_smoke(native_smoke_archive: Path, tmp_path: Path) -> None:
    """Exercise one bounded real search through a hidden pywebview bridge process."""
    run_native_smoke(native_smoke_archive, tmp_path)


@pytest.mark.skipif(
    sys.platform != "darwin" or os.environ.get("MAILARCHIVER_NATIVE_APPLICATION_E2E") != "1",
    reason="set MAILARCHIVER_NATIVE_APPLICATION_E2E=1 for production Cocoa lifecycle checks",
)
def test_native_application_lifecycle(native_smoke_archive: Path, tmp_path: Path) -> None:
    """About must populate through its real bridge; extensionless Open and menus must work."""
    from scripts.run_gui import development_bundle, development_environment
    executable = development_bundle(tmp_path / "launcher")
    try:
        result = subprocess.run(
            [str(executable), str(Path(__file__).with_name("native_application_probe.py"))],
            # Cocoa treats positional filesystem paths as document-open requests.
            # Pass fixtures out of band so the probe can first test About alone.
            env=development_environment() | {
                PROBE_ARCHIVE_ENV: str(native_smoke_archive),
                PROBE_PREFERENCES_ENV: str(tmp_path / "preferences.json"),
            },
            capture_output=True, text=True, timeout=40, check=False,
        )
    except subprocess.TimeoutExpired as error:
        pytest.fail(f"Native lifecycle timed out; captured stderr: {error.stderr!r}")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Production About, document bridge, and menus passed" in result.stdout


@pytest.mark.skipif(
    sys.platform != "darwin" or os.environ.get("MAILARCHIVER_NATIVE_HTML_FIND_E2E") != "1",
    reason="set MAILARCHIVER_NATIVE_HTML_FIND_E2E=1 for the visible macOS HTML-find check",
)
def test_native_html_find_smoke(native_smoke_archive: Path, tmp_path: Path) -> None:
    """Requirement: WKWebView paints the active HTML finder match after a header transition."""
    run_native_smoke(native_smoke_archive, tmp_path, html_find=True)


@pytest.mark.skipif(
    sys.platform != "darwin" or os.environ.get("MAILARCHIVER_NATIVE_APPLICATION_E2E") != "1",
    reason="set MAILARCHIVER_NATIVE_APPLICATION_E2E=1 for real curator menu acceptance",
)
def test_native_curator_reprocessing(tmp_path: Path) -> None:
    """Real menu Cancel preserves work; confirmation replays canonical mail without downloading."""
    from scripts.run_gui import development_bundle, development_environment
    source = tmp_path / "fixture.eml"
    source.write_bytes(NATIVE_SMOKE_MESSAGE)
    archive = tmp_path / "fixture.mailarchive"
    run_ingest(IngestRequest(archive=archive, roots=[str(source)], scan_policy="not-scanned",
                            owner_rules=OwnerRules(include=["owner@example.test"])), terminal=False)
    source.unlink()
    executable = development_bundle(tmp_path / "launcher")
    result = subprocess.run(
        [str(executable), str(Path(__file__).with_name("native_application_probe.py")), "--reprocess-only"],
        env=development_environment() | {PROBE_ARCHIVE_ENV: str(archive),
                                        PROBE_PREFERENCES_ENV: str(tmp_path / "preferences.json")},
        capture_output=True, text=True, timeout=40, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Curator reprocessing menu passed" in result.stdout
