# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Desktop delivery: GUI success requires bounded shutdown of exit-blocking workers."""

from threading import Event, Thread, Timer

from mailarchiver.self_test import pending_gui_workers


def test_gui_shutdown_waits_for_worker_but_not_daemon() -> None:
    """A finishing worker must be joined; an idle daemon must not fail the build."""
    release, daemon_release = Event(), Event()
    worker = Thread(target=release.wait, name="finishing-gui-worker")
    daemon = Thread(target=daemon_release.wait, daemon=True)
    timer = Timer(0.05, release.set)
    timer.daemon = True
    worker.start()
    daemon.start()
    timer.start()
    try:
        assert pending_gui_workers(timeout=2) == ()
        assert not worker.is_alive()
        assert daemon.is_alive()
    finally:
        release.set()
        daemon_release.set()
        timer.cancel()
        worker.join()
        daemon.join()
        timer.join()


def test_gui_shutdown_reports_blocked_worker() -> None:
    """The observed post-success hang must become a named shutdown failure."""
    release = Event()
    worker = Thread(target=release.wait, name="blocked-gui-worker")
    worker.start()
    try:
        assert pending_gui_workers(timeout=0.01) == ("blocked-gui-worker",)
    finally:
        release.set()
        worker.join()
