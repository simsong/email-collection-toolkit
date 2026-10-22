# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Verify the pre-scan definition refresh contract without contacting a public CDN.
# Synthetic CVD headers establish the authoritative publication age and version.
# A local HTTP server exercises reachable, refused and offline network checks.
# Only the external updater boundary is substituted for deterministic outcomes.
# Real scanner tests separately validate native loading and clean/EICAR verdicts.
# Cancellation tests exercise the owned subprocess supervisor rather than a mock.

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
import os
import socket
import sys

import pytest

from mailarchiver import clamav_definitions, clamav_update
from mailarchiver.clamav_definitions import ActiveDefinitions, DATABASE_ENV, DefinitionSet, UPDATE_ENV, read_definitions, selected_definitions
from mailarchiver.clamav_update import UpdateResult, definitions_for_scan, internet_available
from mailarchiver.owned_command import run_owned_command
from mailarchiver.scanner import ClamScanner, ClamScannerStartupError

NOW = datetime(2026, 10, 22, 16, tzinfo=UTC)


def definitions_at(directory: Path, published: datetime, version: int = 12) -> DefinitionSet:
    directory.mkdir(parents=True)
    for name in clamav_definitions.DATABASE_NAMES:
        header = f"ClamAV-VDB:fixture:{version}:1:90:unused:unused:fixture:{int(published.timestamp())}"
        (directory / f"{name}.cvd").write_bytes(header.encode().ljust(512, b" "))
    return read_definitions(directory, "development")


@contextmanager
def connectivity_site(status: int) -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        def do_HEAD(self) -> None:
            if status == 0:
                self.wfile.write(b"invalid HTTP status\r\n\r\n")
                return
            self.send_response(status)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.mark.parametrize("status", [200, 403])
def test_connectivity_accepts_a_reachable_update_site(monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    """SCANNING: an HTTP refusal is online; only FreshClam decides download policy."""
    with connectivity_site(status) as url:
        monkeypatch.setattr(clamav_update, "DEFINITION_CHECK_URL", url)
        assert internet_available()


def test_connectivity_is_offline_when_the_site_cannot_be_reached(monkeypatch: pytest.MonkeyPatch) -> None:
    """SCANNING: a failed network probe must permit use of existing definitions."""
    with socket.socket() as unused:
        unused.bind(("127.0.0.1", 0))
        monkeypatch.setattr(clamav_update, "DEFINITION_CHECK_URL", f"http://127.0.0.1:{unused.getsockname()[1]}/")
        assert not internet_available()


def test_malformed_probe_response_retains_definitions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """SCANNING: malformed CDN/proxy HTTP must not prevent scanning with the baseline."""
    definitions = definitions_at(tmp_path / "db", NOW - timedelta(days=2))
    monkeypatch.delenv(DATABASE_ENV, raising=False)
    monkeypatch.setenv(UPDATE_ENV, str(tmp_path / "updates"))
    with connectivity_site(0) as url:
        monkeypatch.setattr(clamav_update, "DEFINITION_CHECK_URL", url)
        assert not internet_available()
        assert definitions_for_scan(definitions, now=NOW) is definitions
    assert not (tmp_path / "updates").exists()


@pytest.mark.parametrize("age", [timedelta(hours=23), timedelta(days=1), timedelta(days=-1)])
def test_recent_definitions_do_not_check_the_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, age: timedelta,
) -> None:
    """SCANNING: refresh begins only when daily publication is strictly over 24 hours old."""
    monkeypatch.delenv(DATABASE_ENV, raising=False)
    definitions = definitions_at(tmp_path / "db", NOW - age)
    def unexpected_network() -> bool:
        pytest.fail("recent definitions must not contact the network")
    monkeypatch.setattr(clamav_update, "internet_available", unexpected_network)
    assert definitions_for_scan(definitions, now=NOW) is definitions


@pytest.mark.parametrize("outcome", ["offline", "updated", "failed", "locked"])
def test_stale_definitions_refresh_online_and_preserve_the_baseline_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str,
) -> None:
    """SCANNING: use activated updates, but offline/update/lock failures cannot block scans."""
    baseline = definitions_at(tmp_path / "baseline", NOW - timedelta(days=2))
    root = tmp_path / "updates"
    monkeypatch.delenv(DATABASE_ENV, raising=False)
    monkeypatch.setenv(UPDATE_ENV, str(root))
    monkeypatch.setattr(clamav_definitions, "DEVELOPMENT_DATABASE", baseline.directory)
    refreshed: list[bool] = []
    callbacks: list[bool] = []
    def status_callback() -> None:
        callbacks.append(True)
    def updater(*, status_callback=None) -> UpdateResult:
        assert status_callback is not None
        status_callback()
        refreshed.append(True)
        if outcome == "failed":
            raise RuntimeError("CDN unavailable")
        if outcome == "locked":
            raise BlockingIOError("another updater is active")
        candidate = definitions_at(root / "generations/new", NOW, version=13)
        (root / "active.json").write_text(ActiveDefinitions(generation="new").model_dump_json())
        return UpdateResult(versions=candidate.versions, published=candidate.daily.published, directory=candidate.directory)
    monkeypatch.setattr(clamav_update, "refresh_definitions", updater)
    with connectivity_site(200) as url, socket.socket() as unused:
        unused.bind(("127.0.0.1", 0))
        offline_url = f"http://127.0.0.1:{unused.getsockname()[1]}/"
        monkeypatch.setattr(clamav_update, "DEFINITION_CHECK_URL", offline_url if outcome == "offline" else url)
        selected = definitions_for_scan(baseline, now=NOW, status_callback=status_callback)
    assert selected.daily.version == (13 if outcome == "updated" else 12)
    assert len(refreshed) == (0 if outcome == "offline" else 1)
    assert callbacks
    assert read_definitions(baseline.directory, "development") == baseline


def test_explicit_database_override_remains_pinned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """SCANNING: deterministic test/development overrides must never download or switch definitions."""
    definitions = definitions_at(tmp_path / "pinned", NOW - timedelta(days=30))
    monkeypatch.setenv(DATABASE_ENV, str(definitions.directory))
    def unexpected_network() -> bool:
        pytest.fail("pinned definitions must not contact the network")
    monkeypatch.setattr(clamav_update, "internet_available", unexpected_network)
    assert definitions_for_scan(definitions, now=NOW) is definitions


def test_scanner_refreshes_before_native_startup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """SCANNING: the normal scanner entry point refreshes; explicit validation engines do not recurse."""
    definitions = definitions_at(tmp_path / "db", NOW)
    refreshed: list[DefinitionSet] = []
    def refresh(selected: DefinitionSet, *, status_callback=None) -> DefinitionSet:
        refreshed.append(selected)
        raise KeyboardInterrupt("stop before native startup")
    monkeypatch.setattr(clamav_update, "definitions_for_scan", refresh)
    monkeypatch.setattr(clamav_definitions, "DEVELOPMENT_DATABASE", definitions.directory)
    monkeypatch.setenv(DATABASE_ENV, str(definitions.directory))
    with pytest.raises(KeyboardInterrupt, match="before native startup"):
        ClamScanner().__enter__()
    assert refreshed == [definitions]
    with pytest.raises(ClamScannerStartupError, match="missing or not a file"):
        ClamScanner(definitions=definitions, library=tmp_path / "missing-library").__enter__()
    assert refreshed == [definitions]


@pytest.mark.parametrize("during_update", [False, True])
def test_refresh_cancellation_does_not_become_an_update_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, during_update: bool,
) -> None:
    """SCANNING: Stop/Quit interrupts an automatic update rather than continuing to scan."""
    definitions = definitions_at(tmp_path / "db", NOW - timedelta(days=2))
    monkeypatch.delenv(DATABASE_ENV, raising=False)
    calls: list[bool] = []
    def interrupt() -> None:
        calls.append(True)
        if not during_update or len(calls) > 1:
            raise KeyboardInterrupt("cancelled")
    def updater(*, status_callback=None) -> UpdateResult:
        assert status_callback is not None
        status_callback()
        pytest.fail("cancelled update must not return")
    monkeypatch.setattr(clamav_update, "refresh_definitions", updater)
    with connectivity_site(200) as url:
        monkeypatch.setattr(clamav_update, "DEFINITION_CHECK_URL", url)
        with pytest.raises(KeyboardInterrupt, match="cancelled"):
            definitions_for_scan(definitions, now=NOW, status_callback=interrupt)


def test_owned_updater_callback_cancels_and_reaps_the_process(tmp_path: Path) -> None:
    """SCANNING: the updater stays cancellable and leaves no command running after cancellation."""
    marker = tmp_path / "pid"
    command = [sys.executable, "-c", "import os,sys,time; from pathlib import Path; "
               "Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(30)", str(marker)]
    def interrupt() -> None:
        if marker.exists():
            raise KeyboardInterrupt("cancel update")
    with pytest.raises(KeyboardInterrupt, match="cancel update"):
        run_owned_command(command, timeout=10, status_callback=interrupt)
    if os.name != "nt":
        with pytest.raises(ProcessLookupError):
            os.kill(int(marker.read_text()), 0)


def test_cancelled_candidate_startup_reaps_native_worker(tmp_path: Path) -> None:
    """SCANNING: cancellation during candidate validation reaps the started native worker."""
    started = []
    def interrupt() -> None:
        assert scanner.process is not None
        started.append(scanner.process)
        raise KeyboardInterrupt("cancel candidate validation")
    scanner = ClamScanner(interrupt, definitions=selected_definitions(), scan_temporary_directory=tmp_path)
    try:
        with pytest.raises(KeyboardInterrupt, match="cancel candidate validation"):
            scanner.__enter__()
        assert len(started) == 1 and not started[0].is_alive()
        assert scanner.process is None and scanner.connection is None and scanner.runtime is None
        assert list(tmp_path.iterdir()) == []
    finally:
        scanner.__exit__()
