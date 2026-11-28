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
from .imap_preferences import DEFAULT_IMAP_CONNECTIONS, MAX_IMAP_CONNECTIONS, validate_imap_connections
from .application import MULTI_MESSAGE_FORMATS, MultiMessageFormat

DRAG_FORMAT_TITLES = ("One MBOX file (default)", "A ZIP of .eml files")

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

    def __init__(self, service: UpdateService, save: Callable[[UpdateChannel, bool, WorkerPreferences | None, int], None],
                 save_drag_format: Callable[[MultiMessageFormat], None] | None = None) -> None:
        self.service, self.save, self.save_drag_format = service, save, save_drag_format
        self.saved_drag_format: MultiMessageFormat = "mbox"
        self.workers: WorkerSettings
        self.saved: tuple[UpdateChannel, bool, WorkerPreferences | None, int] | None = None
        appkit = import_module("AppKit")
        parent = appkit.NSApplication.sharedApplication().keyWindow()
        self.screen = parent.screen() if parent is not None else appkit.NSScreen.mainScreen()
        self.delegate = preferences_delegate().alloc().init()
        self.delegate.pane = self
        # A compact two-column form: right-aligned section labels, controls on the
        # right, small secondary notes under their control. Rows run top-down from y=0.
        width, height, left, column, control_width = 560, 478, 20, 176, 364
        self.window = appkit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            ((0, 0), (width, height)), appkit.NSWindowStyleMaskTitled | appkit.NSWindowStyleMaskClosable |
            appkit.NSWindowStyleMaskMiniaturizable, appkit.NSBackingStoreBuffered, False)
        self.window.setReleasedWhenClosed_(False)
        self.window.setTitle_("Preferences")
        self.window.setDelegate_(self.delegate)
        view = self.window.contentView()
        small = appkit.NSFont.systemFontOfSize_(appkit.NSFont.smallSystemFontSize())

        def frame(x: int, top: int, w: int, h: int) -> tuple[tuple[int, int], tuple[int, int]]:
            return ((x, height - top - h), (w, h))

        def add(control: Any, x: int, top: int, w: int, h: int) -> Any:
            control.setFrame_(frame(x, top, w, h))
            view.addSubview_(control)
            return control

        def heading(text: str, top: int) -> Any:
            control = add(appkit.NSTextField.labelWithString_(text), left, top + 3, column - left - 8, 18)
            control.setAlignment_(appkit.NSTextAlignmentRight)
            return control

        def note(text: str, top: int, h: int) -> Any:
            control = add(appkit.NSTextField.wrappingLabelWithString_(text), column, top, control_width, h)
            control.setFont_(small)
            control.setTextColor_(appkit.NSColor.secondaryLabelColor())
            return control

        self.version = note("", 16, 16)
        heading("Workers:", 44)
        prototype = appkit.NSButtonCell.alloc().init()
        prototype.setButtonType_(appkit.NSRadioButton)
        self.choices = appkit.NSMatrix.alloc().initWithFrame_mode_prototype_numberOfRows_numberOfColumns_(
            frame(column, 44, 160, 66), appkit.NSRadioModeMatrix, prototype, 3, 1)
        self.choices.setCellSize_((160, 20))
        self.choices.setIntercellSpacing_((0, 3))
        self.choices.setAccessibilityIdentifier_("worker-mode")
        self.choices.setTarget_(self.delegate)
        self.choices.setAction_("changed:")
        view.addSubview_(self.choices)
        self.count = add(appkit.NSTextField.alloc().initWithFrame_(frame(0, 0, 0, 0)), column + 80, 87, 56, 22)
        self.count.setAccessibilityIdentifier_("worker-count")
        self.count.setDelegate_(self.delegate)
        self.note = note("", 114, 30)
        self.note.setAccessibilityIdentifier_("worker-note")
        heading("IMAP connections:", 156)
        self.imap_count = add(appkit.NSTextField.alloc().initWithFrame_(frame(0, 0, 0, 0)), column, 156, 56, 22)
        self.imap_count.setAccessibilityIdentifier_("imap-connections")
        self.imap_count.setDelegate_(self.delegate)
        note(f"Per account. 1–{MAX_IMAP_CONNECTIONS}; default {DEFAULT_IMAP_CONNECTIONS}. Independent of workers; "
             "applies when starting or continuing an import.", 182, 30)
        heading("Drag messages as:", 224)
        self.drag_format = add(appkit.NSPopUpButton.alloc().initWithFrame_pullsDown_(frame(0, 0, 0, 0), False),
                               column - 3, 222, 230, 26)
        self.drag_format.addItemsWithTitles_(list(DRAG_FORMAT_TITLES))
        self.drag_format.setTarget_(self.delegate)
        self.drag_format.setAction_("changed:")
        note("When dragging several messages out of a search window.", 250, 16)
        separator = add(appkit.NSBox.alloc().initWithFrame_(frame(0, 0, 0, 0)), left, 280, width - 2 * left, 1)
        separator.setBoxType_(appkit.NSBoxSeparator)
        heading("Updates:", 296)
        self.channel = add(appkit.NSPopUpButton.alloc().initWithFrame_pullsDown_(frame(0, 0, 0, 0), False),
                           column - 3, 294, 300, 26)
        self.channel.addItemsWithTitles_([RELEASE_STREAM_LABEL, PREVIEW_STREAM_LABEL])
        self.channel.setTarget_(self.delegate)
        self.channel.setAction_("changed:")
        self.automatic = add(appkit.NSButton.checkboxWithTitle_target_action_(
            "Check when the application opens", self.delegate, "changed:"), column, 326, control_width, 20)
        note(f"Downloading and installation always require confirmation. {UPDATE_STREAM_HELP}", 352, 44)
        self.detail = note("", 400, 30)
        self.detail.setAccessibilityIdentifier_("update-status")
        self.check = add(appkit.NSButton.buttonWithTitle_target_action_(
            "Check for Program Updates", self.delegate, "checkUpdates:"), column - 6, 436, 210, 28)
        self.check.setAccessibilityIdentifier_("check-updates")

    def show(self, workers: WorkerSettings, imap_connections: int = DEFAULT_IMAP_CONNECTIONS,
             drag_format: MultiMessageFormat = "mbox") -> None:
        """Reuse an open pane; refresh saved choices each time a closed pane reopens."""
        if self.window.isVisible():
            self.window.deminiaturize_(None)
            self.window.makeKeyAndOrderFront_(None)
            return
        self.workers = workers
        status = self.service.status
        self.version.setStringValue_(f"Version {status.version} ({status.build or 'source checkout'})")
        for index, title in enumerate((f"Half the CPUs ({workers.half})", f"All CPUs ({workers.cpus})", "Custom")):
            self.choices.cellAtRow_column_(index, 0).setTitle_(title)
        self.choices.selectCellAtRow_column_(WORKER_MODES.index(workers.preferences.mode), 0)
        self.choices.setEnabled_(workers.command_line is None)
        self.count.setStringValue_(str(min(workers.preferences.custom_count, workers.maximum)))
        self.count.setEnabled_(workers.command_line is None)
        self.imap_count.setStringValue_(str(imap_connections))
        self.saved_drag_format = drag_format
        self.drag_format.selectItemAtIndex_(MULTI_MESSAGE_FORMATS.index(drag_format))
        self.channel.selectItemAtIndex_(int(status.channel == "preview"))
        self.automatic.setState_(int(status.automatic_checks))
        self.saved = (status.channel, status.automatic_checks,
                      workers.preferences if workers.command_line is None else None, imap_connections)
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

    def worker_note(self) -> str:
        return self.workers.override_note or (
            f"1–{self.workers.maximum} (at most twice {self.workers.cpus} CPUs). "
            "Changes save automatically and apply when work starts or continues.")

    def commit(self) -> bool:
        """Persist valid controls, leaving invalid input and failed writes visible."""
        try:
            selection = (WorkerPreferences.from_input(WORKER_MODES[self.choices.selectedRow()],
                         int(self.count.stringValue()), self.workers.cpus) if self.workers.command_line is None else None)
            channel: UpdateChannel = "preview" if self.channel.indexOfSelectedItem() == 1 else "release"
            connections = validate_imap_connections(int(self.imap_count.stringValue()))
            value = channel, bool(self.automatic.state()), selection, connections
            if value != self.saved:
                self.save(*value)
                if self.saved is None or value[:2] != self.saved[:2]:
                    self.service.configure(channel, bool(self.automatic.state()))
                self.saved = value
            drag_format = MULTI_MESSAGE_FORMATS[max(0, self.drag_format.indexOfSelectedItem())]
            if self.save_drag_format is not None and drag_format != self.saved_drag_format:
                self.save_drag_format(drag_format)
                self.saved_drag_format = drag_format
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
            detail = "In-app updates need the installed app; this is a source checkout."
        self.detail.setStringValue_(f"Last checked: {checked}\n{detail}")
        self.check.setEnabled_(status.available and status.phase not in {"checking", "installing"})
