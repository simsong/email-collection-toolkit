# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Verify worker-policy arithmetic, persisted choices, CLI priority and real dispatch.
# Pure policy cases cover small and odd CPU counts without replacing CPU detection.
# Disposable preference stores preserve unrelated document and updater settings.
# CLI subprocesses use a temporary home rather than the operator's preferences.
# Background GUI processing uses real canonical fixtures and checks recorded pools.
# No source account, user archive, or machine configuration is modified.
"""Desktop/CLI worker-preference requirements and continuation regressions."""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from mailarchiver.application import ApplicationController, ApplicationPreferences, ApplicationPreferencesStore, configured_workers
from mailarchiver.gui_app import build_parser
from mailarchiver.ingest_status import read_ingest_history
from mailarchiver.worker_preferences import WorkerPreferences, WorkerSettings, logical_cpus, validate_workers
from tests.test_cli_processing import HEADER
from tests.test_gui_processing import build_deferred_archive, open_headless, wait_for_import


@pytest.mark.parametrize("cpus,half", [(1, 1), (2, 1), (5, 2), (16, 8)])
def test_cpu_choices_bounds_and_moving_settings(cpus: int, half: int) -> None:
    """Half is rounded down to at least one; saved custom settings respect the current machine."""
    assert WorkerPreferences().resolve(cpus) == half
    assert WorkerPreferences(mode="all").resolve(cpus) == cpus
    assert WorkerPreferences(mode="custom", custom_count=1000).resolve(cpus) == cpus * 2
    assert validate_workers(2 * cpus, cpus) == 2 * cpus
    for invalid in (0, -1, 2 * cpus + 1, True):
        with pytest.raises(ValueError):
            validate_workers(invalid, cpus)
    settings = WorkerSettings(preferences=WorkerPreferences(), cpus=cpus, command_line=cpus * 2)
    assert settings.effective_count == cpus * 2
    assert settings.override_note == f"# workers set to {cpus * 2} on command line"
    with pytest.raises(ValidationError):
        WorkerSettings(preferences=WorkerPreferences(), cpus=cpus, command_line=cpus * 2 + 1)


def test_preferences_persist_without_changing_other_settings(tmp_path: Path) -> None:
    """Worker choices round-trip independently of recent documents and updater configuration."""
    store = ApplicationPreferencesStore(tmp_path / "preferences.json")
    store.write(ApplicationPreferences(last_archive=tmp_path / "archive", automatic_update_checks=False))
    controller = ApplicationController(store)
    selection = WorkerPreferences(mode="custom", custom_count=1)
    controller.configure_preferences("preview", False, selection, strict=True)
    reopened = ApplicationController(store)
    assert reopened.preferences.workers == selection
    assert reopened.preferences.last_archive == tmp_path / "archive"
    assert not reopened.preferences.automatic_update_checks
    assert configured_workers(store=store) == 1
    assert configured_workers(2, store) == 2
    assert store.read().workers == selection
    controller.configure_updates("release", True, strict=True)
    assert store.read().workers == selection
    before = store.path.read_bytes()
    with pytest.raises(ValueError):
        controller.configure_preferences("preview", False, WorkerPreferences(mode="custom", custom_count=2 * logical_cpus() + 1), strict=True)
    assert store.path.read_bytes() == before


@pytest.mark.parametrize("override,expected", [(None, 1), (2, 2)])
def test_gui_continuation_uses_current_preferences_or_cli_override(tmp_path: Path, override: int | None, expected: int) -> None:
    """Existing saved queues use the current effective limit; CLI launch values do not overwrite it."""
    archive = build_deferred_archive(tmp_path)
    preferences = tmp_path / "preferences.json"
    ApplicationPreferencesStore(preferences).write(ApplicationPreferences(workers=WorkerPreferences(mode="custom", custom_count=1)))
    app, api = open_headless(archive, preferences, workers_override=override)
    try:
        assert app.worker_settings().effective_count == expected
        assert api.resume_processing(False, True)
        wait_for_import(api)
        assert not any(item.severity == "error" for item in app.notices()), app.notices()
        assert read_ingest_history(archive).statuses[0].configured_workers == expected
        assert ApplicationPreferencesStore(preferences).read().workers.custom_count == 1
        if override is not None:
            with pytest.raises(ValueError, match="on command line"):
                app.save_preferences("release", True, WorkerPreferences(mode="all"))
        with sqlite3.connect(archive / "search.sqlite3") as database:
            assert database.execute("SELECT count(*) FROM message_fts WHERE message_fts MATCH 'parenttoken'").fetchone() == (1,)
    finally:
        api.close()
        app.shutdown()


def test_gui_cli_rejects_out_of_bounds_before_startup() -> None:
    """The GUI launcher validates explicit input before creating native windows."""
    assert build_parser().parse_args([]).workers is None
    assert build_parser().parse_args(["--workers", "2"]).workers == 2
    for invalid in ("0", "2.5", str(2 * logical_cpus() + 1)):
        with pytest.raises(SystemExit):
            build_parser().parse_args(["--workers", invalid])


def test_cli_preferences_and_explicit_overrides_drive_actual_pool(tmp_path: Path) -> None:
    """CLI import/process read a disposable personal preference; explicit flags win without rewriting it."""
    home = tmp_path / "home"
    home.mkdir()
    environment = os.environ | {"HOME": str(home), "APPDATA": str(home), "XDG_CONFIG_HOME": str(home)}
    initialize = ("from mailarchiver.application import ApplicationPreferencesStore,ApplicationPreferences; "
                  "from mailarchiver.worker_preferences import WorkerPreferences; "
                  "ApplicationPreferencesStore().write(ApplicationPreferences(workers=WorkerPreferences(mode='custom',custom_count=1)))")
    subprocess.run([sys.executable, "-c", initialize], env=environment, check=True, capture_output=True, text=True, timeout=15)
    source, owner, archive = tmp_path / "mail.eml", tmp_path / "owners.txt", tmp_path / "archive"
    source.write_bytes(HEADER + b"\r\nworkerpreferencetoken\r\n")
    owner.write_text("owner@example.test\n")
    for arguments, count in ((["ingest", "--no-scan", "--owner-names-file", str(owner), "--defer-content", str(source)], 1),
                             (["process", "--workers", "2"], 2)):
        result = subprocess.run([sys.executable, "-m", "mailarchiver", "--archive", str(archive), *arguments],
                                env=environment, capture_output=True, text=True, check=False, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        assert read_ingest_history(archive).statuses[0].configured_workers == count
    refreshed = subprocess.run([sys.executable, "-m", "mailarchiver", "--archive", str(archive), "refresh-index"],
                               env=environment, capture_output=True, text=True, check=False, timeout=30)
    assert refreshed.returncode == 0, refreshed.stdout + refreshed.stderr
    assert "Refreshing search index (1 workers)" in refreshed.stderr
    # An import's obsolete-index repair must also honor the explicit override.
    (archive / "search.sqlite3").unlink()
    with sqlite3.connect(archive / "search.sqlite3") as database:
        database.execute("CREATE TABLE message_metadata (sha256 TEXT PRIMARY KEY, fts_rowid INTEGER NOT NULL)")
    repaired = subprocess.run([sys.executable, "-m", "mailarchiver", "--archive", str(archive), "process", "--workers", "2"],
                              env=environment, capture_output=True, text=True, check=False, timeout=30)
    assert repaired.returncode == 0, repaired.stdout + repaired.stderr
    assert "rebuilding obsolete search index:" in repaired.stderr
    assert "Refreshing search index (2 workers)" in repaired.stderr
    subprocess.run([sys.executable, "-c", "from mailarchiver.application import ApplicationPreferencesStore; "
                    "assert ApplicationPreferencesStore().read().workers.custom_count == 1"],
                   env=environment, check=True, capture_output=True, text=True, timeout=15)
    assert source.read_bytes() == HEADER + b"\r\nworkerpreferencetoken\r\n"


def test_cli_over_limit_fails_without_creating_an_archive(tmp_path: Path) -> None:
    """All operator CLI entry points reject counts above twice the CPUs before archive writes."""
    archive = tmp_path / "not-created"
    over = str(2 * logical_cpus() + 1)
    for module, arguments in (("mailarchiver", ["--archive", str(archive), "process", "--workers", over]),
                              ("mailarchiver.imap_cli", ["--new", str(archive), "--workers", over])):
        result = subprocess.run([sys.executable, "-m", module, *arguments], capture_output=True, text=True, check=False, timeout=15)
        assert result.returncode != 0 and "twice the CPU count" in result.stderr
        assert not archive.exists()
