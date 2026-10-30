# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Present application preferences in a normal, reusable macOS window.
# Native controls edit the shared worker and update preference models.
# Control actions persist valid choices; the close delegate commits active edits.
# Invalid input stays visible without replacing the previously saved settings.
# A retained Cocoa delegate routes actions without a modal application loop.
# Update availability follows the installed backend and explains source-run limits.
# The application retains this window independently of archive documents.
"""Modeless native preferences with ordinary window controls and saved edits."""
from __future__ import annotations

import sys
from collections.abc import Callable
from functools import cache
from importlib import import_module
from types import new_class
from typing import Any

from .updates import PREVIEW_STREAM_LABEL, RELEASE_STREAM_LABEL, UPDATE_STREAM_HELP, UpdateChannel, UpdateService
from .worker_preferences import WORKER_MODES, WorkerPreferences, WorkerSettings

MODULE_KEY = "__module__"
PROTOCOLS_KEY = "protocols"


@cache
def preferences_delegate() -> Any:
    """Register one Objective-C delegate type; each instance retains its own pane."""
    objc = import_module("objc")
    window_protocol = objc.protocolNamed("NSWindowDelegate")

    class DelegateMethods:
        pane: MacOSPreferences

        def changed_(self, _sender):
            self.pane.commit()

        def controlTextDidEndEditing_(self, _notification):
            self.pane.commit()

        def windowShouldClose_(self, _sender):
            self.pane.window.makeFirstResponder_(None)
            return self.pane.commit()

        def windowWillClose_(self, _notification):
            if self.pane.timer is not None:
                self.pane.timer.invalidate()
                self.pane.timer = None

        def checkUpdates_(self, _sender):
            if self.pane.commit():
                self.pane.service.check()
                self.pane.refresh_status()

    def namespace(values: dict[str, object]) -> None:
        values[MODULE_KEY] = __name__
        values.update((name, method) for name, method in vars(DelegateMethods).items() if not name.startswith("__"))
        # AppKit's arm64 BOOL signature differs from PyObjC's legacy default.
        signature = window_protocol.descriptionForInstanceMethod_(b"windowShouldClose:")[1]
        values["windowShouldClose_"] = objc.selector(DelegateMethods.windowShouldClose_, signature=signature)

    return new_class("ECTPreferencesDelegate", (objc.lookUpClass("NSObject"),),
                     {PROTOCOLS_KEY: [window_protocol, objc.protocolNamed("NSTextFieldDelegate")]}, namespace)


class MacOSPreferences:
    """Own native controls, validation and a reusable preferences window."""

    def __init__(self, service: UpdateService, save: Callable[[UpdateChannel, bool, WorkerPreferences | None], None]) -> None:
        self.service, self.save = service, save
        self.workers: WorkerSettings
        self.saved: tuple[UpdateChannel, bool, WorkerPreferences | None] | None = None
        self.timer: Any = None
        appkit = import_module("AppKit")
        parent = appkit.NSApplication.sharedApplication().keyWindow()
        self.screen = parent.screen() if parent is not None else appkit.NSScreen.mainScreen()
        self.delegate = preferences_delegate().alloc().init()
        self.delegate.pane = self
        self.window = appkit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            ((0, 0), (580, 620)), appkit.NSWindowStyleMaskTitled | appkit.NSWindowStyleMaskClosable |
            appkit.NSWindowStyleMaskMiniaturizable, appkit.NSBackingStoreBuffered, False)
        self.window.setReleasedWhenClosed_(False)
        self.window.setTitle_("Preferences")
        self.window.setDelegate_(self.delegate)
        view = self.window.contentView()

        def label(text: str, y: int, height: int = 24) -> Any:
            control = appkit.NSTextField.wrappingLabelWithString_(text)
            control.setFrame_(((24, y), (532, height)))
            view.addSubview_(control)
            return control

        self.version = label("", 568)
        label("Maximum number of workers", 520)
        prototype = appkit.NSButtonCell.alloc().init()
        prototype.setButtonType_(appkit.NSRadioButton)
        self.choices = appkit.NSMatrix.alloc().initWithFrame_mode_prototype_numberOfRows_numberOfColumns_(
            ((24, 408), (300, 96)), appkit.NSRadioModeMatrix, prototype, 3, 1)
        self.choices.setCellSize_((300, 28))
        self.choices.setIntercellSpacing_((0, 4))
        self.choices.setAccessibilityIdentifier_("worker-mode")
        self.choices.setTarget_(self.delegate)
        self.choices.setAction_("changed:")
        view.addSubview_(self.choices)
        self.count = appkit.NSTextField.alloc().initWithFrame_(((340, 408), (150, 28)))
        self.count.setAccessibilityIdentifier_("worker-count")
        self.count.setDelegate_(self.delegate)
        view.addSubview_(self.count)
        self.note = label("", 340, 56)
        self.note.setAccessibilityIdentifier_("worker-note")
        label("Updates", 300)
        self.channel = appkit.NSPopUpButton.alloc().initWithFrame_pullsDown_(((24, 260), (532, 28)), False)
        self.channel.addItemsWithTitles_([RELEASE_STREAM_LABEL, PREVIEW_STREAM_LABEL])
        self.channel.setTarget_(self.delegate)
        self.channel.setAction_("changed:")
        view.addSubview_(self.channel)
        self.automatic = appkit.NSButton.checkboxWithTitle_target_action_(
            "Check for updates automatically (daily)", self.delegate, "changed:")
        self.automatic.setFrame_(((24, 224), (532, 28)))
        view.addSubview_(self.automatic)
        label(f"Downloading and installation always require confirmation.\n{UPDATE_STREAM_HELP}", 150, 64)
        self.detail = label("", 70, 72)
        self.detail.setAccessibilityIdentifier_("update-status")
        self.check = appkit.NSButton.buttonWithTitle_target_action_("Check for Program Updates", self.delegate, "checkUpdates:")
        self.check.setFrame_(((24, 24), (220, 32)))
        self.check.setAccessibilityIdentifier_("check-updates")
        view.addSubview_(self.check)

    def show(self, workers: WorkerSettings) -> None:
        """Reuse an open pane; refresh saved choices each time a closed pane reopens."""
        if self.window.isVisible():
            self.window.deminiaturize_(None)
            self.window.makeKeyAndOrderFront_(None)
            return
        self.workers = workers
        status = self.service.status
        self.version.setStringValue_(f"You are running {status.version} (build {status.build or 'source checkout'}).")
        for index, title in enumerate((f"Half the CPUs ({workers.half})", f"All CPUs ({workers.cpus})", "Custom")):
            self.choices.cellAtRow_column_(index, 0).setTitle_(title)
        self.choices.selectCellAtRow_column_(WORKER_MODES.index(workers.preferences.mode), 0)
        self.choices.setEnabled_(workers.command_line is None)
        self.count.setStringValue_(str(min(workers.preferences.custom_count, workers.maximum)))
        self.count.setEnabled_(workers.command_line is None)
        self.channel.selectItemAtIndex_(int(status.channel == "preview"))
        self.automatic.setState_(int(status.automatic_checks))
        self.saved = (status.channel, status.automatic_checks, workers.preferences if workers.command_line is None else None)
        self.note.setStringValue_(self.worker_note())
        self.refresh_status()
        appkit = import_module("AppKit")
        parent = appkit.NSApplication.sharedApplication().keyWindow()
        if parent is not None and parent != self.window:
            self.screen = parent.screen()
        area = self.screen.visibleFrame()
        size = self.window.frame().size
        self.window.setFrameOrigin_((area.origin.x + (area.size.width - size.width) / 2,
                                     area.origin.y + (area.size.height - size.height) / 2))
        self.window.makeKeyAndOrderFront_(None)
        if self.timer is not None:
            self.timer.invalidate()
        self.timer = import_module("Foundation").NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            0.5, True, lambda _timer: self.refresh_status())

    def worker_note(self) -> str:
        return self.workers.override_note or (
            f"1–{self.workers.maximum} workers (at most twice {self.workers.cpus} CPUs).\n"
            "Changes save automatically and apply when starting or continuing work.")

    def commit(self) -> bool:
        """Persist valid controls, leaving invalid input and failed writes visible."""
        try:
            selection = (WorkerPreferences.from_input(WORKER_MODES[self.choices.selectedRow()],
                         int(self.count.stringValue()), self.workers.cpus) if self.workers.command_line is None else None)
            channel: UpdateChannel = "preview" if self.channel.indexOfSelectedItem() == 1 else "release"
            value = channel, bool(self.automatic.state()), selection
            if value != self.saved:
                self.save(*value)
                if self.saved is None or value[:2] != self.saved[:2]:
                    self.service.configure(channel, bool(self.automatic.state()))
                self.saved = value
            self.note.setStringValue_(self.worker_note())
            return True
        except (OSError, ValueError, RuntimeError) as error:
            self.note.setStringValue_(f"Could not save preferences: {error}")
            return False

    def refresh_status(self) -> None:
        status = self.service.status
        checked = status.last_checked.astimezone().strftime("%Y-%m-%d %H:%M %Z") if status.last_checked else "Not yet"
        detail = status.detail
        if not status.available and status.phase == "unavailable" and not getattr(sys, "frozen", False):
            detail = "In-app updates require the installed macOS app. This is a source checkout."
        self.detail.setStringValue_(f"Last checked: {checked}\n{detail}")
        self.check.setEnabled_(status.available and status.phase not in {"checking", "installing"})
