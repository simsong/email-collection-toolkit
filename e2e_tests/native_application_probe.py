"""Exercise the production About and document bridges in a real Cocoa process.

Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""

import sys
from pathlib import Path
from threading import Event

import webview
from PyObjCTools import AppHelper
from AppKit import NSApplication, NSModalPanelRunLoopMode, NSTextView  # pylint: disable=no-name-in-module
from Foundation import NSRunLoop, NSTimer  # pylint: disable=no-name-in-module

from mailarchiver.application import ApplicationController, ApplicationPreferencesStore, IngestJob
from mailarchiver.gui_app import GUI_DIRECTORY, PyWebViewApplication, configure_macos_application, macos_import_picker, macos_owner_names
from mailarchiver.loopback import LoopbackAssetServer
from mailarchiver.document_options import DocumentOptions
from mailarchiver.writer_lock import WriterLease


def evaluate_async(window: webview.Window, expression: str):
    """Wait for the real JavaScript Promise result, not its serialized Promise object."""
    completed = Event()
    results = []

    def received(value):
        results.append(value)
        completed.set()

    window.evaluate_js(expression, callback=received)
    assert completed.wait(10), f"JavaScript request timed out: {expression}"
    return results[0]


def main() -> None:
    """Open a real extensionless fixture after About, inspect native menus, then quit."""
    configure_macos_application()
    server = LoopbackAssetServer(GUI_DIRECTORY)
    controller = ApplicationController(ApplicationPreferencesStore(Path(sys.argv[2])))
    application = PyWebViewApplication(controller, server)
    application.create_about_window()
    about = webview.windows[0]
    failures: list[str] = []

    def check_new_search(enabled: bool) -> None:
        done = Event()

        def inspect() -> None:
            try:
                menu = NSApplication.sharedApplication().mainMenu()
                assert menu.itemWithTitle_("File").submenu().itemWithTitle_("New Search Window") is None
                item = menu.itemWithTitle_("Window").submenu().itemWithTitle_("New Search Window")
                assert item is not None and bool(item.isEnabled()) == enabled
            except Exception as error:  # pylint: disable=broad-exception-caught
                failures.append(str(error))
            finally:
                done.set()

        application._refresh_menus()  # pylint: disable=protected-access
        AppHelper.callAfter(inspect)
        assert done.wait(5), "New Search Window menu inspection timed out"

    def probe() -> None:
        try:
            assert about.events.loaded.wait(10), "About bridge failed to load"
            status = evaluate_async(about, "window.pywebview.api.status()")
            assert status["metadata"]["version"], status
            assert status["disk_free_bytes"] > 0, status
            assert about.evaluate_js("Object.keys(window.pywebview.api)") == ["status"]
            application.add_notice("warning", "Native About regression check")
            evaluate_async(about, "refresh().then(() => true)")
            assert "Native About regression check" in about.evaluate_js("document.getElementById('notices').textContent")
            assert about.evaluate_js("document.getElementById('error').hidden")
            check_new_search(False)
            api = application.open_document(Path(sys.argv[1]))
            assert api.window.events.loaded.wait(10), "Document bridge failed to load"
            assert evaluate_async(api.window, "window.pywebview.api.status()")['message_count'] == 1
            assert api.window.evaluate_js("document.getElementById('choose-archive') === null")
            check_new_search(True)
            application._refresh_menus()  # pylint: disable=protected-access
            done = Event()

            def inspect_menu() -> None:
                try:
                    menu = NSApplication.sharedApplication().mainMenu()
                    titles = [item.title() for item in menu.itemArray()]
                    assert titles[1:5] == ["File", "Edit", "View", "Window"], titles
                    file_menu = menu.itemWithTitle_("File").submenu()
                    assert file_menu.itemWithTitle_("Open…").keyEquivalent() == "o"
                except Exception as error:  # pylint: disable=broad-exception-caught
                    failures.append(str(error))
                finally:
                    done.set()

            AppHelper.callAfter(inspect_menu)
            assert done.wait(5), "Native menu inspection timed out"
            # GUI requirement: one source picker accepts both files and directories.
            def inspect_import_picker(timer) -> None:
                timer.invalidate()
                app = NSApplication.sharedApplication()
                modal = app.modalWindow()
                try:
                    assert modal is not None, "Import picker did not appear"
                    assert api.document.display_path.name in modal.title()
                    assert str(api.document.display_path) in modal.message()
                    assert modal.prompt() == "Import"
                    assert modal.canChooseDirectories() and modal.canChooseFiles()
                    assert modal.allowsMultipleSelection()
                except Exception as error:  # pylint: disable=broad-exception-caught
                    failures.append(str(error))
                finally:
                    app.stopModalWithCode_(0)

            def schedule_inspection() -> None:
                timer = NSTimer.timerWithTimeInterval_repeats_block_(0.1, False, inspect_import_picker)
                NSRunLoop.mainRunLoop().addTimer_forMode_(timer, NSModalPanelRunLoopMode)

            AppHelper.callAfter(schedule_inspection)
            assert not application._import_document(api)  # pylint: disable=protected-access
            assert api.document.ingest_job is None, "Cancel must not start ingest"

            def accept_directory(timer) -> None:
                timer.invalidate()
                panel = NSApplication.sharedApplication().modalWindow()
                try:
                    assert panel is not None, "Directory picker did not appear"
                    assert panel.prompt() == "Import"
                    assert panel.canChooseDirectories() and panel.canChooseFiles()
                    assert Path(panel.directoryURL().path()) == Path(sys.argv[1])
                    NSApplication.sharedApplication().stopModalWithCode_(1)
                except Exception as error:  # pylint: disable=broad-exception-caught
                    failures.append(str(error))
                    NSApplication.sharedApplication().stopModalWithCode_(0)

            def schedule_directory_selection() -> None:
                timer = NSTimer.timerWithTimeInterval_repeats_block_(0.2, False, accept_directory)
                NSRunLoop.mainRunLoop().addTimer_forMode_(timer, NSModalPanelRunLoopMode)

            AppHelper.callAfter(schedule_directory_selection)
            selected = macos_import_picker(
                Path(sys.argv[1]), "Import directory test", "Select this entire directory",
                "Import", folders=True, multiple=True,
            )
            assert len(selected) == 1 and selected[0].samefile(Path(sys.argv[1])), selected

            # GUI requirement: multiline owner entry replaces a second file picker.
            for accept in (False, True):
                def enter_owner_names(timer) -> None:
                    timer.invalidate()
                    app = NSApplication.sharedApplication()
                    modal = app.modalWindow()
                    try:
                        assert modal is not None and "Owner names for" in modal.title()
                        views = [modal.contentView()]
                        editors = []
                        while views:
                            view = views.pop()
                            views.extend(view.subviews())
                            if isinstance(view, NSTextView) and view.isEditable():
                                editors.append(view)
                        assert len(editors) == 1, "Expected one multiline owner editor"
                        editors[0].setString_(" José Example \n jose@example.org \n")
                    except Exception as error:  # pylint: disable=broad-exception-caught
                        failures.append(str(error))
                    finally:
                        app.stopModalWithCode_(1000 if accept else 1001)

                def schedule_owner_entry() -> None:
                    timer = NSTimer.timerWithTimeInterval_repeats_block_(0.1, False, enter_owner_names)
                    NSRunLoop.mainRunLoop().addTimer_forMode_(timer, NSModalPanelRunLoopMode)

                AppHelper.callAfter(schedule_owner_entry)
                entered = macos_owner_names(api.document.display_path)
                assert entered == ("jose@example.org\nJosé Example\n" if accept else None)
                assert not (api.document.path / "owner-names.txt").exists(), "Entry alone must not save"
            assert application.open_document_options(api.document)
            options = next(window for window in webview.windows if " — Document Options — " in window.title)
            assert options.events.loaded.wait(10), "Options bridge failed to load"
            evaluate_async(options, "refreshOptions().then(() => true)")
            options.evaluate_js("document.getElementById('add').click()")
            assert not options.evaluate_js("document.getElementById('add-form').hidden")
            evaluate_async(options, "updateOptions('Zed;alice@example.org, Bob zed', []).then(() => true)")
            assert options.evaluate_js("Array.from(document.getElementById('owners').options, o => o.value)") == ["alice@example.org", "Bob", "Zed"]
            store = DocumentOptions(api.document.path)
            owner_lease = WriterLease.acquire(api.document.path, api.document.descriptor.identity, "test", "options", "test")
            try:
                store.record_import(store.state().names, owner_lease)
            finally:
                owner_lease.release()
            evaluate_async(options, "updateOptions('', ['Bob', 'Zed']).then(() => true)")
            assert not options.evaluate_js("document.getElementById('changed').hidden")
            assert store.state().names == ["alice@example.org"]
            options.destroy()
            assert options.events.closed.wait(5)
            assert application.open_document_options(api.document)
            options = next(window for window in webview.windows if " — Document Options — " in window.title)
            assert options.events.loaded.wait(10)
            evaluate_async(options, "refreshOptions().then(() => true)")
            assert not options.evaluate_js("document.getElementById('changed').hidden")
            assert application.open_ingest_window(api.document)
            ingest = next(window for window in webview.windows if " — Ingests — " in window.title)
            assert ingest.events.loaded.wait(10), "Ingest bridge failed to load"
            evaluate_async(ingest, "refreshHistory().then(() => true)")
            assert ingest.evaluate_js("document.getElementById('import-directory').textContent") == "Import Directory…"
            assert not ingest.evaluate_js("document.getElementById('import-directory').disabled")
            document_id = api.document.descriptor.document_id
            lease = WriterLease.acquire(api.document.path, api.document.descriptor.identity, "test", "button-test", "test")
            controller.begin_ingest(document_id, IngestJob(operation_id="button-test", owner_window_id=api.search_window.window_id), lease)
            try:
                evaluate_async(options, "refreshOptions().then(() => true)")
                assert options.evaluate_js("document.getElementById('add').disabled")
                evaluate_async(ingest, "refreshHistory().then(() => true)")
                assert ingest.evaluate_js("document.getElementById('import-directory').disabled")
                assert not evaluate_async(ingest, "window.pywebview.api.import_directory()")
            finally:
                controller.finish_ingest(document_id, "button-test", published=False)
            evaluate_async(ingest, "refreshHistory().then(() => true)")
            assert not ingest.evaluate_js("document.getElementById('import-directory').disabled")
        except Exception as error:  # pylint: disable=broad-exception-caught
            failures.append(str(error))
        finally:
            for window in list(webview.windows):
                if window is not about:
                    window.destroy()
                    window.events.closed.wait(3)
            about.destroy()

    try:
        webview.start(probe, private_mode=True, http_server=False, menu=application.menu())
    finally:
        application.shutdown()
    assert not failures, failures
    print("Production About, document bridge, and menus passed")


if __name__ == "__main__":
    main()
