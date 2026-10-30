# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Validate the shared Python reader through its actual native webview bridge.
# Generate a new synthetic collection and use the normal GUI smoke entry point.
# This runs on Windows/WebView2 and macOS/WKWebView without production ingestion.
# Keep reports outside the fixture and compare all archived bytes after closing.
# A bounded subprocess prevents a broken native bridge from hanging validation.
"""Portable native reader acceptance, invoked through Make."""
from __future__ import annotations

import argparse
from collections.abc import Callable
import faulthandler
from importlib import import_module
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Event
from time import monotonic

from pydantic import BaseModel

from mailarchiver.reader_fixture import NATIVE_DESKTOP_TIMEOUT, RAW_MESSAGE, check_reader, inventory
from mailarchiver.clamav_definitions import UPDATE_ENV, UPDATER_ENV


class DesktopResult(BaseModel):
    passed: bool
    error: str | None = None
    phase: str = "startup"


def check_windows_antivirus_menu(application, search, evaluate, progress: Callable[[str], None], output: Path) -> None:
    """Cold and reopened Windows menu bursts retain one About and the existing definitions."""
    system = import_module("System")
    forms = import_module("System.Windows.Forms")
    webview = import_module("webview")
    settings = {UPDATE_ENV: str(output / "antivirus-updates"), UPDATER_ENV: str(output / "missing-freshclam")}
    saved = {key: os.environ.get(key) for key in settings}
    os.environ.update(settings)
    try:
        for phase in ("cold", "reopened"):
            progress(f"Windows anti-virus menu ({phase})")
            if application._about_window is not None and not application._about_window.events.closed.is_set():
                raise AssertionError("Anti-virus cold-window acceptance requires no existing About")

            def burst() -> None:
                strips = [control for control in search.native.Controls if isinstance(control, forms.MenuStrip)]
                if len(strips) != 1 or strips[0].IsDisposed:
                    raise AssertionError("Windows anti-virus command requires one live native menu")
                heading = next(heading for heading in strips[0].Items if str(heading.Text) == "Help")
                item = next(item for item in heading.DropDownItems if str(item.Text) == "Check for Anti-Virus Updates")
                if item.IsDisposed or not item.Enabled or item.Owner != heading.DropDown:
                    raise AssertionError("Windows anti-virus command is detached, disposed or disabled")
                for _ in range(8):
                    item.PerformClick()

            search.native.Invoke(system.Action(burst))
            deadline = monotonic() + 20
            about = None
            while monotonic() < deadline:
                about = application._about_window
                if about is not None and not about.events.closed.is_set():
                    break
                Event().wait(0.025)
            if about is None or about.events.closed.is_set() or not about.events.loaded.wait(20):
                raise TimeoutError("Anti-virus command did not create and load About")
            answer = evaluate("(async () => { const deadline=Date.now()+10000; "
                              "const status=document.getElementById('definition-update-status'); "
                              "while (!status.textContent.includes('Update failed') && Date.now()<deadline) "
                              "await new Promise(resolve=>setTimeout(resolve,25)); "
                              "return status.textContent.includes('Existing definitions were retained.') && "
                              "!document.getElementById('update-definitions').disabled && "
                              "document.getElementById('update-definitions').textContent === 'Check for Anti-Virus Updates'; })()", about)
            if not answer or not application._about_visible or (output / "antivirus-updates/active.json").exists():
                raise AssertionError("Windows anti-virus menu did not report a retained-data update failure")
            if [window for window in webview.windows if window.title.startswith("About ")] != [about]:
                raise AssertionError("Repeated anti-virus commands created duplicate About windows")
            about.destroy()
            if not about.events.closed.wait(20):
                raise TimeoutError("Anti-virus About did not close")
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def check_windows_preferences(application, evaluate, progress: Callable[[str], None]) -> None:
    """Native Close flushes active edits, and reopening restores the saved settings."""
    progress("Preferences loading")
    application.show_update_preferences()
    window = application._windows_preferences_window
    if not window.events.loaded.wait(25):
        raise TimeoutError("Preferences did not load")
    evaluate("(async () => { while (document.body.dataset.ready !== 'true') "
             "await new Promise(resolve => setTimeout(resolve, 25)); return true; })()", window)
    application.show_update_preferences()
    if application._windows_preferences_window is not window:
        raise AssertionError("Preferences created a duplicate native window")
    answer = evaluate("(async () => { document.querySelector('input[value=custom]').checked=true; "
                      "document.getElementById('worker-count').value='1'; "
                      "document.getElementById('automatic').checked=true; await savePreferences(); "
                      "document.getElementById('worker-count').value='2'; "
                      "return {saveAbsent: document.getElementById('save') === null, "
                      "automatic: document.getElementById('automatic').checked}; })()", window)
    if not answer["saveAbsent"] or answer["automatic"] != application.updates.status.automatic_checks:
        raise AssertionError("Preferences did not display accepted update settings without a Save button")
    progress("Preferences Close")
    window.destroy()
    if not window.events.closed.wait(20):
        raise TimeoutError("Preferences Close did not finish")
    if application.controller.preferences.workers.custom_count != 2:
        raise AssertionError("Native Close lost the unfinished worker edit")
    progress("Preferences reopening")
    application.show_update_preferences()
    reopened = application._windows_preferences_window
    if not reopened.events.loaded.wait(25):
        raise TimeoutError("Preferences did not reopen")
    answer = evaluate("(async () => { while (document.body.dataset.ready !== 'true') "
                      "await new Promise(resolve => setTimeout(resolve, 25)); "
                      "return document.getElementById('worker-count').value; })()", reopened)
    if answer != "2":
        raise AssertionError("Reopened Preferences lost the saved worker count")
    progress("Reopened Preferences Close")
    reopened.destroy()
    if not reopened.events.closed.wait(20):
        raise TimeoutError("Reopened Preferences did not close")


def check_native_import(application, output: Path) -> None:
    """Run the shared desktop create/import service with real windows and mail."""
    from mailarchiver.owner_rules import OwnerRules
    from mailarchiver.standalone_verify import verify_archive
    from mailarchiver.ingest_status import read_ingest_status

    source = output / "import-source.eml"
    source.write_bytes(RAW_MESSAGE)
    destination = output / "created.mailarchive"
    document = application.controller.create_document(destination)
    api = application.create_search_window(application.controller.new_search_window(document))
    if not api.window.events.loaded.wait(25):
        raise TimeoutError("Created archive window did not load")
    if not application.start_import(api, [source], owner_rules=OwnerRules(include=["*@example.test"]),
                                    scan_policy="clamav" if sys.platform == "win32" else "not-scanned"):
        raise AssertionError("Native desktop import did not start")
    deadline = monotonic() + 120
    while document.ingest_job is not None and monotonic() < deadline:
        Event().wait(0.05)
    if document.ingest_job is not None:
        raise TimeoutError("Native import did not finish")
    if api.status()["message_count"] != 1:
        raise AssertionError("Native import did not publish the synthetic message")
    if verify_archive(destination) or source.read_bytes() != RAW_MESSAGE:
        raise AssertionError("Native import failed fixity or changed its source")
    statuses = [read_ingest_status(path) for path in (destination / "status").glob("*.json")]
    if len(statuses) != 1 or statuses[0].state != "completed":
        raise AssertionError("Native import did not retain its completed status")


def desktop_check(output: Path) -> None:
    """Exercise normal windows and rendering, independently of the smoke page."""
    import webview
    from mailarchiver.application import ApplicationController, ApplicationPreferencesStore
    from mailarchiver.desktop_platform import windows_webview
    from mailarchiver.gui_app import GUI_DIRECTORY, PyWebViewApplication, configure_macos_application
    from mailarchiver.loopback import LoopbackAssetServer

    faulthandler.enable()
    faulthandler.dump_traceback_later(45, repeat=True)
    result = DesktopResult(passed=False)

    def progress(phase: str) -> None:
        result.phase = phase
        (output / "desktop.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
        print(f"Native desktop acceptance: {phase}", flush=True)

    progress("Platform initialization")
    if sys.platform == "win32":
        windows_webview()
    configure_macos_application()
    archive = output / "synthetic.mailarchive"
    before = inventory(archive)
    controller = ApplicationController(ApplicationPreferencesStore(output / "preferences.json"))
    application = PyWebViewApplication(controller, LoopbackAssetServer(GUI_DIRECTORY))
    if sys.platform == "win32":
        progress("Updater initialization")
        application.start_updates()
        if not application.updates.status.available:
            raise AssertionError(application.updates.status.detail)
    progress("Opening synthetic archive")
    if sys.platform != "win32":
        application.create_about_window(hidden=True)
    # Requirement: a file chosen inside a collection opens the enclosing document.
    api = application.open_document(archive / "archive.sqlite3")

    def evaluate(script: str, window=None):
        finished = Event()
        values = []
        def receive(value):
            values.append(value)
            finished.set()
        (window or api.window).evaluate_js(script, callback=receive)
        if not finished.wait(20):
            raise TimeoutError("Native UI operation timed out")
        return values[0]

    def exercise() -> None:
        try:
            progress("Search loading")
            if not api.window.events.loaded.wait(25):
                raise TimeoutError("Search window did not load")
            progress("Search and message rendering")
            answer = evaluate("(async () => { while (!document.title.includes('synthetic.mailarchive')) "
                              "await new Promise(resolve => setTimeout(resolve, 25)); elements.search.value='observatory'; "
                              "await runSearch(); await selectMessage(state.results[0].message_pk); "
                              "return {count: state.results.length, subject: state.view.subject}; })()")
            if answer != {"count": 1, "subject": "Portable message viewer"}:
                raise AssertionError(f"Rendered search/message failed: {answer}")
            for sort in ("date", "subject", "sender"):
                for direction in ("ascending", "descending"):
                    progress(f"Sorting {sort} {direction}")
                    answer = evaluate(f"window.pywebview.api.search('observatory',0,'{sort}','{direction}')")
                    if len(answer["results"]) != 1:
                        raise AssertionError("Native sort failed")
            if sys.platform == "win32":
                progress("Windows menus")
                system = import_module("System")
                forms = import_module("System.Windows.Forms")
                menus = []
                api.window.native.Invoke(system.Action(lambda: menus.extend(
                    control for control in api.window.native.Controls if isinstance(control, forms.MenuStrip))))
                if len(menus) != 1:
                    raise AssertionError("Reader accumulated native menu strips")
                labels = [str(item.Text) for heading in menus[0].Items for item in heading.DropDownItems]
                if not {"New", "Import…", "Document Options…", "Check for Program Updates", "Preferences…",
                        "Reprocess Saved Mail…"}.issubset(labels):
                    raise AssertionError("Native Windows creation/import actions are missing")
                if labels[labels.index("Check for Program Updates") + 1] != "Check for Anti-Virus Updates":
                    raise AssertionError("Native Windows anti-virus command must follow program updates")
                check_windows_antivirus_menu(application, api.window, evaluate, progress, output)
                check_windows_preferences(application, evaluate, progress)
                progress("Windows clipboard")
                # Save and restore clipboard contents within the UI thread, never log them.
                saved = []
                api.window.native.Invoke(system.Action(lambda: saved.append(forms.Clipboard.GetDataObject())))
                try:
                    evaluate("window.pywebview.api.copy_visible_text('Synthetic café clipboard')")
                    copied = []
                    api.window.native.Invoke(system.Action(lambda: copied.append(str(forms.Clipboard.GetText()))))
                    if copied != ["Synthetic café clipboard"]:
                        raise AssertionError("Native Unicode clipboard failed")
                finally:
                    api.window.native.Invoke(system.Action(lambda: forms.Clipboard.SetDataObject(saved[0], True)
                                                           if saved[0] is not None else forms.Clipboard.Clear()))
                progress("Windows screenshot")
                core = import_module("Microsoft.Web.WebView2.Core")
                stream = system.IO.MemoryStream()
                tasks = []
                api.window.native.Invoke(system.Action(lambda: tasks.append(api.window.native.webview.CoreWebView2.CapturePreviewAsync(
                    core.CoreWebView2CapturePreviewImageFormat.Png, stream))))
                if not tasks[0].Wait(20000):
                    raise TimeoutError("Native screenshot timed out")
                (output / "reader.png").write_bytes(bytes(stream.ToArray()))
                stream.Dispose()
            if inventory(archive) != before:
                raise AssertionError("Desktop interaction changed archive files")
            progress("Native import with preservation and scanning")
            check_native_import(application, output)
            if sys.platform == "win32":
                from mailarchiver.scanner import scanner_availability
                if not scanner_availability().configured:
                    raise AssertionError("ClamAV is unavailable after native import")
            result.passed = True
            progress("Checks complete")
        except Exception as error:
            result.error = f"{type(error).__name__}: {error}"
        finally:
            (output / "desktop.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
            print("Native desktop acceptance: Quit", flush=True)
            application.request_quit(confirm_ingest=False)

    webview.start(exercise, gui="edgechromium" if sys.platform == "win32" else None,
                  http_server=False, private_mode=True, menu=application.menu())
    application.shutdown()
    faulthandler.cancel_dump_traceback_later()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--desktop-check", type=Path)
    args = parser.parse_args()
    if args.desktop_check is not None:
        desktop_check(args.desktop_check.resolve())
        return
    output = args.output or Path(tempfile.mkdtemp(prefix="python-reader-")) / "evidence"
    check_reader(output)
    archive = output / "synthetic.mailarchive"
    before = inventory(archive)
    report = output / "native.json"
    with (output / "native.log").open("w", encoding="utf-8") as log:
        subprocess.run([sys.executable, "-m", "mailarchiver.gui_app", "--archive", str(archive),
                        "--smoke-test", "--smoke-html-find", "--smoke-report", str(report)],
                       stdout=log, stderr=subprocess.STDOUT, check=True, timeout=90)
    if not json.loads(report.read_text(encoding="utf-8"))["passed"]:
        raise AssertionError("Native reader bridge failed")
    if inventory(archive) != before:
        raise AssertionError("Native reader changed collection bytes")
    with (output / "desktop.log").open("w", encoding="utf-8") as log:
        try:
            subprocess.run([sys.executable, __file__, "--desktop-check", str(output.resolve())],
                           stdout=log, stderr=subprocess.STDOUT, check=True, timeout=NATIVE_DESKTOP_TIMEOUT)
        except subprocess.SubprocessError:
            log.flush()
            for name in ("desktop.json", "desktop.log"):
                evidence = output / name
                if evidence.is_file():
                    print(f"Native desktop evidence: {name}\n{evidence.read_text(encoding='utf-8', errors='replace')[-32000:]}",
                          file=sys.stderr, flush=True)
            raise
    if not DesktopResult.model_validate_json((output / "desktop.json").read_text(encoding="utf-8")).passed:
        raise AssertionError((output / "desktop.json").read_text(encoding="utf-8"))
    print(f"Native reader passed: {output}")


if __name__ == "__main__":
    main()
