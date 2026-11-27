# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Show Rebuild Databases runs in the real Activity window from their status files.
# A real rebuild of a synthetic fixture archive writes the status this window reads;
# a running snapshot is then written the same way to check live byte progress.
# Assertions inspect visible text only; no personal mail or real archive is used.
"""Requirement: rebuilding appears in the Activity window as its own run with progress."""

from pathlib import Path

from playwright.sync_api import Page, expect

from e2e_tests.test_gui_processing import expose
from mailarchiver.gui_app import GUI_DIRECTORY, SEARCH_BRIDGE_METHODS, GuiApi, IngestWindowApi
from mailarchiver.ingest_status import read_ingest_history
from mailarchiver.rebuild import RebuildActivity, RebuildProgress, rebuild_archive
from scripts.website_screenshots import bridge
from tests.test_gui_service import make_gui_archive


def test_activity_window_lists_completed_and_running_rebuild_runs(tmp_path: Path, page: Page) -> None:
    """DESKTOP: a finished run shows its message count; a running one shows bytes and ETA."""
    archive = make_gui_archive(tmp_path)
    rebuild_archive(archive, workers=1)
    running = RebuildActivity(archive, 4, "test")
    running.messages = 1_234
    running.update(RebuildProgress(phase="Rebuilding databases (4 workers)", total_bytes=4 * 1024**3,
                                     completed_bytes=1024**3))
    assert [status.state for status in read_ingest_history(archive).statuses] == ["running", "completed"]
    bridge(page, IngestWindowApi(archive), ("history", "antivirus"))
    page.goto((GUI_DIRECTORY / "ingests.html").as_uri())
    expect(page.locator("#import-directory")).to_have_count(0)  # Imports start from File > Import Mail….
    expect(page.locator(".ingest-actions button:visible")).to_have_text(["Suspend", "Continue"])
    detail = page.locator("#ingest-detail")
    expect(detail.locator("h1")).to_have_text("Rebuild databases")
    expect(detail.locator(".progress-caption")).to_contain_text("25.0% · 1.0 GiB of 4.0 GiB · ETA")
    expect(detail).to_contain_text("1,234")
    rows = page.locator(".history-row")
    expect(rows).to_have_count(2)
    rows.nth(1).click()
    expect(detail.locator(".state-badge")).to_have_text("completed")
    expect(detail).to_contain_text("Rebuilt from 2 messages")
    expect(rows.nth(1)).to_contain_text("Rebuild databases · 2 messages")


def test_search_window_status_line_reports_the_last_rebuild(tmp_path: Path, page: Page) -> None:
    """DESKTOP: after a rebuild the search window names it, not a source ingest."""
    archive = make_gui_archive(tmp_path)
    rebuild_archive(archive, workers=1)
    expose(page, GuiApi(archive), SEARCH_BRIDGE_METHODS)
    page.goto((GUI_DIRECTORY / "index.html").as_uri())
    expect(page.locator("#ingest-status-line")).to_contain_text("Last rebuild completed · 2 messages")
