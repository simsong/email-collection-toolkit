# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Exercise saved failure diagnostics through the actual Ingests browser interface.
# Synthetic imports and snapshots provide source context and version attribution.
# Historical versions must survive viewing under a different application version.
# Legacy snapshots remain readable without inventing missing producer metadata.
# Assertions inspect visible text and ensure history reads never rewrite evidence.
# No test accesses personal mail, credentials or a real canonical archive.

"""Requirement: local failure evidence is readable in the real Ingests interface."""

from datetime import UTC, datetime
from pathlib import Path
import json

import pytest
from playwright.sync_api import Page, expect

from mailarchiver.__main__ import IngestRequest, ProgressReporter, ProgressUpdate, run_ingest
from mailarchiver.gui_app import GUI_DIRECTORY, IngestWindowApi
from mailarchiver.ingest_status import IngestStatusFile, read_ingest_history
from mailarchiver.identity import APPLICATION_VERSION
from scripts.website_screenshots import bridge

APPLICATION_VERSION_FIELD = "application_version"
DOWNLOAD_COVERAGE_FIELD = "download_coverage"
ETA_FIELD = "eta"
WIDTH = "width"
HEIGHT = "height"


@pytest.mark.parametrize("recorded_version", ["0.0.0+synthetic-history", None])
def test_failed_run_shows_recorded_version_or_unavailable(tmp_path: Path, page: Page, recorded_version: str | None) -> None:
    """DESKTOP: failures show their producer's version; legacy files never inherit the viewer's."""
    archive = tmp_path / "synthetic.mailarchive"
    saved = IngestStatusFile(archive, "ingest-version-fixture")
    progress = ProgressReporter(1, status_file=saved, archive=archive, run_pk=1, terminal=False)
    progress.finish("failed", "Synthetic failure retained for version attribution")
    payload = json.loads(saved.path.read_text())
    if recorded_version is None:
        del payload[APPLICATION_VERSION_FIELD]
    else:
        payload[APPLICATION_VERSION_FIELD] = recorded_version
    saved.path.write_text(json.dumps(payload))
    original = saved.path.read_bytes()
    bridge(page, IngestWindowApi(archive), ("history", "antivirus", "can_import_directory"))
    page.goto((GUI_DIRECTORY / "ingests.html").as_uri())
    label = f"Version {recorded_version}" if recorded_version else "Version unavailable"
    expect(page.locator(".history-version")).to_have_text(label)
    expect(page.locator(".detail-heading")).to_contain_text(label)
    expect(page.locator("div.failure")).to_contain_text("Synthetic failure retained")
    assert saved.path.read_bytes() == original
    page.screenshot(path=str(tmp_path / "failed-run-version.png"), full_page=True)


def test_invalid_configuration_shows_warning_and_saved_history(tmp_path: Path, page: Page) -> None:
    """DESKTOP: malformed account settings show actionable warning text while history stays usable."""
    archive = tmp_path / "synthetic.mailarchive"
    saved = IngestStatusFile(archive, "ingest-config-fixture")
    progress = ProgressReporter(1, status_file=saved, archive=archive, run_pk=1,
                                source_roots=["imap://synthetic-account?archive=fixture"], terminal=False)
    progress.finish("completed")
    (archive / "config.yaml").write_text("imap_sources: PRIVATE_CONFIGURATION_VALUE\n")
    original = saved.path.read_bytes()
    bridge(page, IngestWindowApi(archive), ("history", "antivirus", "can_import_directory"))
    page.goto((GUI_DIRECTORY / "ingests.html").as_uri())
    warning = page.locator("#configuration-warning")
    expect(warning).to_be_visible()
    expect(warning).to_contain_text("imap_sources: expected a list")
    expect(warning).to_contain_text("Repair config.yaml")
    expect(page.locator(".state-badge")).to_have_text("completed")
    expect(page.locator("#error")).not_to_be_visible()
    assert "PRIVATE_CONFIGURATION_VALUE" not in page.locator("body").inner_text()
    assert saved.path.read_bytes() == original
    page.screenshot(path=str(tmp_path / "configuration-warning.png"), full_page=True)


def test_saved_timeout_shows_yellow_dash_without_traceback(tmp_path: Path, page: Page) -> None:
    """Desktop: older recorded IMAP timeouts use the same quiet display without rewriting history."""
    archive = tmp_path / "synthetic.mailarchive"
    saved = IngestStatusFile(archive, "ingest-legacy-timeout")
    progress = ProgressReporter(1, status_file=saved, archive=archive, run_pk=1,
        source_roots=["imap://synthetic-account?archive=fixture"], terminal=False)
    progress.finish("failed", "TimeoutError: The read operation timed out\n\nTraceback (most recent call last):\n"
                    '  File "/fixture/imap_protocol.py", line 331, in body\nsynthetic stack')
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
    assert read_ingest_history(archive).statuses[0].application_version == APPLICATION_VERSION
    page.set_viewport_size({WIDTH: 1000, HEIGHT: 900})
    bridge(page, IngestWindowApi(archive), ("history", "antivirus", "can_import_directory"))
    page.goto((GUI_DIRECTORY / "ingests.html").as_uri())
    failure = page.locator("div.failure")
    expect(page.locator(".history-version")).to_have_text(f"Version {APPLICATION_VERSION}")
    expect(failure).to_contain_text("Traceback (most recent call last)")
    expect(failure).to_contain_text("Message SHA-256:")
    expect(failure).to_contain_text(str(source))
    expect(failure).to_contain_text("<script>alert(1)</script>")
    assert failure.locator("script").count() == 0
    assert page.locator(".ingest-detail").evaluate("el => el.scrollWidth <= el.clientWidth + 1")
    failure.scroll_into_view_if_needed()
    page.screenshot(path=str(tmp_path / "import-failure.png"))


@pytest.mark.parametrize("legacy", [False, True])
def test_imap_coverage_and_unknown_eta(tmp_path: Path, page: Page, legacy: bool) -> None:
    """DESKTOP: unknown-size IMAP work has honest ETA and visible run-specific date/year counts."""
    archive = tmp_path / "synthetic.mailarchive"
    saved = IngestStatusFile(archive, "ingest-coverage-fixture")
    progress = ProgressReporter(1, status_file=saved, archive=archive, run_pk=1,
        source_roots=["imap://synthetic-account?archive=fixture"], terminal=False)
    progress.state.source_files_total = 16
    progress.state.files_processed = 6
    progress.state.inventory_complete = True
    for year in (2020, 2020, 2024):
        progress.updates.put(ProgressUpdate(worker=1, phase="ingesting", download_date=datetime(year, 6, 15, tzinfo=UTC)))
    progress.updates.put(ProgressUpdate(worker=1, message_date=datetime(1990, 1, 1, tzinfo=UTC)))
    progress.refresh()
    if legacy:
        payload = json.loads(saved.path.read_text())
        del payload[DOWNLOAD_COVERAGE_FIELD]
        payload[ETA_FIELD] = "finalizing"
        saved.path.write_text(json.dumps(payload))
    page.set_viewport_size({WIDTH: 1200, HEIGHT: 1000})
    bridge(page, IngestWindowApi(archive), ("history", "antivirus", "can_import_directory"))
    page.goto((GUI_DIRECTORY / "ingests.html").as_uri())
    expect(page.locator(".progress-caption")).to_contain_text("ETA unavailable")
    coverage = page.locator(".message-coverage")
    if legacy:
        expect(coverage).to_contain_text("Download coverage unavailable for this older run.")
    else:
        expect(page.locator(".history-summary")).to_contain_text("3 source messages")
        expect(coverage).to_contain_text("This run: 3 messages")
        expect(coverage).to_contain_text("Earliest: 2020-06-15")
        expect(coverage).to_contain_text("Latest: 2024-06-15")
        expect(coverage.locator("tbody tr")).to_have_text(["20202", "20241"])
    page.screenshot(path=str(tmp_path / "imap-message-coverage.png"), full_page=True)
    expect(page.locator("body")).not_to_contain_text("ETA finalizing")
