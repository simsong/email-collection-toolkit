# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Translate Windows power and network notifications to shared desktop handlers.
# Register an in-process power callback, including unattended automatic resume.
# Reuse pywebview's pythonnet runtime for interface change subscriptions/queries.
# Network availability is a hint, never proof of working IMAP connectivity.
# Keep callback memory alive until native power registration is released.
# Remove static network delegates and suppress late callbacks on shutdown.
"""Windows subscriptions for the common desktop lifecycle controller."""

import ctypes
from ctypes import wintypes
from importlib import import_module
import logging
from typing import Any

from .desktop_lifecycle import DesktopLifecycle

_POWER_REGISTRATIONS: set[object] = set()  # Keep callbacks alive if native unregister fails.
_POWER_CALLBACK = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(
    wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG, ctypes.c_void_p)


class PowerSubscription(ctypes.Structure):
    """DEVICE_NOTIFY_SUBSCRIBE_PARAMETERS, owned for the native registration."""
    _fields_ = [("callback", _POWER_CALLBACK), ("context", ctypes.c_void_p)]


class WindowsLifecycle:
    def __init__(self, lifecycle: DesktopLifecycle) -> None:
        clr = import_module("clr")
        clr.AddReference("System")
        network = import_module("System.Net.NetworkInformation")
        self._lifecycle = lifecycle
        self._network: Any = network.NetworkChange
        self._interface: Any = network.NetworkInterface
        self._address = network.NetworkAddressChangedEventHandler(self._network_changed)
        self._availability = network.NetworkAvailabilityChangedEventHandler(self._network_changed)
        self._subscriptions: list[tuple[Any, Any]] = []
        self._power = _POWER_CALLBACK(self._power_changed)
        self._power_parameters = PowerSubscription(self._power, None)
        self._power_handle = wintypes.HANDLE()
        self._power_api = getattr(ctypes, "WinDLL")("powrprof", use_last_error=True)
        register = self._power_api.PowerRegisterSuspendResumeNotification
        register.argtypes = [wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(wintypes.HANDLE)]
        register.restype = wintypes.DWORD
        self._power_api.PowerUnregisterSuspendResumeNotification.argtypes = [wintypes.HANDLE]
        self._power_api.PowerUnregisterSuspendResumeNotification.restype = wintypes.DWORD
        try:
            error = register(2, ctypes.byref(self._power_parameters), ctypes.byref(self._power_handle))
            if error:
                raise OSError(error, "Could not subscribe to Windows power notifications")
            _POWER_REGISTRATIONS.add(self)
            for event, handler in ((self._network.NetworkAddressChanged, self._address),
                                   (self._network.NetworkAvailabilityChanged, self._availability)):
                event.__iadd__(handler)
                self._subscriptions.append((event, handler))
            self._lifecycle.network_changed(lambda: bool(self._interface.GetIsNetworkAvailable()))
        except Exception:
            self.close()
            raise

    def _power_changed(self, _context: object, event: int, _setting: object) -> int:
        try:
            if event == 4:  # PBT_APMSUSPEND
                self._lifecycle.sleep()
            elif event in (7, 18):  # PBT_APMRESUMESUSPEND, PBT_APMRESUMEAUTOMATIC
                self._lifecycle.wake(lambda: bool(self._interface.GetIsNetworkAvailable()))
        except Exception:  # pylint: disable=broad-exception-caught
            logging.getLogger(__name__).exception("Windows power callback failed")
        return 0

    def _network_changed(self, _sender: object, _event: object) -> None:
        self._lifecycle.network_changed(lambda: bool(self._interface.GetIsNetworkAvailable()))

    def close(self) -> None:
        subscriptions, self._subscriptions = self._subscriptions, []
        for event, handler in reversed(subscriptions):
            try:
                event.__isub__(handler)
            except Exception:  # pylint: disable=broad-exception-caught
                logging.getLogger(__name__).exception("Could not remove Windows network subscription")
        if self._power_handle.value is not None:
            error = self._power_api.PowerUnregisterSuspendResumeNotification(self._power_handle)
            if error:
                logging.getLogger(__name__).warning("Could not remove Windows power subscription: %s", error)
            else:
                self._power_handle = wintypes.HANDLE()
                _POWER_REGISTRATIONS.discard(self)
