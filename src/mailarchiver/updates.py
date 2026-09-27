# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Application update policy, separate from archive state and native UI."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal, Protocol

from packaging.version import Version
from pydantic import BaseModel

UpdateChannel = Literal["release", "preview"]


def default_channel(version: str) -> UpdateChannel:
    """Preview installations discover subsequent previews until explicitly changed."""
    return "preview" if Version(version).is_prerelease else "release"


def allowed_channels(channel: UpdateChannel) -> frozenset[str]:
    """Sparkle always includes its default channel; preview is an additional track."""
    return frozenset({"preview"}) if channel == "preview" else frozenset()


class UpdateStatus(BaseModel):
    available: bool = False
    phase: Literal["unavailable", "idle", "checking", "available", "deferred", "installing", "error"] = "unavailable"
    version: str
    build: str = ""
    channel: UpdateChannel
    automatic_checks: bool = True
    last_checked: datetime | None = None
    detail: str = "Updates are available in the installed macOS application."


class UpdateBackend(Protocol):
    """The native controller owns download, verification, and standard Sparkle UI."""

    def check(self) -> None: ...
    def configure(self, channel: UpdateChannel, automatic_checks: bool) -> None: ...


class UpdateService:
    """Retain update state and a single deferred installation continuation."""

    def __init__(self, status: UpdateStatus, reserve_install: Callable[[], bool],
                 cancel_install: Callable[[], None] = lambda: None,
                 notify_deferred: Callable[[str], None] = lambda _detail: None) -> None:
        self.status = status
        self.backend: UpdateBackend | None = None
        self.reserve_install = reserve_install
        self.cancel_install = cancel_install
        self.notify_deferred = notify_deferred
        self._pending_install: Callable[[], None] | None = None

    def attach(self, backend: UpdateBackend, build: str) -> None:
        self.backend = backend
        self.status.build = build
        self.status.available = True
        self.status.phase = "idle"
        self.status.detail = "Downloading and installation require confirmation."

    def check(self) -> None:
        if self.backend is not None:
            self.status.phase = "checking"
            self.status.detail = "Checking for updates…"
            self.backend.check()

    def configure(self, channel: UpdateChannel, automatic_checks: bool) -> None:
        self.status.channel = channel
        self.status.automatic_checks = automatic_checks
        if self.backend is not None:
            self.backend.configure(channel, automatic_checks)

    def finish_check(self, error: str | None = None) -> None:
        self.status.last_checked = datetime.now(UTC)
        if error:
            self.fail(error)
        elif self.status.phase == "checking":
            self.status.phase = "idle"
            self.status.detail = "Update check completed."

    def fail(self, detail: str) -> None:
        self.cancel_install()
        self._pending_install = None
        self.status.phase = "error"
        self.status.detail = detail[:2048]

    def defer_install(self, continuation: Callable[[], None]) -> None:
        self._pending_install = continuation
        self.status.phase = "deferred"
        self.status.detail = "Update ready. Installation waits for archive work to finish; you can safely stop it through Quit."
        self.notify_deferred(self.status.detail)

    def resume_install(self) -> bool:
        """Reserve an idle application before consuming the continuation exactly once."""
        if self._pending_install is None or not self.reserve_install():
            return False
        continuation, self._pending_install = self._pending_install, None
        self.status.phase = "installing"
        self.status.detail = "Installing update and relaunching…"
        continuation()
        return True
