# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""Requirements: authoritative definition dates, calendar months, atomic verified updates."""
from __future__ import annotations

import shutil
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mailarchiver.clamav_definitions import ActiveDefinitions, DATABASE_NAMES, UPDATE_ENV, choose_definitions, read_definitions, selected_definitions, three_months_after
from mailarchiver.clamav_update import publish_definitions, publish_downloaded_definitions, update_lock
from mailarchiver.scanner import ClamScannerStartupError
from mailarchiver.owned_command import run_owned_command
from mailarchiver.writer_lock import WriterLease


def test_owned_updater_preserves_diagnostics_and_enforces_timeout() -> None:
    """The supervised updater reports command failures and bounds an unresponsive utility."""
    result = run_owned_command([sys.executable, "-c", "print('update diagnostic'); raise SystemExit(7)"], timeout=5)
    assert result.returncode == 7 and result.stdout == "update diagnostic\n"
    with pytest.raises(RuntimeError, match="timed out"):
        run_owned_command([sys.executable, "-c", "from threading import Event; Event().wait()"], timeout=0.1)


@pytest.mark.parametrize(("start", "expected"), [
    ("2026-06-18", "2026-09-18"), ("2026-11-30", "2027-02-28"), ("2027-11-30", "2028-02-29"),
    ("2026-01-31", "2026-04-30"),
])
def test_three_calendar_months_clamps_month_end(start: str, expected: str) -> None:
    assert three_months_after(datetime.fromisoformat(start).replace(tzinfo=UTC)) == datetime.fromisoformat(expected).replace(tzinfo=UTC)


def write_headers(directory: Path, daily: int = 12) -> None:
    directory.mkdir()
    for name in DATABASE_NAMES:
        version = daily if name == "daily" else 1
        epoch = int(datetime(2026 if name == "daily" else 2020, 1, 31, tzinfo=UTC).timestamp())
        header = f"ClamAV-VDB:31 Jan 2026 00-00 +0000:{version}:1:90:unused:unused:fixture:{epoch}"
        (directory / f"{name}.cvd").write_bytes(header.encode().ljust(512, b" "))


def test_header_date_ignores_copy_time_and_old_main(tmp_path: Path) -> None:
    write_headers(tmp_path / "db")
    definitions = read_definitions(tmp_path / "db", "bundled")
    assert definitions.daily.published == datetime(2026, 1, 31, tzinfo=UTC)
    assert definitions.versions == "main:1,daily:12,bytecode:1"
    (tmp_path / "db/daily.cld").write_bytes((tmp_path / "db/daily.cvd").read_bytes())
    with pytest.raises(ValueError, match="exactly one daily"):
        read_definitions(tmp_path / "db", "bundled")


def test_invalid_update_preserves_active_manifest_and_existing_files(tmp_path: Path) -> None:
    baseline = selected_definitions()
    root = tmp_path / "updates"
    staging = tmp_path / "staging"
    staging.mkdir()
    for item in baseline.files:
        shutil.copyfile(item.path, staging / item.path.name)
    daily = staging / baseline.daily.path.name
    with daily.open("r+b") as handle:
        # Updates may produce uncompressed CLD archives; truncate the payload,
        # rather than changing bytes that could be unused tar header fields.
        handle.truncate(520)
    root.mkdir()
    active = root / "active.json"
    active.write_bytes(b'{"generation":"previous"}')
    with update_lock(root):
        with pytest.raises(ClamScannerStartupError):
            publish_definitions(staging, root, baseline)
    assert active.read_bytes() == b'{"generation":"previous"}'
    assert staging.is_dir()
    assert not (root / "generations").exists()


def test_publication_failure_retains_updater_diagnostics_after_staging_moves(tmp_path: Path) -> None:
    """Requirement: failed activation retains its cause and downloaded-file evidence."""
    baseline = selected_definitions()
    root = tmp_path / "updates"
    root.mkdir()
    active = root / "active.json"
    active.mkdir()  # Real filesystem failure after generation publication.
    sentinel = active / "existing"
    sentinel.write_bytes(b"preserve existing state")
    staging = tmp_path / "staging"
    staging.mkdir()
    for item in baseline.files:
        os.link(item.path, staging / item.path.name)
    with update_lock(root), pytest.raises(RuntimeError, match="FreshClam: download complete") as failure:
        publish_downloaded_definitions(staging, root, baseline, "download complete")
    assert isinstance(failure.value.__cause__, OSError)
    assert not isinstance(failure.value.__cause__, FileNotFoundError)
    assert baseline.daily.path.name in str(failure.value)
    assert not staging.exists()
    assert len(list((root / "generations").iterdir())) == 1
    assert sentinel.read_bytes() == b"preserve existing state"


def test_update_lock_blocks_concurrent_writer_and_releases(tmp_path: Path) -> None:
    with update_lock(tmp_path):
        with pytest.raises(OSError):
            with update_lock(tmp_path):
                pytest.fail("a second updater acquired the lock")
    with update_lock(tmp_path):
        pass


@pytest.mark.parametrize("development", [False, True])
def test_cli_definition_refresh_is_blocked_before_writes_during_install(tmp_path: Path, development: bool) -> None:
    """Issue #91: both real CLI paths respect the OS install reservation without running FreshClam."""
    root = tmp_path / "definitions"
    assert WriterLease.reserve_update()
    try:
        result = subprocess.run([sys.executable, "-m", "mailarchiver.clamav_update", *(["--development"] if development else [])],
                                env={**os.environ, UPDATE_ENV: str(root)}, capture_output=True, text=True,
                                check=False, timeout=10)
        assert result.returncode != 0
        assert "application writer or update installation is active" in result.stderr
        assert not root.exists()
    finally:
        WriterLease.cancel_update()


def test_external_definition_lock_defers_install_and_releases(tmp_path: Path) -> None:
    """Issue #91: the exact shared guard used by definition refresh blocks GUI relaunch."""
    script = (
        "import sys\nfrom pathlib import Path\n"
        "from mailarchiver.writer_lock import application_write_activity\n"
        "from mailarchiver.clamav_update import update_lock\n"
        "with application_write_activity(), update_lock(Path(sys.argv[1])):\n"
        " print('locked',flush=True)\n sys.stdin.readline()\n"
    )
    child = subprocess.Popen([sys.executable, "-c", script, str(tmp_path / "definitions")],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "locked"
        assert WriterLease.writers_active()
        assert not WriterLease.reserve_update()
    finally:
        assert child.stdin is not None
        child.stdin.write("release\n")
        child.stdin.flush()
        child.wait(timeout=10)
    assert child.returncode == 0, child.stderr.read() if child.stderr else ""
    try:
        assert WriterLease.reserve_update()
    finally:
        WriterLease.cancel_update()


def test_selection_uses_newer_baseline_and_recovers_bad_manifest(tmp_path: Path) -> None:
    baseline_path = tmp_path / "baseline"
    update = tmp_path / "generations" / "candidate"
    update.parent.mkdir()
    write_headers(baseline_path, 12)
    write_headers(update, 11)
    baseline = read_definitions(baseline_path, "bundled")
    manifest = tmp_path / "active.json"
    manifest.write_text(ActiveDefinitions(generation="candidate").model_dump_json())
    assert choose_definitions(baseline, tmp_path).definitions.source == "bundled"
    shutil.rmtree(update)
    write_headers(update, 13)
    assert choose_definitions(baseline, tmp_path).definitions.daily.version == 13
    manifest.write_text(ActiveDefinitions(generation="../escape").model_dump_json())
    choice = choose_definitions(baseline, tmp_path)
    assert choice.definitions.source == "bundled" and "using baseline" in choice.warning


def test_validated_user_generation_does_not_require_a_bundled_baseline(tmp_path: Path) -> None:
    """First-run user storage becomes authoritative after native validation publishes it."""
    with pytest.raises(ValueError, match="Update virus definitions"):
        choose_definitions(None, tmp_path)
    generation = tmp_path / "generations" / "candidate"
    generation.parent.mkdir()
    write_headers(generation)
    manifest = tmp_path / "active.json"
    manifest.write_text(ActiveDefinitions(generation="candidate").model_dump_json())
    assert choose_definitions(None, tmp_path).definitions.directory == generation
    manifest.write_text(ActiveDefinitions(generation="../escape").model_dump_json())
    with pytest.raises(ValueError, match="Update virus definitions"):
        choose_definitions(None, tmp_path)
