# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Subscribe to macOS sleep, wake and network configuration notifications.
# Keep the subscriptions on the application's main run loop.
# Wake refreshes desktop status even when the same window remains focused.
# Network notifications signal IMAP recovery without contacting the server.
# Application shutdown removes observers and the run-loop source.
"""Native desktop lifecycle notifications."""

from __future__ import annotations

from collections.abc import Callable
from importlib import import_module
import logging
from typing import Any, TYPE_CHECKING

from .imap_transport import ImapTransport

if TYPE_CHECKING:
    from collections.abc import Sequence
    from SystemConfiguration import SCDynamicStoreRef


class MacOSLifecycle:
    def __init__(self, refresh_status: Callable[[], None]) -> None:
        appkit = import_module("AppKit")
        self._core: Any = import_module("CoreFoundation")
        self._loop = self._core.CFRunLoopGetMain()
        self._center = appkit.NSWorkspace.sharedWorkspace().notificationCenter()
        self.transport = ImapTransport()
        self._refresh_status = refresh_status
        self._observers = [
            self._center.addObserverForName_object_queue_usingBlock_(name, None, None, action)
            for name, action in ((appkit.NSWorkspaceWillSleepNotification, self._sleep),
                                 (appkit.NSWorkspaceDidWakeNotification, self._wake))
        ]
        self._store: SCDynamicStoreRef | None = None
        self._source: Any = None
        self._route_keys: tuple[str, ...] = ()
        try:
            self._watch_network()
        except (ImportError, OSError, RuntimeError, TypeError, ValueError) as error:
            logging.getLogger(__name__).warning("Network change notifications unavailable: %s", error)

    def _sleep(self, _notification: object) -> None:
        self.transport.sleep()

    def _wake(self, _notification: object) -> None:
        try:
            if self._store is not None:
                self._read_path(self._store)
        finally:
            self.transport.wake()
            self._refresh_status()

    def _network_changed(self, store: SCDynamicStoreRef, _keys: Sequence[str], _context: None) -> None:
        self._read_path(store)
        self._refresh_status()

    def _read_path(self, store: SCDynamicStoreRef) -> None:
        config = import_module("SystemConfiguration")
        self.transport.network_changed(any(config.SCDynamicStoreCopyValue(store, key) for key in self._route_keys))

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
        self._read_path(store)
        self._core.CFRunLoopAddSource(self._loop, source, self._core.kCFRunLoopCommonModes)
        self._store, self._source = store, source

    def close(self) -> None:
        for observer in self._observers:
            self._center.removeObserver_(observer)
        self._observers.clear()
        if self._source is not None:
            self._core.CFRunLoopSourceInvalidate(self._source)
            self._source = self._store = None
