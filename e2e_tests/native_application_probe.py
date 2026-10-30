# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Exercise the production About and document bridges in a real Cocoa process.

Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""

import os
import json
import hashlib
import sqlite3
import sys
import traceback
from importlib import import_module
from pathlib import Path
from threading import Event, enumerate as active_threads
from time import monotonic, sleep

import webview

from mailarchiver.application import ApplicationController, ApplicationPreferencesStore, IngestJob
from mailarchiver.clamav_definitions import UPDATE_ENV, UPDATER_ENV
from mailarchiver.gui_app import GUI_DIRECTORY, PyWebViewApplication, configure_macos_application, install_macos_document_events, macos_import_picker, macos_owner_rules
from mailarchiver.loopback import LoopbackAssetServer
from mailarchiver.identity import APPLICATION_NAME
from mailarchiver.owner_rules import OwnerRules
from mailarchiver.document_options import DocumentOptions
from mailarchiver.writer_lock import WriterLease
from mailarchiver.imap_credentials import password_for
from mailarchiver.imap_models import ImapAccount
from mailarchiver.__main__ import IngestRequest, run_ingest
from mailarchiver.ingest_status import read_ingest_history

AppHelper = import_module("PyObjCTools.AppHelper")
NSApplication = import_module("AppKit").NSApplication
NSRunningApplication = import_module("AppKit").NSRunningApplication
NSModalPanelRunLoopMode = import_module("AppKit").NSModalPanelRunLoopMode
NSTextView = import_module("AppKit").NSTextView
NSRunLoop = import_module("Foundation").NSRunLoop
NSTimer = import_module("Foundation").NSTimer
PROBE_ARCHIVE_ENV = "MAILARCHIVER_NATIVE_PROBE_ARCHIVE"
PROBE_PREFERENCES_ENV = "MAILARCHIVER_NATIVE_PROBE_PREFERENCES"


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


def drain_bridge_calls() -> bool:
    """Keep Cocoa alive until fixture requests and their chained JS returns settle."""
    deadline, idle = monotonic() + 5, False
    while monotonic() < deadline:
        calls = [thread for thread in active_threads() if thread.name.endswith(" (_call)")]
        if not calls and idle:
            return True
        idle = not calls
        for thread in calls:
            thread.join(timeout=max(0, deadline - monotonic()))
        sleep(0.02)
    return False


def close_window(window: webview.Window) -> None:
    """Finish bridge replies before destroying the WKWebView that receives them."""
    assert drain_bridge_calls(), "Native bridge callback did not finish before window closure"
    window.destroy()
    assert window.events.closed.wait(5)


def check_antivirus_menu(application: PyWebViewApplication, about: webview.Window) -> None:
    """Desktop: native menu dispatch runs the shared updater and displays retained-data failure."""
    root = Path(os.environ[PROBE_PREFERENCES_ENV]).parent
    settings = {UPDATE_ENV: str(root / "antivirus-updates"), UPDATER_ENV: str(root / "missing-freshclam")}
    saved = {key: os.environ.get(key) for key in settings}
    os.environ.update(settings)
    handled, errors = Event(), []

    def invoke() -> None:
        try:
            native = NSApplication.sharedApplication()
            menu = native.mainMenu().itemAtIndex_(0).submenu()
            titles = [item.title() for item in menu.itemArray()]
            index = titles.index("Check for Program Updates")
            assert titles[index + 1] == "Check for Anti-Virus Updates", titles
            assert "Check for Updates…" not in titles, titles
            item = menu.itemAtIndex_(index + 1)
            for _ in range(8):
                assert native.sendAction_to_from_(item.action(), item.target(), item)
        except Exception:  # pylint: disable=broad-exception-caught
            errors.append(traceback.format_exc())
        finally:
            handled.set()

    try:
        AppHelper.callAfter(invoke)
        assert handled.wait(5) and not errors, errors
        assert evaluate_async(about, """(async () => {
            const button = document.getElementById('update-definitions');
            const status = document.getElementById('definition-update-status');
            const deadline = Date.now() + 5000;
            while (!status.textContent.includes('Update failed') && Date.now() < deadline)
                await new Promise(resolve => setTimeout(resolve, 20));
            return button.textContent === 'Check for Anti-Virus Updates' && !button.disabled &&
                status.textContent.includes('Existing definitions were retained.');
        })()""") is True
        assert application._about_visible  # pylint: disable=protected-access
        assert webview.windows == [about], "Anti-virus command duplicated About"
        assert not (root / "antivirus-updates/active.json").exists()
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def check_reprocess_menu(application: PyWebViewApplication, api) -> None:
    """Curator confirmation alone starts replay; Cancel preserves jobs and search data."""
    document = api.document
    assert document is not None and document.path is not None
    archive = document.path
    canonical = {}
    for path in archive.rglob("*.mbox"):
        with path.open("rb") as source:
            canonical[path] = hashlib.file_digest(source, "sha256").hexdigest()
    with sqlite3.connect(archive / "processing.sqlite3") as database:
        jobs = database.execute("SELECT * FROM jobs ORDER BY job_id").fetchall()
        invocations = database.execute("SELECT kind,count(*) FROM invocations GROUP BY kind ORDER BY kind").fetchall()
        saved_request = database.execute("SELECT value FROM processing_settings WHERE name='request'").fetchone()[0]
    with sqlite3.connect(archive / "search.sqlite3") as database:
        indexed = database.execute("SELECT * FROM message_fts").fetchall()
    for accepted in (False, True):
        handled, triggered = Event(), Event()
        errors = []
        before = len(read_ingest_history(archive).statuses)

        def answer(timer) -> None:
            modal = NSApplication.sharedApplication().modalWindow()
            if modal is None:
                return
            timer.invalidate()
            try:
                assert modal.title() == "Reprocess saved mail?"
                views, texts = [modal.contentView()], []
                while views:
                    view = views.pop()
                    views.extend(view.subviews())
                    if hasattr(view, "stringValue"):
                        texts.append(str(view.stringValue()))
                assert "very long time" in "\n".join(texts)
                assert "No mail will be downloaded" in "\n".join(texts)
            except Exception:  # pylint: disable=broad-exception-caught
                errors.append(traceback.format_exc())
            finally:
                NSApplication.sharedApplication().stopModalWithCode_(1000 if accepted else 1001)
                handled.set()

        def invoke() -> None:
            try:
                native = NSApplication.sharedApplication()
                native.activateIgnoringOtherApps_(True)
                api.window.native.makeKeyAndOrderFront_(None)
                timer = NSTimer.timerWithTimeInterval_repeats_block_(0.05, True, answer)
                NSRunLoop.mainRunLoop().addTimer_forMode_(timer, NSModalPanelRunLoopMode)
                item = native.mainMenu().itemWithTitle_("File").submenu().itemWithTitle_("Reprocess Saved Mail…")
                assert item is not None and item.isEnabled(), "Reprocessing menu is missing"
                assert native.sendAction_to_from_(item.action(), item.target(), item)
            except Exception:  # pylint: disable=broad-exception-caught
                errors.append(traceback.format_exc())
            finally:
                triggered.set()

        AppHelper.callAfter(invoke)
        assert triggered.wait(3) and not errors, errors
        assert handled.wait(5) and not errors, errors
        if accepted:
            deadline = monotonic() + 10
            history = read_ingest_history(archive).statuses
            while monotonic() < deadline:
                history = read_ingest_history(archive).statuses
                if len(history) > before and history[0].state == "completed":
                    break
                sleep(0.02)
            assert len(history) == before + 1 and history[0].state == "completed", history
            assert not history[0].source_roots, "Replay must not download mail"
        else:
            assert document.ingest_job is None
            with sqlite3.connect(archive / "processing.sqlite3") as database:
                assert database.execute("SELECT * FROM jobs ORDER BY job_id").fetchall() == jobs
            with sqlite3.connect(archive / "search.sqlite3") as database:
                assert database.execute("SELECT * FROM message_fts").fetchall() == indexed
    with sqlite3.connect(archive / "processing.sqlite3") as database:
        after = database.execute("SELECT kind,count(*) FROM invocations GROUP BY kind ORDER BY kind").fetchall()
        assert dict(after)["headers"] > dict(invocations)["headers"]
        assert dict(after)["clamav"] == dict(invocations)["clamav"]
        assert dict(after)["file-message"] == dict(invocations)["file-message"]
        assert database.execute("SELECT value FROM processing_settings WHERE name='request'").fetchone()[0] == saved_request
    for path, digest in canonical.items():
        with path.open("rb") as source:
            assert hashlib.file_digest(source, "sha256").hexdigest() == digest


def main() -> None:
    """Open a real extensionless fixture after About, inspect native menus, then quit."""
    configure_macos_application()
    assert NSRunningApplication.currentApplication().localizedName() == APPLICATION_NAME, "macOS still identifies the GUI as Python"
    # Query only a synthetic missing item: no personal credential or vault write.
    missing_account = ImapAccount(server="native-fixture.example.test", username="missing-keychain-fixture")
    try:
        password_for(missing_account)
    except ValueError as error:
        assert "No saved password" in str(error), str(error)
    else:
        raise AssertionError("Unexpected credential for randomly identified native fixture")
    archive = Path(os.environ[PROBE_ARCHIVE_ENV])
    server = LoopbackAssetServer(GUI_DIRECTORY)
    controller = ApplicationController(ApplicationPreferencesStore(Path(os.environ[PROBE_PREFERENCES_ENV])))
    application = PyWebViewApplication(controller, server)
    install_macos_document_events(application)
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
            if "--reprocess-only" in sys.argv:
                document = controller.open_document(archive)
                api = application.create_search_window(controller.new_search_window(document))
                assert api.window.events.loaded.wait(10), "Reprocess search failed to load"
                check_reprocess_menu(application, api)
                print("Curator reprocessing menu passed")
                return
            status = evaluate_async(about, "window.pywebview.api.status()")
            assert status["metadata"]["version"], status
            assert status["metadata"]["build"]["git_commit"], status
            assert status["disk_free_bytes"] > 0, status
            assert not [notice for notice in status["notices"] if notice["severity"] == "error"], status
            assert about.evaluate_js("Object.keys(window.pywebview.api)") == ["show_processor_graph", "status", "update_definitions"]
            application.add_notice("warning", "Native About regression check")
            evaluate_async(about, "refresh().then(() => true)")
            assert status["metadata"]["build"]["git_commit"] in about.evaluate_js("document.getElementById('git-commit').textContent")
            assert "Native About regression check" in about.evaluate_js("document.getElementById('notices').textContent")
            assert about.evaluate_js("document.getElementById('error').hidden")
            # Processing requirement: About opens a separate offline DAG window.
            about.evaluate_js("document.getElementById('show-processor-graph').click()")
            deadline = monotonic() + 5
            while len(webview.windows) < 2 and monotonic() < deadline:
                sleep(0.02)
            graph = next(window for window in webview.windows if window is not about)
            assert graph.events.loaded.wait(10), "Processor graph window failed to load"
            assert evaluate_async(graph, """(async () => {
                const deadline = Date.now() + 5000;
                while (!document.querySelector('svg .node') && Date.now() < deadline)
                    await new Promise(resolve => setTimeout(resolve, 20));
                if (document.querySelectorAll('svg').length !== 3 || !document.getElementById('error').hidden)
                    throw new Error('Native DAG did not render all three stages');
                return true;
            })()""") is True
            assert any(item.title == "Processor graph" for item in application.window_menu_items())
            assert evaluate_async(about, "window.pywebview.api.show_processor_graph()") is True
            assert len(webview.windows) == 2, "Graph command created duplicate windows"
            close_window(graph)
            assert not any(item.title == "Processor graph" for item in application.window_menu_items())
            check_new_search(False)
            # Closing the sole visible window must keep menus alive, without
            # status polling or Dock activation reopening About.
            native_about = about.native
            assert native_about is not None
            AppHelper.callAfter(native_about.performClose_, None)
            application.add_notice("warning", "Notice while About is dismissed")
            evaluate_async(about, "refresh().then(() => true)")
            dismissed = Event()

            def inspect_dismissed() -> None:
                try:
                    native = NSApplication.sharedApplication()
                    assert not native_about.isVisible(), "Dismissed About is still visible"
                    assert not about.events.closed.is_set(), "About was destroyed instead of hidden"
                    assert webview.windows == [about], f"Unexpected windows: {[window.title for window in webview.windows]}"
                    assert application.window_menu_items() == [], "Hidden About remains in Window menu"
                    native.delegate().applicationShouldHandleReopen_hasVisibleWindows_(native, False)
                    assert not native_about.isVisible(), "Dock activation reopened About"
                    menu = native.mainMenu()
                    assert menu.itemWithTitle_("File").submenu().itemWithTitle_("Open…").isEnabled(), "File/Open is disabled"
                    item = menu.itemAtIndex_(0).submenu().itemAtIndex_(0)
                    assert native.sendAction_to_from_(item.action(), item.target(), item), f"About action failed: {item.action()!r}"
                except Exception as error:  # pylint: disable=broad-exception-caught
                    failures.append(str(error))
                finally:
                    dismissed.set()

            AppHelper.callLater(1.2, inspect_dismissed)
            assert dismissed.wait(5), "About dismissal check timed out"
            reopened = Event()

            def inspect_reopened() -> None:
                try:
                    assert native_about.isVisible(), "About menu did not restore the window"
                    assert webview.windows == [about], "About must restore the retained window"
                except Exception as error:  # pylint: disable=broad-exception-caught
                    failures.append(str(error))
                finally:
                    reopened.set()

            AppHelper.callLater(0.3, inspect_reopened)
            assert reopened.wait(5), "About menu reopening check timed out"
            check_antivirus_menu(application, about)
            # Disabling command-line reinterpretation must retain genuine Finder opens.
            native = NSApplication.sharedApplication()
            AppHelper.callAfter(native.delegate().application_openFiles_, native, [str(archive)])
            deadline = monotonic() + 10
            api = application.active_api()
            while api is None and monotonic() < deadline:
                sleep(0.01)
                api = application.active_api()
            assert api is not None, "Finder document-open event did not open the archive"
            document = api.document
            assert document is not None and document.path is not None and document.display_path is not None
            assert api.search_window is not None
            assert api.window.events.loaded.wait(10), "Document bridge failed to load"
            assert evaluate_async(api.window, "window.pywebview.api.status()")['message_count'] == 1
            assert api.window.evaluate_js("document.getElementById('choose-archive') === null")
            check_new_search(True)
            # Picker navigation must create a real search window bound to its archive.
            picker_root = Path(os.environ[PROBE_PREFERENCES_ENV]).parent / "picker-fixture"
            picker_root.mkdir()
            picker_source = picker_root / "messages"
            picker_source.mkdir()
            for index in range(11):
                sender = "Native Picker <picker@example.test>" if index < 10 else "Other Person <other@example.test>"
                subject = f"Picker navigation {index}" if index < 10 else "Unrelated message"
                (picker_source / f"{index}.eml").write_bytes((f"From: {sender}\nTo: owner@example.test\n"
                    f"Date: Tue, 02 Jan 2024 10:00:00 +0000\nMessage-ID: <native-picker-{index}@example.test>\n"
                    f"Subject: {subject}\n\nSynthetic navigation fixture.\n-- \nNative Picker\n"
                    "+1 (617) 555-0102\n+44 20 7946 0958\n").encode())
            picker_archive = picker_root / "archive"
            run_ingest(IngestRequest(archive=picker_archive, roots=[str(picker_source)], scan_policy="not-scanned",
                owner_rules=OwnerRules(include=["owner@example.test"]), continue_content=True), terminal=False)
            pending_source = picker_root / "pending.eml"
            pending_source.write_bytes(b"From: Other Person <other@example.test>\nTo: owner@example.test\n"
                b"Date: Tue, 02 Jan 2024 10:00:00 +0000\nMessage-ID: <deferred-native@example.test>\n"
                b"Subject: Deferred content\n\nPending fixture.\n")
            run_ingest(IngestRequest(archive=picker_archive, roots=[str(pending_source)], scan_policy="not-scanned",
                owner_rules=OwnerRules(include=["owner@example.test"]), continue_content=False), terminal=False)
            picker_document = controller.open_document(picker_archive)
            assert application.open_picker(picker_document, "name")
            picker = next(window for window in webview.windows if " — Name matcher — " in window.title)
            assert picker.events.loaded.wait(10), "Name picker bridge failed to load"
            evaluate_async(picker, """(async () => {
                const deadline = Date.now() + 5000;
                while (!window.archivePicker?.model.groups.length && Date.now() < deadline)
                    await new Promise(resolve => setTimeout(resolve, 20));
                if (!window.archivePicker?.model.groups.length) throw new Error('No picker identities');
                return true;
            })()""")
            picker.evaluate_js("""window.targetGroup = [...document.querySelectorAll('tr.group')].find(
                row => row.querySelector('.row-label').textContent === 'Native Picker');
                window.otherGroup = [...document.querySelectorAll('tr.group')].find(
                row => row.querySelector('.row-label').textContent === 'Other Person');""")
            assert picker.evaluate_js("window.targetGroup.querySelector('.phone-numbers summary').textContent") == "2 phone numbers"
            picker.evaluate_js("window.targetGroup.querySelector('.phone-numbers summary').click()")
            assert picker.evaluate_js("window.targetGroup.querySelector('.phone-numbers').open")
            # Desktop requirement: a count targets its own person/address, not the selected row.
            for selector, event in ((".row-label", "dblclick"), (".message-count", "click"), ("address", "click")):
                picker.evaluate_js("window.otherGroup.querySelector('.row-label').click()")
                before = {window.uid for window in webview.windows}
                target = "window.targetGroup.nextElementSibling.querySelector('.message-count')" if selector == "address" else f"window.targetGroup.querySelector('{selector}')"
                picker.evaluate_js(f"{target}.dispatchEvent(new MouseEvent('{event}', {{bubbles:true}}))")
                deadline = monotonic() + 10
                while not any(window.uid not in before for window in webview.windows) and monotonic() < deadline:
                    sleep(0.02)
                opened = next(window for window in webview.windows if window.uid not in before)
                assert opened.events.loaded.wait(10), "Picker search failed to load"
                assert evaluate_async(opened, "window.pywebview.api.status()")['document_id'] == picker_document.descriptor.document_id
                assert evaluate_async(opened, """(async () => {
                    const deadline = Date.now() + 5000;
                    while (document.getElementById('result-status').textContent !== '10 messages' && Date.now() < deadline)
                        await new Promise(resolve => setTimeout(resolve, 20));
                    const subjects = [...document.querySelectorAll('.result-subject')];
                    if (subjects.length !== 10 || subjects.some(row => !row.textContent.startsWith('Picker navigation ')))
                        throw new Error('Picker search did not return exactly the clicked identity’s ten messages');
                    return true;
                })()""") is True
                assert not opened.evaluate_js("document.getElementById('processing-dialog').open"), "Recovery interrupted count navigation"
                close_window(opened)
            close_window(picker)
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
                    assert document.display_path is not None
                    assert document.display_path.name in modal.title()
                    assert str(document.display_path) in modal.message()
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
            assert document.ingest_job is None, "Cancel must not start ingest"

            def accept_directory(timer) -> None:
                timer.invalidate()
                panel = NSApplication.sharedApplication().modalWindow()
                try:
                    assert panel is not None, "Directory picker did not appear"
                    assert panel.prompt() == "Import"
                    assert panel.canChooseDirectories() and panel.canChooseFiles()
                    assert Path(panel.directoryURL().path()) == archive
                    NSApplication.sharedApplication().stopModalWithCode_(1)
                except Exception as error:  # pylint: disable=broad-exception-caught
                    failures.append(str(error))
                    NSApplication.sharedApplication().stopModalWithCode_(0)

            def schedule_directory_selection() -> None:
                timer = NSTimer.timerWithTimeInterval_repeats_block_(0.2, False, accept_directory)
                NSRunLoop.mainRunLoop().addTimer_forMode_(timer, NSModalPanelRunLoopMode)

            AppHelper.callAfter(schedule_directory_selection)
            selected = macos_import_picker(
                archive, "Import directory test", "Select this entire directory",
                "Import", folders=True, multiple=True,
            )
            assert len(selected) == 1 and selected[0].samefile(archive), selected

            # GUI requirement: multiline owner entry replaces a second file picker.
            for accept in (False, True):
                def enter_owner_names(timer) -> None:
                    timer.invalidate()
                    app = NSApplication.sharedApplication()
                    modal = app.modalWindow()
                    try:
                        assert modal is not None and "Owner emails for" in modal.title()
                        views = [modal.contentView()]
                        editors = []
                        while views:
                            view = views.pop()
                            views.extend(view.subviews())
                            if isinstance(view, NSTextView) and view.isEditable():
                                editors.append(view)
                        assert len(editors) == 2, "Expected include and exclude editors"
                        for editor in editors:
                            if editor.identifier() == "owner-include":
                                assert editor.string() == "slg"
                                editor.setString_("*Simson*\nslg")
                            elif editor.identifier() == "owner-exclude":
                                assert editor.string() == "*david*"
                                editor.setString_("*David*")
                            else:
                                raise AssertionError("Unexpected owner editor")
                    except Exception as error:  # pylint: disable=broad-exception-caught
                        failures.append(str(error))
                    finally:
                        app.stopModalWithCode_(1000 if accept else 1001)

                def schedule_owner_entry() -> None:
                    timer = NSTimer.timerWithTimeInterval_repeats_block_(0.1, False, enter_owner_names)
                    NSRunLoop.mainRunLoop().addTimer_forMode_(timer, NSModalPanelRunLoopMode)

                AppHelper.callAfter(schedule_owner_entry)
                entered = macos_owner_rules(document.display_path, OwnerRules(include=["slg"], exclude=["*david*"]))
                assert entered == (OwnerRules(include=["*simson*", "slg"], exclude=["*david*"]) if accept else None)
                assert not (document.path / "owner-names.txt").exists(), "Entry alone must not save"
            # A personal list editor is a standalone window: Command-W must be
            # enabled even though it is not registered as an archive child.
            assert application.open_default_lists(personal=True)
            lists = next(window for window in webview.windows if window.title.endswith(" — Customizable Default Lists"))
            assert lists.events.loaded.wait(10), "List editor bridge failed to load"
            evaluate_async(lists, "(async () => { while(document.getElementById('save').disabled) "
                "await new Promise(resolve => setTimeout(resolve, 10)); return true; })()")
            assert lists.evaluate_js("document.getElementById('list').options.length") == 8
            native_lists = lists.native
            assert native_lists is not None
            focused_list = Event()
            focus_deadline = monotonic() + 3

            def wait_for_list_focus() -> None:
                if webview.active_window() == lists:
                    focused_list.set()
                elif monotonic() < focus_deadline:
                    AppHelper.callLater(0.02, wait_for_list_focus)

            def focus_list() -> None:
                NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
                native_lists.makeKeyAndOrderFront_(None)
                wait_for_list_focus()

            AppHelper.callAfter(focus_list)
            assert focused_list.wait(5)
            closed_menu = Event()

            def check_list_close() -> None:
                try:
                    item = NSApplication.sharedApplication().mainMenu().itemWithTitle_("File").submenu().itemWithTitle_("Close")
                    assert item.isEnabled() and item.keyEquivalent() == "w"
                    assert webview.active_window() == lists, (
                        "List editor lost native focus", webview.active_window(),
                        [(window.uid, window.title) for window in webview.windows],
                    )
                except Exception as error:  # pylint: disable=broad-exception-caught
                    failures.append(str(error))
                finally:
                    closed_menu.set()

            application._refresh_menus()  # pylint: disable=protected-access
            AppHelper.callAfter(check_list_close)
            assert closed_menu.wait(5)
            assert drain_bridge_calls()
            assert application.close_active_window()
            assert lists.events.closed.wait(5)
            assert application.open_default_lists(api.document)
            lists = next(window for window in webview.windows if " — Customizable Default Lists — " in window.title)
            assert lists.events.loaded.wait(10)
            evaluate_async(lists, "(async () => { while(document.getElementById('save').disabled) "
                "await new Promise(resolve => setTimeout(resolve, 10)); return true; })()")
            lists.evaluate_js("document.getElementById('list').value = 'reply-addresses'; document.getElementById('scope').value = 'archive'")
            evaluate_async(lists, "load().then(() => true)")
            lists.evaluate_js("document.getElementById('yaml').value = " + json.dumps("version: 1\nrules: []\n"))
            evaluate_async(lists, "save(false).then(() => true)")
            assert lists.evaluate_js("document.getElementById('status').textContent") == "Saved.", lists.evaluate_js("document.getElementById('error').textContent")
            assert lists.evaluate_js("document.getElementById('origin').textContent").startswith("Source: archive database")
            close_window(lists)
            assert application.open_document_options(api.document)
            options = next(window for window in webview.windows if " — Document Options — " in window.title)
            assert options.events.loaded.wait(10), "Options bridge failed to load"
            evaluate_async(options, "refreshOptions().then(() => true)")
            options.evaluate_js("document.getElementById('owner-include').value = '*Simson*, SLG'; document.getElementById('owner-exclude').value = '*David*'")
            evaluate_async(options, "saveOptions().then(() => true)")
            assert options.evaluate_js("document.getElementById('owner-include').value") == "*simson*\nslg"
            assert options.evaluate_js("document.getElementById('owner-exclude').value") == "*david*"
            store = DocumentOptions(document.path)
            owner_lease = WriterLease.acquire(document.path, document.descriptor.identity, "test", "options", "test")
            try:
                store.record_import(store.defaults(), owner_lease)
            finally:
                owner_lease.release()
            options.evaluate_js("document.getElementById('owner-include').value = 'slg'")
            evaluate_async(options, "saveOptions().then(() => true)")
            assert not options.evaluate_js("document.getElementById('changed').hidden")
            assert store.state().include == ["slg"]
            assert store.state().exclude == ["*david*"]
            close_window(options)
            assert application.open_document_options(api.document)
            options = next(window for window in webview.windows if " — Document Options — " in window.title)
            assert options.events.loaded.wait(10)
            evaluate_async(options, "refreshOptions().then(() => true)")
            assert not options.evaluate_js("document.getElementById('changed').hidden")
            assert application.open_ingest_window(document)
            ingest = next(window for window in webview.windows if " — Ingests — " in window.title)
            assert ingest.events.loaded.wait(10), "Ingest bridge failed to load"
            evaluate_async(ingest, "refreshHistory().then(() => true)")
            assert ingest.evaluate_js("document.getElementById('import-directory').textContent") == "Import Directory…"
            assert not ingest.evaluate_js("document.getElementById('import-directory').disabled")
            document_id = document.descriptor.document_id
            lease = WriterLease.acquire(document.path, document.descriptor.identity, "test", "button-test", "test")
            controller.begin_ingest(document_id, IngestJob(operation_id="button-test", owner_window_id=api.search_window.window_id), lease)
            try:
                evaluate_async(options, "refreshOptions().then(() => true)")
                assert options.evaluate_js("document.getElementById('save').disabled")
                evaluate_async(ingest, "refreshHistory().then(() => true)")
                assert ingest.evaluate_js("document.getElementById('import-directory').disabled")
                assert not evaluate_async(ingest, "window.pywebview.api.import_directory()")
            finally:
                controller.finish_ingest(document_id, "button-test", published=False)
            evaluate_async(ingest, "refreshHistory().then(() => true)")
            assert not ingest.evaluate_js("document.getElementById('import-directory').disabled")
            # Shared-document Close must never wait for an import or a Cocoa dialog.
            sibling = application.create_search_window(controller.new_search_window(document))
            assert sibling.window.events.loaded.wait(10), "Second search bridge failed to load"
            # Windows has no persistent About: only the search timer may deliver notices.
            about.load_html("<p id='search-only-probe'>About polling paused for the search-only regression.</p>")
            deadline = monotonic() + 3
            while not about.evaluate_js("Boolean(document.getElementById('search-only-probe'))") and monotonic() < deadline:
                sleep(0.02)
            assert about.evaluate_js("Boolean(document.getElementById('search-only-probe'))")
            job = IngestJob(operation_id="shared-close", owner_window_id=api.search_window.window_id)
            lease = WriterLease.acquire(document.path, document.descriptor.identity, "test", job.operation_id, "test")
            controller.begin_ingest(document_id, job, lease)
            release_commands = Event()
            try:
                assert drain_bridge_calls()
                AppHelper.callAfter(api.window.native.performClose_, None)
                assert api.window.events.closed.wait(5), "Closing original import window blocked Cocoa"
                transferred = document.ingest_job
                assert transferred is not None and sibling.search_window is not None
                assert transferred.owner_window_id == sibling.search_window.window_id
                assert transferred.stop is job.stop and transferred.finished is job.finished and lease.acquired
                assert evaluate_async(sibling.window, "window.pywebview.api.status()")['document_id'] == document_id
                # Long imports occupy every command worker; notices must not use that pool.
                started = [Event() for _ in range(8)]

                def hold_command(ready: Event) -> None:
                    ready.set()
                    assert release_commands.wait(10), "Busy command fixture was not released"

                for ready in started:
                    application._submit_task("busy close fixture", lambda ready=ready: hold_command(ready))  # pylint: disable=protected-access
                assert all(ready.wait(2) for ready in started)
                # A bridge may own the app lock while waiting for Cocoa. Close must return.
                main_returned = Event()
                with application._lock:  # pylint: disable=protected-access
                    AppHelper.callAfter(sibling.window.native.performClose_, None)
                    AppHelper.callAfter(main_returned.set)
                    assert main_returned.wait(2), "Last-window Close waited for the application lock"
                assert sibling.window.evaluate_js("document.readyState") == "complete"
                assert not sibling.window.events.closed.is_set()
                evaluate_async(sibling.window, """(async () => {
                    const deadline = Date.now() + 3000;
                    const message = 'Cannot close window when import is running. Use Quit (⌘ Q) to suspend import and exit program.';
                    while (!document.body.textContent.includes(message) && Date.now() < deadline)
                        await new Promise(resolve => setTimeout(resolve, 20));
                    if (!document.body.textContent.includes(message))
                        throw new Error('Last-window Close did not explain Quit');
                    return true;
                })()""")
            finally:
                release_commands.set()
                controller.finish_ingest(document_id, job.operation_id, published=False)
            close_window(sibling.window)
        except Exception:  # pylint: disable=broad-exception-caught
            failures.append(traceback.format_exc())
        finally:
            application.stop_imports_for_quit()
            for window in list(webview.windows):
                if window is not about:
                    if not drain_bridge_calls():
                        failures.append("Native bridge callback did not finish before teardown")
                    window.destroy()
                    window.events.closed.wait(3)
            # pywebview returns bridge results synchronously through Cocoa. Let those
            # callbacks finish before closing its last window and stopping the loop.
            if not drain_bridge_calls():
                failures.append("Native bridge callback did not finish before teardown")
            about.destroy()

    try:
        webview.start(probe, private_mode=True, http_server=False, menu=application.menu())
    finally:
        application.shutdown()
    assert not failures, failures
    print("Production About, document bridge, and menus passed")


if __name__ == "__main__":
    main()
