# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Exercise the common desktop lifecycle with real transport and cancellation.
# OS path queries are callbacks so power and network logic stays shared.
# Tests cover session invalidation, wake, user cancellation and disposal races.
# Native subscription execution belongs to the platform integration tests.
# No test changes the host's power state, interfaces or source mail.
"""DESKTOP/ACQUISITION: platform events share recovery and shutdown semantics."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

from mailarchiver.change_signal import ChangeEvent
from mailarchiver.desktop_lifecycle import DesktopLifecycle


def test_common_handlers_preserve_cancellation_and_replace_sleeping_sessions() -> None:
    refreshed: list[bool | None] = []
    lifecycle = DesktopLifecycle(lambda: refreshed.append(lifecycle.transport.path_available))
    stop = ChangeEvent()
    session = lifecycle.transport.session(stop)
    assert session is not None and not session.done()
    lifecycle.sleep()
    assert session.done()
    with ThreadPoolExecutor(max_workers=1) as pool:
        waiting = pool.submit(lifecycle.transport.session, stop)
        assert not waiting.done()
        lifecycle.wake(lambda: False)
        replacement = waiting.result(timeout=2)
    assert replacement is not None and not replacement.done()
    changed = lifecycle.transport.changed.ready
    lifecycle.network_changed(lambda: True)
    assert changed.done() and refreshed == [False, True]
    # A later OS notification cannot clear an explicit Suspend/Quit request.
    stop.set()
    lifecycle.sleep()
    lifecycle.wake(lambda: True)
    assert lifecycle.transport.session(stop) is None
    lifecycle.close()


def test_callback_errors_do_not_leave_transport_sleeping_or_abort_later_events() -> None:
    refreshed: list[None] = []

    def broken_path() -> bool:
        raise OSError("interface disappeared")

    def broken_status() -> None:
        refreshed.append(None)
        raise RuntimeError("window closed")

    lifecycle = DesktopLifecycle(broken_status)
    lifecycle.sleep()
    lifecycle.wake(broken_path)
    session = lifecycle.transport.session(ChangeEvent())
    assert session is not None and not session.done()
    assert lifecycle.transport.path_available is None
    lifecycle.network_changed(lambda: True)
    assert lifecycle.transport.path_available is True and len(refreshed) == 2
    lifecycle.close()


def test_closed_controller_ignores_queued_events_before_querying_disposed_os_state() -> None:
    refreshed: list[None] = []
    lifecycle = DesktopLifecycle(lambda: refreshed.append(None))
    release = Event()
    queried: list[None] = []

    def queued_event() -> None:
        assert release.wait(2)
        lifecycle.wake(disposed_path)
        lifecycle.network_changed(disposed_path)
        lifecycle.sleep()

    def disposed_path() -> bool:
        queried.append(None)
        raise AssertionError("OS query after disposal")

    session = lifecycle.transport.session(ChangeEvent())
    with ThreadPoolExecutor(max_workers=1) as pool:
        queued = pool.submit(queued_event)
        lifecycle.close()
        release.set()
        queued.result(timeout=2)
    assert session is not None and not session.done() and not refreshed and not queried
    lifecycle.start("win32")  # Closing also forbids installing new subscriptions.
    assert lifecycle._subscription is None
