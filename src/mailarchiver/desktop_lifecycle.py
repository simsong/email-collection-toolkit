# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Own the desktop's common sleep, wake and network event behavior.
# Native adapters supply subscriptions and short OS-query callbacks only.
# All events update the same IMAP transport and status notification path.
# A lock serializes concurrent native callbacks with application shutdown.
# Closing suppresses late callbacks and releases the adapter exactly once.
# Native failures leave timed IMAP recovery available on both platforms.
"""Shared desktop power and connectivity event handling."""

from collections.abc import Callable
import logging
from threading import RLock
from typing import Protocol

from .imap_transport import ImapTransport


class LifecycleSubscription(Protocol):
    def close(self) -> None: ...


class DesktopLifecycle:
    def __init__(self, refresh_status: Callable[[], None]) -> None:
        self.transport = ImapTransport()
        self._refresh_status = refresh_status
        self._lock = RLock()
        self._closed = False
        self._subscription: LifecycleSubscription | None = None

    def start(self, platform: str) -> None:
        """Install native events once; retain timed recovery if an OS API fails."""
        with self._lock:
            if self._closed or self._subscription is not None:
                return
            try:
                if platform == "darwin":
                    from .macos_lifecycle import MacOSLifecycle  # pylint: disable=import-outside-toplevel
                    adapter = MacOSLifecycle
                elif platform == "win32":
                    from .windows_lifecycle import WindowsLifecycle  # pylint: disable=import-outside-toplevel
                    adapter = WindowsLifecycle
                else:
                    return
                self._subscription = adapter(self)
            except Exception:  # pylint: disable=broad-exception-caught
                logging.getLogger(__name__).warning("Native lifecycle notifications unavailable", exc_info=True)

    def sleep(self) -> None:
        with self._lock:
            if not self._closed:
                self.transport.sleep()

    def wake(self, read_path: Callable[[], bool | None]) -> None:
        self._changed(read_path, waking=True)

    def network_changed(self, read_path: Callable[[], bool | None]) -> None:
        self._changed(read_path, waking=False)

    def _changed(self, read_path: Callable[[], bool | None], *, waking: bool) -> None:
        with self._lock:
            if self._closed:
                return
            try:
                available = read_path()
            except Exception:  # pylint: disable=broad-exception-caught
                logging.getLogger(__name__).warning("Could not read network availability", exc_info=True)
                available = None
            self.transport.network_changed(available)
            if waking:
                self.transport.wake()
            # Serialize delivery with close; the UI callback must never acquire
            # the application lock or synchronously wait for the UI thread.
            try:
                self._refresh_status()
            except Exception:  # pylint: disable=broad-exception-caught
                logging.getLogger(__name__).debug("Lifecycle status delivery failed", exc_info=True)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            subscription, self._subscription = self._subscription, None
        if subscription is not None:
            subscription.close()
