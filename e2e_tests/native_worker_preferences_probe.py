# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Run the real macOS preferences window against disposable application settings.
# Native actions operate the same radio buttons and field editor as users.
# Accepted, invalid and command-line-locked choices exercise persistence boundaries.
# The probe creates its own anchor window on the leftmost attached display.
# It captures only this synthetic dialog and closes its own windows on completion.
# No source archive, updater check, or personal preference file is opened.
"""Visible Cocoa worker-preference acceptance subprocess."""
from __future__ import annotations

import sys
from importlib import import_module
from pathlib import Path

from mailarchiver.application import ApplicationController, ApplicationPreferences, ApplicationPreferencesStore
from mailarchiver.gui_app import PyWebViewApplication, build_parser, configure_macos_application
from mailarchiver.worker_preferences import WorkerPreferences
from mailarchiver.macos_preferences import MacOSPreferences


def main() -> None:
    """Operate a real modeless window and verify traffic-light Close persists edits."""
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
    def pump() -> None:
        foundation.NSRunLoop.currentRunLoop().runUntilDate_(foundation.NSDate.dateWithTimeIntervalSinceNow_(0.3))

    def controls_for(window):
        controls, views = {}, [window.contentView()]
        while views:
            view = views.pop()
            views.extend(view.subviews())
            identifier = view.accessibilityIdentifier() if hasattr(view, "accessibilityIdentifier") else None
            if identifier:
                controls[str(identifier)] = view
            assert not (hasattr(view, "title") and str(view.title()) == "Done"), "Done button remains"
        return controls

    # Select the test display explicitly; activation policy may keep a standalone
    # subprocess from acquiring keyboard focus on the operator's desktop.
    application._preferences_window = MacOSPreferences(application.updates, application.save_preferences)
    application._preferences_window.screen = screen
    try:
        pump()
        application.show_update_preferences()
        pane = application._preferences_window
        assert pane is not None
        window = pane.window
        pump()
        assert native.modalWindow() is None, "Preferences still blocks the application"
        assert window.isVisible(), "Preferences did not open"
        assert window.screen() == screen
        close = window.standardWindowButton_(appkit.NSWindowCloseButton)
        assert close is not None and close.isEnabled()
        assert window.standardWindowButton_(appkit.NSWindowMiniaturizeButton).isEnabled()
        controls = controls_for(window)
        choices, count, note = controls["worker-mode"], controls["worker-count"], controls["worker-note"]
        assert choices.numberOfRows() == 3
        assert not controls["check-updates"].isEnabled()
        assert "source checkout" in str(controls["update-status"].stringValue())
        if override is not None:
            assert not choices.isEnabled() and not count.isEnabled()
            assert str(note.stringValue()) == "# workers set to 2 on command line"
        else:
            assert choices.isEnabled() and count.isEnabled()
            choices.selectCellAtRow_column_(1, 0)
            assert native.sendAction_to_from_("changed:", choices.target(), choices)
            assert store.read().workers.mode == "all", "Radio changes were not saved immediately"
        pump()
        output = Path.cwd() / ".tmp/gui-previews" / f"native-worker-preferences-{'locked' if override else 'all'}.png"
        output.parent.mkdir(parents=True, exist_ok=True)
        view = window.contentView().superview()
        bitmap = view.bitmapImageRepForCachingDisplayInRect_(view.bounds())
        view.cacheDisplayInRect_toBitmapImageRep_(view.bounds(), bitmap)
        data = bitmap.representationUsingType_properties_(appkit.NSBitmapImageFileTypePNG, {})
        assert data.writeToFile_atomically_(str(output), True)
        if override is None:
            choices.selectCellAtRow_column_(2, 0)
            count.setStringValue_(str(application.worker_settings().maximum + 1))
            close.performClick_(None)
            assert window.isVisible(), "Invalid custom input closed without feedback"
            assert store.read().workers.mode == "all", "Invalid custom input replaced saved settings"
            assert "Could not save preferences" in str(note.stringValue())
            # Leave a valid edit in Cocoa's field editor: Close must commit it.
            assert window.makeFirstResponder_(count)
            count.currentEditor().setString_("1")
        close.performClick_(None)
        pump()
        assert not window.isVisible() and anchor.isVisible()
        assert pane.timer is None
        assert store.read().workers == WorkerPreferences(mode="custom", custom_count=1)
        application.show_update_preferences()
        pump()
        assert application._preferences_window is pane and window.isVisible()
        assert str(count.stringValue()) == "1"
        window.standardWindowButton_(appkit.NSWindowCloseButton).performClick_(None)
        assert not window.isVisible()
        assert not application.notices(), application.notices()
        print("Native worker preferences passed", flush=True)
    finally:
        application.shutdown()
        anchor.orderOut_(None)


if __name__ == "__main__":
    main()
