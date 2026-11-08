# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Coordinate native Cocoa acceptance with actual window and run-loop events.
# Modal observers subscribe before an action and enter the modal run-loop mode.
# Visibility/focus waits recheck predicates only after relevant native notifications.
# No elapsed delay establishes readiness or repeatedly drives a callback.
# Owned observers are removed on completion; a watchdog reports missing modals.
# These helpers are imported lazily and never load Cocoa on other platforms.
"""Cocoa notification acknowledgements with explicit failure deadlines."""
from __future__ import annotations

from collections.abc import Callable
from importlib import import_module
from threading import Event, Lock, Timer
from time import monotonic
from typing import Any


class ModalObserver:
    """Inspect actual modal run-loop entry, independent of window focus notifications."""

    def __init__(self, callback: Callable[[ModalObserver], None], errors: list[str], timeout: float = 10) -> None:
        self.callback = callback
        self.errors = errors
        self._lock = Lock()
        self._active = True
        self._last_panel: tuple[int, str] | None = None
        self._core: Any = import_module("CoreFoundation")
        self._appkit = import_module("AppKit")
        self._loop = self._core.CFRunLoopGetMain()
        activities = (self._core.kCFRunLoopEntry | self._core.kCFRunLoopBeforeSources |
                      self._core.kCFRunLoopBeforeWaiting)
        self._observer = self._core.CFRunLoopObserverCreateWithHandler(None, activities, True, 0, self._entered)
        self._deadline = Timer(timeout, self._expired)
        self._deadline.daemon = True
        self._core.CFRunLoopAddObserver(self._loop, self._observer, self._appkit.NSModalPanelRunLoopMode)
        self._deadline.start()

    def _entered(self, _observer: Any, _activity: int) -> None:
        native = self._appkit.NSApplication.sharedApplication()
        panel = native.modalWindow()
        if panel is None:
            return
        identity = (int(panel.windowNumber()), str(panel.title()))
        with self._lock:
            if not self._active or self._last_panel == identity:
                return
            self._last_panel = identity
        try:
            self.callback(self)
        except Exception as error:  # pylint: disable=broad-exception-caught
            self.errors.append(f"Native modal inspection failed: {type(error).__name__}: {error}")
            self.invalidate()
            native.stopModalWithCode_(0)

    def _expired(self) -> None:
        with self._lock:
            if not self._active:
                return
            self.errors.append("Native modal readiness deadline expired")
        self.invalidate()
        self._core.CFRunLoopPerformBlock(self._loop,
            [self._appkit.NSModalPanelRunLoopMode, self._core.kCFRunLoopDefaultMode],
            lambda: self._appkit.NSApplication.sharedApplication().stopModalWithCode_(0))
        self._core.CFRunLoopWakeUp(self._loop)

    def invalidate(self) -> None:
        with self._lock:
            if not self._active:
                return
            self._active = False
        self._deadline.cancel()
        self._core.CFRunLoopRemoveObserver(self._loop, self._observer, self._appkit.NSModalPanelRunLoopMode)


def wait_native_state(condition: Callable[[], bool], label: str, timeout: float = 5) -> None:
    """Wait off the main thread for actual Cocoa visibility/focus state."""
    foundation = import_module("Foundation")
    if foundation.NSThread.isMainThread():
        raise RuntimeError("A native event waiter must not block Cocoa's main thread")
    helper = import_module("PyObjCTools.AppHelper")
    center = foundation.NSNotificationCenter.defaultCenter()
    finished = Event()
    errors: list[Exception] = []
    observers = []

    def check() -> None:
        if finished.is_set():
            return
        try:
            if condition():
                finished.set()
        except Exception as error:  # pylint: disable=broad-exception-caught
            errors.append(error)
            finished.set()

    try:
        for name in ("NSWindowDidBecomeKeyNotification", "NSWindowDidResignKeyNotification",
                     "NSWindowDidChangeOcclusionStateNotification", "NSWindowWillCloseNotification",
                     "NSApplicationDidBecomeActiveNotification"):
            observers.append(center.addObserverForName_object_queue_usingBlock_(
                name, None, None, lambda _notification: helper.callAfter(check)))
        helper.callAfter(check)
        if not finished.wait(timeout):
            raise TimeoutError(label)
        if errors:
            raise errors[0]
    finally:
        finished.set()
        for observer in observers:
            center.removeObserver_(observer)


def run_native_until(condition: Callable[[], bool], label: str, timeout: float = 10) -> None:
    """Drive main-thread Cocoa sources until a predicate succeeds; the date is a failure limit."""
    foundation = import_module("Foundation")
    if not foundation.NSThread.isMainThread():
        raise RuntimeError("Only Cocoa's main thread may drive its run loop")
    deadline = monotonic() + timeout
    loop = foundation.NSRunLoop.currentRunLoop()
    while not condition():
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError(label)
        limit = foundation.NSDate.dateWithTimeIntervalSinceNow_(remaining)
        if not loop.runMode_beforeDate_("kCFRunLoopDefaultMode", limit) and not condition():
            raise RuntimeError(f"No native event source can establish: {label}")
