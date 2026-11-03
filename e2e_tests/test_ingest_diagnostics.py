# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Requirement: local failure evidence is readable in the real Ingests interface."""

from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from mailarchiver.__main__ import IngestRequest, ProgressReporter, run_ingest
from mailarchiver.gui_app import GUI_DIRECTORY, IngestWindowApi
from mailarchiver.ingest_status import IngestStatusFile
from scripts.website_screenshots import bridge


def test_saved_timeout_shows_yellow_dash_without_traceback(tmp_path: Path, page: Page) -> None:
    """Desktop: older recorded IMAP timeouts use the same quiet display without rewriting history."""
    archive = tmp_path / "synthetic.mailarchive"
    saved = IngestStatusFile(archive, "ingest-legacy-timeout")
    progress = ProgressReporter(1, status_file=saved, archive=archive, run_pk=1,
        source_roots=["imap://synthetic-account?archive=fixture"], terminal=False)
    progress.finish("failed", "TimeoutError: The read operation timed out\n\nTraceback (most recent call last):\nsynthetic stack")
    original = saved.path.read_bytes()
    bridge(page, IngestWindowApi(archive), ("history", "antivirus", "can_import_directory"))
    page.goto((GUI_DIRECTORY / "ingests.html").as_uri())
    expect(page.locator(".state-badge")).to_have_text("Timeout")
    expect(page.locator(".state-dot").last).to_have_text("−")
    assert page.locator(".state-dot").last.evaluate("el => getComputedStyle(el).backgroundColor") == "rgb(244, 197, 66)"
    expect(page.get_by_role("heading", name="Failure", exact=True)).to_have_count(0)
    expect(page.locator(".progress-caption")).to_contain_text("Timeout. Choose Resume when connected.")
    expect(page.locator("body")).not_to_contain_text("Traceback")
    expect(page.locator("body")).not_to_contain_text("ETA finalizing")
    assert saved.path.read_bytes() == original
    page.screenshot(path=str(tmp_path / "quiet-timeout.png"))


def test_long_failure_detail_wraps_without_hiding_source_or_traceback(tmp_path: Path, page: Page) -> None:
    """A real failed import displays escaped mail as text and keeps diagnostics inside the pane."""
    source = tmp_path / "undated.eml"
    source.write_bytes(b"Message-ID: <undated@example.test>\n\n<script>alert(1)</script>" + b"x" * 5000)
    owners = tmp_path / "owners.txt"
    owners.write_text("owner@example.test\n", encoding="utf-8")
    archive = tmp_path / "archive"
    with pytest.raises(RuntimeError, match="failed to parse"):
        run_ingest(IngestRequest(
            archive=archive, roots=[str(source)], owner_names_file=owners, scan_policy="not-scanned",
        ), terminal=False)
    page.set_viewport_size({"width": 1000, "height": 900})
    bridge(page, IngestWindowApi(archive), ("history", "antivirus", "can_import_directory"))
    page.goto((GUI_DIRECTORY / "ingests.html").as_uri())
    failure = page.locator("div.failure")
    expect(failure).to_contain_text("Traceback (most recent call last)")
    expect(failure).to_contain_text("Message SHA-256:")
    expect(failure).to_contain_text(str(source))
    expect(failure).to_contain_text("<script>alert(1)</script>")
    assert failure.locator("script").count() == 0
    assert page.locator(".ingest-detail").evaluate("el => el.scrollWidth <= el.clientWidth + 1")
    failure.scroll_into_view_if_needed()
    page.screenshot(path=str(tmp_path / "import-failure.png"))
