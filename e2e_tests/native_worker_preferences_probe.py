# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Run the real macOS preferences dialog against disposable application settings.
# Cocoa timers inspect and operate the same radio buttons and text field as users.
# Accepted, invalid and command-line-locked choices exercise persistence boundaries.
# The probe creates its own anchor window on the leftmost attached display.
# It captures only this synthetic dialog and closes its own windows on completion.
# No source archive, updater check, or personal preference file is opened.
"""Visible Cocoa worker-preference acceptance subprocess."""
from __future__ import annotations

import sys
import subprocess
import traceback
from importlib import import_module
from pathlib import Path
from typing import Any

from mailarchiver.application import ApplicationController, ApplicationPreferences, ApplicationPreferencesStore
from mailarchiver.gui_app import PyWebViewApplication, build_parser, configure_macos_application
from mailarchiver.worker_preferences import WorkerPreferences


def main() -> None:
    """Inspect native controls during the modal loop, then verify the persisted choice."""
    appkit, foundation = import_module("AppKit"), import_module("Foundation")
    native = appkit.NSApplication.sharedApplication()
    configure_macos_application()
    native.setActivationPolicy_(appkit.NSApplicationActivationPolicyRegular)
    screen = min(appkit.NSScreen.screens(), key=lambda item: item.frame().origin.x)
    area = screen.visibleFrame()
    anchor = appkit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        ((area.origin.x + 80, area.origin.y + 100), (500, 200)), appkit.NSWindowStyleMaskTitled,
        appkit.NSBackingStoreBuffered, False)
    anchor.setTitle_("ECT preference acceptance")
    anchor.makeKeyAndOrderFront_(None)
    native.activateIgnoringOtherApps_(True)
    store = ApplicationPreferencesStore(Path(sys.argv[1]))
    store.write(ApplicationPreferences(workers=WorkerPreferences(mode="custom", custom_count=1)))
    override = build_parser().parse_args(sys.argv[2:]).workers
    application = PyWebViewApplication(ApplicationController(store), workers_override=override)
    failures: list[str] = []
    attempts = ["locked"] if override is not None else ["all", "invalid", "custom"]

    def inspect(timer: Any) -> None:
        modal = native.modalWindow()
        if modal is None:
            return
        try:
            modal.setFrameOrigin_((area.origin.x + 120, area.origin.y + 150))
            assert modal.screen() == screen
            controls: dict[str, Any] = {}
            views = [modal.contentView()]
            while views:
                view = views.pop()
                views.extend(view.subviews())
                identifier = view.accessibilityIdentifier() if hasattr(view, "accessibilityIdentifier") else None
                if identifier:
                    controls[str(identifier)] = view
            choices, count, note = controls["worker-mode"], controls["worker-count"], controls["worker-note"]
            assert choices.numberOfRows() == 3
            step = attempts.pop(0)
            if step == "locked":
                assert not choices.isEnabled() and not count.isEnabled()
                assert str(note.stringValue()) == "# workers set to 2 on command line"
            else:
                assert choices.isEnabled() and count.isEnabled()
                choices.selectCellAtRow_column_(1 if step == "all" else 2, 0)
                assert choices.selectedRow() == (1 if step == "all" else 2)
                count.setStringValue_(str(application.worker_settings().maximum + 1) if step == "invalid" else "1")
                if step == "custom":
                    assert store.read().workers.mode == "all", "Invalid native input was persisted"
                    assert "Enter a whole number" in str(note.stringValue())
            if step in {"all", "locked"}:
                modal.displayIfNeeded()
                output = Path.cwd() / ".tmp/gui-previews" / f"native-worker-preferences-{step}.png"
                output.parent.mkdir(parents=True, exist_ok=True)
                subprocess.run(["/usr/sbin/screencapture", "-x", "-l", str(modal.windowNumber()), str(output)],
                               check=True, capture_output=True, timeout=5)
            if step != "invalid":
                timer.invalidate()
            native.stopModalWithCode_(1000)
        except Exception:  # pylint: disable=broad-exception-caught
            failures.append(traceback.format_exc())
            timer.invalidate()
            native.stopModalWithCode_(1000)

    try:
        for _ in range(1 if override is not None else 2):
            anchor.makeKeyAndOrderFront_(None)
            timer = foundation.NSTimer.timerWithTimeInterval_repeats_block_(0.1, True, inspect)
            foundation.NSRunLoop.mainRunLoop().addTimer_forMode_(timer, appkit.NSModalPanelRunLoopMode)
            application.show_update_preferences()
            assert not failures, failures
            assert not application.notices(), application.notices()
        assert not attempts
        assert store.read().workers == WorkerPreferences(mode="custom", custom_count=1)
        print("Native worker preferences passed", flush=True)
    finally:
        application.shutdown()
        anchor.orderOut_(None)


if __name__ == "__main__":
    main()
