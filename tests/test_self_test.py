# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

# These tests exercise the desktop self-test's bounded worker shutdown gate.
# Each scenario uses real finishing, blocked and daemon threads in a fresh process.
# Isolation matches the mounted app's process boundary and excludes suite workers.
# An idle executor in the parent reproduces the CI suite's unrelated thread state.
# The parent also requires bounded process exit, retaining the packaging hang guard.

from concurrent.futures import ThreadPoolExecutor
from multiprocessing import get_context
from threading import Event, Thread, Timer

import pytest

from mailarchiver.self_test import pending_gui_workers


def _exercise_shutdown(blocked: bool) -> None:
    """Desktop delivery requires joining real workers and reporting blocked ones."""
    release, daemon_release = Event(), Event()
    name = "blocked-gui-worker" if blocked else "finishing-gui-worker"
    worker = Thread(target=release.wait, name=name)
    daemon = Thread(target=daemon_release.wait, daemon=True)
    timer = Timer(0.05, release.set)
    timer.daemon = True
    worker.start()
    daemon.start()
    if not blocked:
        timer.start()
    try:
        assert pending_gui_workers(timeout=0.01 if blocked else 2) == ((name,) if blocked else ())
        assert worker.is_alive() == blocked
        assert daemon.is_alive()
    finally:
        release.set()
        daemon_release.set()
        timer.cancel()
        worker.join()
        daemon.join()
        if not blocked:
            timer.join()


@pytest.mark.parametrize("blocked", (False, True))
def test_gui_shutdown_isolated_from_suite_executor(blocked: bool) -> None:
    """An idle suite executor must not contaminate the fresh app's worker checks."""
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="shutdown-suite") as executor:
        assert executor.submit(lambda: True).result() is True
        assert "shutdown-suite_0" in pending_gui_workers(timeout=0.01)
        process = get_context("spawn").Process(target=_exercise_shutdown, args=(blocked,))
        process.start()
        try:
            process.join(timeout=10)
            assert not process.is_alive(), "shutdown scenario did not exit within its process budget"
            assert process.exitcode == 0
        finally:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
            process.close()
