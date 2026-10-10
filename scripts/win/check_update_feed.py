# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Exercise the actual SDK against the authenticated public update feed.
# Use the source-launch installation fence and disable automatic checks.
# Native WinSparkle performs discovery through the private feed gateway.
# Fail on signature, network, parsing or deadline errors instead of claiming success.
# This check never requests download or installer execution and touches no archive.
"""Live Windows update discovery acceptance through Make."""
from __future__ import annotations

from importlib.metadata import version
from threading import Event
from time import monotonic

from mailarchiver.updates import UpdateService, UpdateStatus, default_channel
from mailarchiver.winsparkle import start_windows_updater


def main() -> None:
    current = version("mailarchiver")
    service = UpdateService(UpdateStatus(version=current, channel=default_channel(current)), lambda: False)
    backend = start_windows_updater(service, lambda: None)
    try:
        if backend.installed:
            raise RuntimeError("Run this check from the source environment")
        service.status.phase = "checking"
        backend.native.win_sparkle_check_update_without_ui()
        deadline = monotonic() + 45
        while service.status.phase == "checking" and monotonic() < deadline:
            Event().wait(0.05)
        if service.status.phase not in {"idle", "available"} or service.status.last_checked is None:
            raise RuntimeError(f"Live native update check failed: {service.status.model_dump_json()}")
        print(service.status.model_dump_json(indent=2))
    finally:
        backend.close()


if __name__ == "__main__":
    main()
