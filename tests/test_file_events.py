# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Exercise real filesystem notifications on the host platform, without mocks.
# Watches are established before publications and before predicates are read.
# Atomic replacement, append and newly created parents cover archive/test signals.
# Cancellation must wake an owned waiter without a polling or scheduling timer.
# Only failure deadlines bound these assertions; elapsed time never means success.
# These regressions support the event-driven desktop and importer supervision.
"""VALIDATION: native change subscriptions cannot lose readiness or strand teardown."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
import os
import sys

import pytest

from mailarchiver.file_events import PathChanges, wait_for_path_state


def test_atomic_replacement_and_append_are_observed_after_rearming(tmp_path: Path) -> None:
    path = tmp_path / "status"
    path.write_text("old")
    with PathChanges([path]) as changes:
        temporary = tmp_path / "new"
        temporary.write_text("new")
        temporary.replace(path)
        assert changes.wait(5)
        assert path.read_text() == "new"
        with path.open("a") as output:
            output.write(" state")
        assert changes.wait(5)
        assert path.read_text() == "new state"


def test_wait_subscribes_before_reading_a_missing_nested_path(tmp_path: Path) -> None:
    target = tmp_path / "new-directory/ready"
    entered = Event()

    def ready() -> bool:
        entered.set()
        return target.is_file()

    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(wait_for_path_state, [target], ready, 5)
        assert entered.wait(5)
        target.parent.mkdir()
        target.touch()
        assert result.result(timeout=6)


def test_cancellation_releases_a_blocked_native_waiter(tmp_path: Path) -> None:
    with PathChanges([tmp_path / "never-created"]) as changes, ThreadPoolExecutor(max_workers=1) as pool:
        started = Event()

        def wait() -> bool:
            started.set()
            return changes.wait()

        result = pool.submit(wait)
        assert started.wait(5)
        changes.cancel()
        assert result.result(timeout=5) is False


def test_expired_notification_deadline_is_a_failure(tmp_path: Path) -> None:
    with PathChanges([tmp_path / "absent"]) as changes, pytest.raises(TimeoutError, match="deadline"):
        changes.wait(0)


@pytest.mark.skipif(sys.platform != "darwin", reason="Cocoa host uses kqueue vnode subscriptions")
@pytest.mark.parametrize("nested", [False, True])
def test_file_created_during_parent_registration_remains_watched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                               nested: bool) -> None:
    """VALIDATION: creating a WAL between parent open and registration cannot lose later appends."""
    target = (tmp_path / "new" if nested else tmp_path) / "database-wal"
    original_open = os.open

    def opening(path: str | bytes | os.PathLike[str], flags: int, mode: int = 0o777,
                *, dir_fd: int | None = None) -> int:
        descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        # Interpose only this exact race boundary; all handles and kevents remain real.
        if Path(os.fsdecode(path)) == tmp_path and not target.exists():
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(b"committed frame")
        return descriptor

    monkeypatch.setattr(os, "open", opening)
    with PathChanges([target]) as changes:
        assert target.exists()
        with target.open("ab") as output:
            output.write(b" next committed frame")
        assert changes.wait(5)
