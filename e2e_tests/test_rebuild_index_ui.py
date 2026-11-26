# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Exercise the search window's bottom status line during File → Rebuild Search Index.
# The real GUI and Python bridge load a fixture archive; the overview the bridge
# returns is then extended with an in-progress rebuild, as the application reports
# it, to check that byte progress (percent, bytes done/total, ETA) is what the user sees.
"""Requirement: a running search-index rebuild shows byte progress in the status line."""

from pathlib import Path

from playwright.sync_api import Page, expect

from e2e_tests.test_gui_processing import expose
from mailarchiver.gui_app import GUI_DIRECTORY, SEARCH_BRIDGE_METHODS, GuiApi, IndexRebuildStatus
from tests.test_gui_service import make_gui_archive


def test_status_line_shows_rebuild_byte_progress(tmp_path: Path, page: Page) -> None:
    """The status line replaces ingest history with rebuild phase, percent, bytes and ETA."""
    api = GuiApi(make_gui_archive(tmp_path))
    expose(page, api, SEARCH_BRIDGE_METHODS)
    page.goto((GUI_DIRECTORY / "index.html").as_uri())
    line = page.locator("#ingest-status-line")
    expect(line).to_have_text("No ingest history · Click to open Activity")
    rebuild = IndexRebuildStatus(phase="Rebuilding search index (4 workers)", percent=42.0,
                                 completed_bytes=16 * 1024**3, total_bytes=37 * 1024**3, eta="1h 5m")
    page.evaluate("""rebuild => {
        const overview = window.pywebview.api.ingest_overview;
        window.pywebview.api.ingest_overview = async () => ({...(await overview()), index_rebuild: rebuild});
        return refreshIngestOverview();
    }""", rebuild.model_dump(mode="json"))
    expect(line).to_have_text("Rebuilding search index (4 workers) · 42.0% · 16.0 GiB of 37.0 GiB · ETA 1h 5m")
