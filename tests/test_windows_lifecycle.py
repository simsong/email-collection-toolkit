# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Validate actual Windows power registration and .NET network subscriptions.
# Invoke the registered native callback for suspend and both resume variants.
# No test physically suspends the computer or changes network configuration.
# Network delegates query real interfaces and feed the common controller.
# Closing must release power registration and detach static network delegates.
# The shared tests cover transport waits and explicit user cancellation.
"""DESKTOP: native Windows lifecycle subscriptions feed common recovery handlers."""

import sys
from threading import Event

import pytest

from mailarchiver.change_signal import ChangeEvent
from mailarchiver.desktop_lifecycle import DesktopLifecycle
from mailarchiver.windows_lifecycle import WindowsLifecycle

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="requires real Windows/.NET event APIs")


@pytest.mark.parametrize("resume", [7, 18], ids=["interactive-resume", "automatic-resume"])
def test_windows_native_power_registration_network_callbacks_and_cleanup(resume: int) -> None:
    refreshed = Event()
    lifecycle = DesktopLifecycle(refreshed.set)
    lifecycle.start("win32")
    adapter = lifecycle._subscription
    assert isinstance(adapter, WindowsLifecycle), "Native Windows subscriptions must install successfully"
    lifecycle.start("win32")
    assert lifecycle._subscription is adapter and len(adapter._subscriptions) == 2
    assert adapter._power_handle.value is not None
    session = lifecycle.transport.session(ChangeEvent())
    assert session is not None
    try:
        refreshed.clear()
        assert adapter._power_parameters.callback(None, 4, None) == 0
        session.result(timeout=2)
        assert adapter._power_parameters.callback(None, resume, None) == 0
        assert refreshed.wait(2)
        replacement = lifecycle.transport.session(ChangeEvent())
        assert replacement is not None and not replacement.done()
        for callback in (adapter._address, adapter._availability):
            refreshed.clear()
            changed = lifecycle.transport.changed.ready
            callback.Invoke(None, None)
            assert changed.done() and refreshed.wait(2)
            assert lifecycle.transport.path_available == bool(adapter._interface.GetIsNetworkAvailable())
    finally:
        lifecycle.close()
    assert not adapter._subscriptions and adapter._power_handle.value is None
    refreshed.clear()
    changed = lifecycle.transport.changed.ready
    assert adapter._power_parameters.callback(None, resume, None) == 0
    adapter._address.Invoke(None, None)
    assert not changed.done() and not refreshed.is_set()
    lifecycle.close()
