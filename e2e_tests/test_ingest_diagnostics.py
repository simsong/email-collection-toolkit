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
from mailarchiver.sources import SourceInventory
from scripts.website_screenshots import bridge

APPLICATION_VERSION_FIELD = "application_version"
DOWNLOAD_COVERAGE_FIELD = "download_coverage"
ETA_FIELD = "eta"
WIDTH = "width"
HEIGHT = "height"


@pytest.mark.parametrize("method", ["suspend_import", "resume_import"])
def test_lost_control_reply_is_visible_and_never_replays_the_command(tmp_path: Path, page: Page, method: str) -> None:
    """DESKTOP: a missing transport reply releases the controls without issuing another command."""
    archive = tmp_path / "synthetic.mailarchive"
    saved = IngestStatusFile(archive, "ingest-reply-fixture")
    progress = ProgressReporter(1, status_file=saved, archive=archive, run_pk=1, terminal=False)
    progress.finish("interrupted")
    bridge(page, IngestWindowApi(archive), ("history", "antivirus", "can_import_directory"))
    page.clock.install()
    page.goto((GUI_DIRECTORY / "ingests.html").as_uri())
    expect(page.locator(".detail-heading")).to_contain_text("Ingest")
    # Inject only a missing reply: the desktop API itself is outside this browser's transport.
    page.evaluate("""method => {
      window.controlCalls = 0;
      window.pywebview.api[method] = () => {
        window.controlCalls++;
        return new Promise(resolve => { window.lateControlReply = resolve; });
      };
      void controlImport(method);
    }""", method)
    page.wait_for_function("window.controlCalls === 1")
    page.clock.fast_forward(15001)
    expect(page.locator("#error")).to_contain_text("No confirmation from the application")
    expect(page.locator("#error")).to_contain_text("before repeating the command")
    page.wait_for_function("!importing && historyRefresh.pending === null")
    page.screenshot(path=str(tmp_path / "unconfirmed-control.png"), full_page=True)
    page.evaluate("window.lateControlReply(true)")
    assert page.evaluate("window.controlCalls") == 1
    expect(page.locator(".state-badge")).to_have_text("interrupted")


def test_lost_history_reply_releases_refresh_for_a_fresh_read(tmp_path: Path, page: Page) -> None:
    """DESKTOP: an abandoned status reply cannot retain RefreshTask ownership forever."""
    saved = IngestStatusFile(tmp_path, "ingest-status-reply")
    progress = ProgressReporter(1, status_file=saved, archive=tmp_path, run_pk=1, terminal=False)
    progress.finish("completed")
    bridge(page, IngestWindowApi(tmp_path), ("history", "antivirus", "can_import_directory"))
    page.clock.install()
    page.goto((GUI_DIRECTORY / "ingests.html").as_uri())
    expect(page.locator(".state-badge")).to_have_text("completed")

    page.evaluate("""() => {
      window.savedHistory = window.pywebview.api.history;
      window.pywebview.api.history = () => { window.statusReadStarted = true; return new Promise(() => {}); };
      void refreshHistory();
    }""")
    page.wait_for_function("window.statusReadStarted")
    page.clock.fast_forward(15001)
    expect(page.locator("#error")).to_contain_text("No confirmation from the application")
    page.wait_for_function("historyRefresh.pending === null")
    page.evaluate("""async () => {
      window.pywebview.api.history = window.savedHistory;
      await refreshHistory();
    }""")
    expect(page.locator(".state-badge")).to_have_text("completed")


def test_concurrent_download_and_processing_have_separate_rows(tmp_path: Path, page: Page) -> None:
    """DESKTOP: mixed activity retains source ETA/counts and distinct processor lanes."""
    saved = IngestStatusFile(tmp_path, "ingest-concurrent-fixture")
    progress = ProgressReporter(1, status_file=saved, archive=tmp_path, run_pk=1,
        source_roots=["imap://synthetic-account?archive=fixture"], terminal=False)
    progress.finish_inventory(SourceInventory(file_count=1, byte_count=0))
    progress.begin_concurrent_processing()
    progress._queue_update(ProgressUpdate(worker=1, phase="downloading", path="Synthetic IMAP mailbox",
        operation="downloading", download_date=datetime(2024, 1, 2, tzinfo=UTC)))
    progress._queue_update(ProgressUpdate(worker=1, saved_pool=True, phase="processing mime", path="content job 7",
        processing_active=True, processing_job_id=7, processing_processor="mime", activity_unit="jobs"))
    progress.refresh()
    snapshot = read_ingest_history(tmp_path).statuses[0]
    assert snapshot.workers[0].operation == "downloading"
    assert snapshot.saved_workers[0].operation == "processing"
    assert snapshot.saved_workers[0].processing_jobs_completed == 0
    assert snapshot.eta == "unavailable"
    bridge(page, IngestWindowApi(tmp_path), ("history", "antivirus", "can_import_directory"))
    page.goto((GUI_DIRECTORY / "ingests.html").as_uri())
    expect(page.locator(".processing-explanation")).to_have_text("Source work and saved message processing are running together.")
    tables = page.locator(".worker-table")
    expect(tables).to_have_count(2)
    expect(tables.nth(0)).to_contain_text("Downloading")
    expect(tables.nth(1)).to_contain_text("Processing saved mail")
    expect(page.locator(".progress-caption")).to_contain_text("ETA unavailable")
    tables.nth(1).scroll_into_view_if_needed()
    page.screenshot(path=str(tmp_path / "concurrent-imap-processing.png"), full_page=True)
    progress._queue_update(ProgressUpdate(worker=1, saved_pool=True, phase="idle", processing_active=False))
    progress.refresh()
    page.evaluate("window.refreshHistory()")
    expect(page.locator(".processing-explanation")).to_have_count(0)
    expect(page.locator(".progress-caption")).to_contain_text("ETA unavailable")
    progress._queue_update(ProgressUpdate(worker=1, phase="idle", file_complete=True))
    progress._queue_update(ProgressUpdate(worker=1, saved_pool=True, phase="processing mime", path="content job 8",
        processing_active=True, processing_job_id=8, processing_processor="mime", activity_unit="jobs"))
    progress.refresh()
    page.evaluate("window.refreshHistory()")
    expect(page.locator(".progress-caption")).to_contain_text("Processing saved mail")
    expect(page.locator(".processing-explanation")).not_to_contain_text("resumes after")


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
