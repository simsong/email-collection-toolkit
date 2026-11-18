# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Subscribe to macOS sleep, wake and network configuration notifications.
# Keep the subscriptions on the application's main run loop.
# Wake refreshes desktop status even when the same window remains focused.
# Network notifications signal IMAP recovery without contacting the server.
# Application shutdown removes observers and the run-loop source.
"""Native desktop lifecycle notifications."""

from __future__ import annotations

from collections.abc import Sequence
from importlib import import_module
import logging
from typing import Any

from .desktop_lifecycle import DesktopLifecycle

class MacOSLifecycle:
    def __init__(self, lifecycle: DesktopLifecycle) -> None:
        appkit = import_module("AppKit")
        self._core: Any = import_module("CoreFoundation")
        self._loop = self._core.CFRunLoopGetMain()
        self._center = appkit.NSWorkspace.sharedWorkspace().notificationCenter()
        self._lifecycle = lifecycle
        self._observers: list[Any] = []
        self._store: object | None = None
        self._source: Any = None
        self._route_keys: tuple[str, ...] = ()
        try:
            for name, action in ((appkit.NSWorkspaceWillSleepNotification, self._sleep),
                                 (appkit.NSWorkspaceDidWakeNotification, self._wake)):
                self._observers.append(self._center.addObserverForName_object_queue_usingBlock_(
                    name, None, None, action))
        except Exception:
            self.close()
            raise
        try:
            self._watch_network()
        except (ImportError, OSError, RuntimeError, TypeError, ValueError) as error:
            logging.getLogger(__name__).warning("Network change notifications unavailable: %s", error)

    def _sleep(self, _notification: object) -> None:
        self._lifecycle.sleep()

    def _wake(self, _notification: object) -> None:
        self._lifecycle.wake(lambda: self._read_path())

    def _network_changed(self, _store: object, _keys: Sequence[str], _context: None) -> None:
        self._lifecycle.network_changed(lambda: self._read_path())

    def _read_path(self) -> bool | None:
        if self._store is None:
            return None
        config = import_module("SystemConfiguration")
        return any(config.SCDynamicStoreCopyValue(self._store, key) for key in self._route_keys)

    def _watch_network(self) -> None:
        config = import_module("SystemConfiguration")
        store = config.SCDynamicStoreCreate(None, "Email Collection Toolkit", self._network_changed, None)
        keys = [
            config.SCDynamicStoreKeyCreateNetworkGlobalEntity(None, config.kSCDynamicStoreDomainState, entity)
            for entity in (config.kSCEntNetIPv4, config.kSCEntNetIPv6, config.kSCEntNetDNS)
        ]
        if store is None or not config.SCDynamicStoreSetNotificationKeys(store, keys, None):
            raise RuntimeError("Could not subscribe to network configuration changes")
        source = config.SCDynamicStoreCreateRunLoopSource(None, store, 0)
        if source is None:
            raise RuntimeError("Could not create the network notification source")
        self._route_keys = tuple(keys[:2])
        self._core.CFRunLoopAddSource(self._loop, source, self._core.kCFRunLoopCommonModes)
        self._store, self._source = store, source
        self._lifecycle.network_changed(lambda: self._read_path())

    def close(self) -> None:
        for observer in self._observers:
            self._center.removeObserver_(observer)
        self._observers.clear()
        if self._source is not None:
            self._core.CFRunLoopSourceInvalidate(self._source)
            self._source = self._store = None
