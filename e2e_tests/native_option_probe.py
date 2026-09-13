"""Exercise Option sampling and the real Cocoa reopen delegate with disposable state.

Only the global hardware modifier source is substituted: unattended tests cannot
hold a physical key without sending input to the user's desktop. Native NSEvents
supply the flags; Cocoa windows, delegate dispatch, and controller stay real.
"""

import faulthandler
from importlib import import_module
import os
from pathlib import Path
from threading import Event
import traceback
from unittest.mock import patch

import webview

from mailarchiver.application import ApplicationController, ApplicationPreferencesStore
from mailarchiver.gui_app import (
    GUI_DIRECTORY, PyWebViewApplication, configure_macos_application,
    install_macos_document_events, macos_option_pressed,
)
from mailarchiver.loopback import LoopbackAssetServer


def main() -> None:
    """Option bypasses remembered/explicit archives and creates or restores one setup."""
    faulthandler.dump_traceback_later(45)
    appkit = import_module("AppKit")
    helper = import_module("PyObjCTools.AppHelper")
    fixture = Path(os.environ["MAILARCHIVER_SETUP_FIXTURE"])
    store = ApplicationPreferencesStore(fixture / "preferences.json")
    controller = ApplicationController(store)
    archive = controller.create_document(fixture / "Remembered.mailarchive")
    assert archive.path is not None
    preferences = store.path.read_bytes()
    native_event = appkit.NSEvent

    def event(flags: int):
        return native_event.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
            appkit.NSEventTypeApplicationDefined, (0, 0), flags, 0, 0, None, 0, 0, 0,
        )

    # Exercise the actual sampler before application configuration, as main does.
    assert macos_option_pressed() == bool(native_event.modifierFlags() & appkit.NSEventModifierFlagOption)
    for flags, expected in ((0, False), (appkit.NSEventModifierFlagShift, False),
                            (appkit.NSEventModifierFlagOption | appkit.NSEventModifierFlagShift, True)):
        with patch.object(appkit, "NSEvent", event(flags)):
            sampled = macos_option_pressed()
        assert sampled is expected
        startup = controller.startup((archive.path,), new=sampled)
        assert len(startup.windows) == 1 and not startup.errors
        session = startup.windows[0]
        assert controller.document(session.document_id).descriptor.untitled is expected
        controller.close_window(session.window_id)
    assert store.path.read_bytes() == preferences

    configure_macos_application()
    server = LoopbackAssetServer(GUI_DIRECTORY)
    application = PyWebViewApplication(controller, server)
    install_macos_document_events(application)
    application.create_about_window(hidden=True)
    about = webview.windows[0]
    failures: list[str] = []

    def reopen(flags: int) -> None:
        completed = Event()

        def dispatch() -> None:
            try:
                native = appkit.NSApplication.sharedApplication()
                with patch.object(appkit, "NSEvent", event(flags)):
                    assert native.delegate().applicationShouldHandleReopen_hasVisibleWindows_(native, False)
            except Exception:  # pylint: disable=broad-exception-caught
                failures.append(traceback.format_exc())
            finally:
                completed.set()
        helper.callAfter(dispatch)
        assert completed.wait(5)
        assert not failures, failures

    def probe() -> None:
        try:
            assert about.events.loaded.wait(10)
            reopen(0)
            assert len(webview.windows) == 1 and not application._setup_visible  # pylint: disable=protected-access
            reopen(appkit.NSEventModifierFlagOption)
            # A barrier on the native loop alone does not join the setup worker.
            for _ in range(100):
                if len(webview.windows) == 2:
                    break
                Event().wait(0.05)
            assert len(webview.windows) == 2
            setup = webview.windows[-1]
            assert setup.events.loaded.wait(10)
            assert setup.native is not None and setup.native.isVisible()
            setup.hide()
            reopen(appkit.NSEventModifierFlagOption | appkit.NSEventModifierFlagShift)
            for _ in range(100):
                if setup.native.isVisible():
                    break
                Event().wait(0.05)
            assert setup.native.isVisible() and len(webview.windows) == 2
            assert store.path.read_bytes() == preferences
        except Exception:  # pylint: disable=broad-exception-caught
            failures.append(traceback.format_exc())
        finally:
            application.shutdown()
            for window in tuple(webview.windows):
                window.destroy()

    webview.start(probe, http_server=False, private_mode=True, menu=application.menu())
    assert not failures, failures
    faulthandler.cancel_dump_traceback_later()
    print("Native Option startup and reopen passed")


if __name__ == "__main__":
    main()
