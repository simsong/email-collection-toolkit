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
import subprocess
import textwrap

import pytest

from mailarchiver.file_events import PathChanges, wait_for_path_state


def test_failed_process_observer_start_closes_real_subscriptions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ACQUISITION: failure to obtain an observer thread must release already-open native watches."""
    from threading import Thread
    import mailarchiver.file_events as events

    class UnavailableThread(Thread):
        def start(self) -> None:
            raise RuntimeError("no thread resources")

    monkeypatch.setattr(events, "Thread", UnavailableThread)  # Fault only OS thread admission.
    process = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.buffer.read()"], stdin=subprocess.PIPE)
    observer = object.__new__(events.ProcessActivity)
    try:
        with pytest.raises(RuntimeError, match="no thread resources"):
            observer.__init__(process, [tmp_path])
        assert observer.changes.cancelled.is_set() and observer.changes._closed
        assert observer.changes._fds == [] and observer.changes._handles == []
        assert observer.changes._read == observer.changes._write == -1
        watcher = object.__new__(events.PathObserver)
        with pytest.raises(RuntimeError, match="no thread resources"):
            watcher.__init__([tmp_path], lambda: None)
        assert watcher.changes.cancelled.is_set() and watcher.changes._closed
        assert watcher.changes._fds == [] and watcher.changes._handles == []
    finally:
        if process.stdin is not None:
            process.stdin.close()
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor relocation; Windows waits use kernel handles")
def test_readiness_supports_descriptors_above_select_limit() -> None:
    """VALIDATION: actual readable pipes above FD_SETSIZE work without machine configuration changes."""
    script = textwrap.dedent('''\
        # Exercise the shared readiness helper with a real high-numbered pipe.
        # Raise a resource limit only in this disposable child process if needed.
        # Descriptor relocation retains the real kernel pipe and its contents.
        # No timer or fake readiness response establishes successful completion.
        # The parent contains unexpected blocking with a failure deadline.
        import fcntl, os, resource, sys
        from mailarchiver.file_events import _readable
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        if hard != resource.RLIM_INFINITY and hard < 1600:
            sys.exit(77)
        resource.setrlimit(resource.RLIMIT_NOFILE, (max(soft, 1600), hard))
        reader, writer = os.pipe()
        high = fcntl.fcntl(reader, fcntl.F_DUPFD, 1500)
        try:
            assert not _readable((high,), 0)
            os.write(writer, b'actual publication')
            assert _readable((high,), 2)
            assert os.read(high, 64) == b'actual publication'
        finally:
            for descriptor in (reader, writer, high): os.close(descriptor)
    ''')
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=10, check=False)
    if result.returncode == 77:
        pytest.skip("Process hard descriptor limit cannot represent a descriptor above FD_SETSIZE")
    assert result.returncode == 0, result.stdout + result.stderr


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
