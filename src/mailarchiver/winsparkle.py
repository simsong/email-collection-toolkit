# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Host the pinned WinSparkle SDK through its public cdecl interface.
# Authenticate and filter the shared appcast before native update discovery.
# Retain native callbacks for process lifetime, including after SDK cleanup.
# Installation reserves the same cross-process writer fence as macOS Sparkle.
# The application schedules checks; downloads and installation require native UI consent.
# Source launches support manual checks only and never enable a background service.
"""Windows update adapter for the shared Python application."""
from __future__ import annotations

from collections.abc import Callable
import ctypes as c
from datetime import UTC, datetime
from pathlib import Path
import sys
from threading import Event, Thread

from .identity import APPLICATION_NAME
from .release_versions import release_metadata
from .update_metadata import SPARKLE_FEED_URL, SPARKLE_PUBLIC_KEY
from .updates import UpdateChannel, UpdateService
from .windows_update_feed import FeedGateway

VOID_CALLBACK = c.CFUNCTYPE(None)
BOOL_CALLBACK = c.CFUNCTYPE(c.c_int)
INSTALLER_CALLBACK = c.CFUNCTYPE(c.c_int, c.c_wchar_p)
# SDK cleanup does not join every native worker; never release its callback code.
_RETAINED: list[WinSparkleBackend] = []


class WinSparkleBackend:
    """Own native code, authenticated feed gateway and bounded check scheduling."""

    def __init__(self, service: UpdateService, library: Path, quit_application: Callable[[], None],
                 *, installed: bool) -> None:
        self.service = service
        self.installed = installed
        self.stopped = Event()
        self.gateway = FeedGateway(SPARKLE_FEED_URL, SPARKLE_PUBLIC_KEY,
                                   lambda: service.status.channel, service.fail)
        try:
            # Search only this DLL's directory and System32, never working-directory DLLs.
            self.native = c.CDLL(str(library.absolute()), winmode=0x1100)
            self.native.win_sparkle_set_appcast_url.argtypes = [c.c_char_p]
            self.native.win_sparkle_set_eddsa_public_key.argtypes = [c.c_char_p]
            self.native.win_sparkle_set_eddsa_public_key.restype = c.c_int
            self.native.win_sparkle_set_app_details.argtypes = [c.c_wchar_p, c.c_wchar_p, c.c_wchar_p]
            self.native.win_sparkle_set_app_build_version.argtypes = [c.c_wchar_p]
            self.native.win_sparkle_set_registry_path.argtypes = [c.c_char_p]
            self.native.win_sparkle_set_automatic_check_for_updates.argtypes = [c.c_int]
            for name in ("init", "cleanup", "check_update_with_ui", "check_update_without_ui"):
                function = getattr(self.native, "win_sparkle_" + name)
                function.argtypes = []
                function.restype = None
            _, _, build, display = release_metadata(service.status.version)
            self.native.win_sparkle_set_app_details("Simson Garfinkel", APPLICATION_NAME, display)
            self.native.win_sparkle_set_app_build_version(str(build))
            self.native.win_sparkle_set_registry_path(b"Software\\Email Collection Toolkit\\Python\\Updates")
            self.native.win_sparkle_set_appcast_url(self.gateway.url.encode("ascii"))
            if not self.native.win_sparkle_set_eddsa_public_key(SPARKLE_PUBLIC_KEY.encode("ascii")):
                raise RuntimeError("WinSparkle rejected the pinned update signing key")
            self.native.win_sparkle_set_automatic_check_for_updates(0)

            def can_shutdown() -> int:
                try:
                    if not self.installed or self.stopped.is_set():
                        return 0
                    if not service.reserve_install():
                        service.status.phase = "deferred"
                        service.status.detail = "Finish archive work and check again to install this update."
                        return 0
                    service.status.phase = "installing"
                    return 1
                except Exception as error:
                    service.fail(str(error))
                    return 0

            def quit_requested() -> None:
                Thread(target=quit_application, name="winsparkle-quit", daemon=True).start()

            def cancelled() -> None:
                service.cancel_install()
                service.status.phase = "idle"
                service.finish_check()

            def found() -> None:
                service.status.phase = "available"
                service.finish_check()

            def failed() -> None:
                if service.status.phase != "error":
                    service.fail("WinSparkle could not check, download or verify the update.")

            self.ready_callback = BOOL_CALLBACK(can_shutdown)
            self.native.win_sparkle_set_can_shutdown_callback.argtypes = [BOOL_CALLBACK]
            self.native.win_sparkle_set_can_shutdown_callback(self.ready_callback)
            # A source checkout must never execute a downloaded installer, even if
            # a future SDK changes the order of its shutdown and installer hooks.
            self.installer_callback = INSTALLER_CALLBACK(lambda _path: 0 if self.installed else -1)
            self.native.win_sparkle_set_user_run_installer_callback.argtypes = [INSTALLER_CALLBACK]
            self.native.win_sparkle_set_user_run_installer_callback(self.installer_callback)
            self.callbacks = []
            for name, handler in (
                ("shutdown_request", quit_requested), ("update_cancelled", cancelled),
                ("update_dismissed", cancelled), ("error", failed),
                ("did_find_update", found), ("did_not_find_update", service.finish_check),
            ):
                callback = VOID_CALLBACK(handler)
                self.callbacks.append(callback)
                setter = getattr(self.native, "win_sparkle_set_" + name + "_callback")
                setter.argtypes = [VOID_CALLBACK]
                setter(callback)
            _RETAINED.append(self)
            self.native.win_sparkle_init()
            service.attach(self, str(build))
            if not installed:
                service.status.automatic_checks = False
                service.status.detail = "Source checkout: manual update checks only; installation is disabled."
            self.scheduler = Thread(target=self._schedule, name="winsparkle-checks", daemon=True)
            self.scheduler.start()
        except BaseException:
            self.gateway.close()
            raise

    def _schedule(self) -> None:
        while not self.stopped.wait(60):
            status = self.service.status
            elapsed = (datetime.now(UTC) - status.last_checked).total_seconds() if status.last_checked else 86400
            if self.installed and status.automatic_checks and elapsed >= 86400 and status.phase in {"idle", "error"}:
                status.phase = "checking"
                status.last_checked = datetime.now(UTC)
                self.native.win_sparkle_check_update_without_ui()

    def check(self) -> None:
        if not self.stopped.is_set():
            self.native.win_sparkle_check_update_with_ui()

    def configure(self, channel: UpdateChannel, automatic_checks: bool) -> None:
        self.service.status.channel = channel
        self.service.status.automatic_checks = automatic_checks and self.installed

    def close(self) -> None:
        if self.stopped.is_set():
            return
        self.stopped.set()
        self.scheduler.join(timeout=2)
        self.native.win_sparkle_cleanup()
        self.gateway.close()


def start_windows_updater(service: UpdateService, quit_application: Callable[[], None]) -> WinSparkleBackend:
    installed_library = Path(sys.executable).parent / "winsparkle" / "WinSparkle.dll"
    installed = installed_library.is_file()
    library = installed_library if installed else Path(__file__).resolve().parents[2] / ".tmp/winsparkle/WinSparkle.dll"
    return WinSparkleBackend(service, library, quit_application, installed=installed)
