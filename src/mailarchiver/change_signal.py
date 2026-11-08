# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Represent new work as a reusable completion future, rather than a timer tick.
# Producers publish their queue/state update before notifying this signal.
# Consumers capture a future before inspecting state; notifications wake all waiters.
# Futures participate in existing owner-thread FIRST_COMPLETED waits.
# Notification does not imply successful work; consumers inspect actual results.
# This keeps progress and cancellation wakeups independent of elapsed-time guesses.
"""Coalesced producer notifications for owner-thread completion waits."""
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, wait
from threading import Event, RLock
from time import monotonic
from typing import cast


class ChangeSignal:
    def __init__(self) -> None:
        self._lock = RLock()
        self._ready: Future[None] = Future()

    @property
    def ready(self) -> Future[None]:
        with self._lock:
            return self._ready

    def notify(self) -> None:
        with self._lock:
            previous, self._ready = self._ready, Future()
        previous.set_result(None)


class ChangeEvent(Event):
    """An ordinary cancellation flag that also wakes owner-thread future waits."""

    def __init__(self) -> None:
        super().__init__()
        self.changed = ChangeSignal()

    def set(self) -> None:
        super().set()
        self.changed.notify()


class ActivityCallback:
    """Carry actual wakeup sources through existing synchronous callback boundaries."""

    def __init__(self, callback: Callable[[], None], signals: tuple[ChangeSignal, ...]) -> None:
        self.callback = callback
        self.signals = signals

    def __call__(self) -> None:
        self.callback()


def await_activity[T](result: Future[T], callback: Callable[[], None] | None, timeout: float) -> T:
    """Service actual producer/cancellation events until completion or a failure deadline."""
    from .console_events import console_signal  # pylint: disable=import-outside-toplevel
    deadline = monotonic() + timeout
    signals = callback.signals if isinstance(callback, ActivityCallback) else ()
    console = console_signal()
    if console is not None:
        signals = (*signals, console)
    while True:
        changes = tuple(signal.ready for signal in signals)
        if callback is not None:
            callback()
        if result.done():
            return result.result()
        remaining = deadline - monotonic()
        if remaining <= 0 or not wait(cast(tuple[Future[object], ...], (result, *changes)), timeout=remaining,
                                      return_when=FIRST_COMPLETED).done:
            raise TimeoutError("Activity completion deadline expired")
