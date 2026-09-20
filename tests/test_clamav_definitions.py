# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""Requirements: authoritative definition dates, calendar months, atomic verified updates."""
from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mailarchiver.clamav_definitions import ActiveDefinitions, DATABASE_NAMES, choose_definitions, read_definitions, selected_definitions, three_months_after
from mailarchiver.clamav_update import publish_definitions, update_lock
from mailarchiver.scanner import ClamScannerStartupError


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


def test_update_lock_blocks_concurrent_writer_and_releases(tmp_path: Path) -> None:
    with update_lock(tmp_path):
        with pytest.raises(OSError):
            with update_lock(tmp_path):
                pytest.fail("a second updater acquired the lock")
    with update_lock(tmp_path):
        pass


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
