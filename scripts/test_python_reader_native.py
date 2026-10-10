# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Validate the shared Python reader through its actual native webview bridge.
# Generate a new synthetic collection and use the normal GUI smoke entry point.
# This runs on Windows/WebView2 and macOS/WKWebView without production ingestion.
# Keep reports outside the fixture and compare all archived bytes after closing.
# A bounded subprocess prevents a broken native bridge from hanging validation.
"""Portable native reader acceptance, invoked through Make."""
from __future__ import annotations

import argparse
from importlib import import_module
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Event
from time import monotonic

from pydantic import BaseModel

from mailarchiver.reader_fixture import RAW_MESSAGE, check_reader, inventory


class DesktopResult(BaseModel):
    passed: bool
    error: str | None = None


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

    if sys.platform == "win32":
        windows_webview()
    configure_macos_application()
    archive = output / "synthetic.mailarchive"
    before = inventory(archive)
    controller = ApplicationController(ApplicationPreferencesStore(output / "preferences.json"))
    application = PyWebViewApplication(controller, LoopbackAssetServer(GUI_DIRECTORY))
    if sys.platform == "win32":
        application.start_updates()
        if not application.updates.status.available:
            raise AssertionError(application.updates.status.detail)
    application.create_about_window(hidden=True)
    # Requirement: a file chosen inside a collection opens the enclosing document.
    api = application.open_document(archive / "archive.sqlite3")
    result = DesktopResult(passed=False)

    def evaluate(script: str):
        finished = Event()
        values = []
        def receive(value):
            values.append(value)
            finished.set()
        api.window.evaluate_js(script, callback=receive)
        if not finished.wait(20):
            raise TimeoutError("Native UI operation timed out")
        return values[0]

    def exercise() -> None:
        try:
            if not api.window.events.loaded.wait(25):
                raise TimeoutError("Search window did not load")
            answer = evaluate("(async () => { while (!document.title.includes('synthetic.mailarchive')) "
                              "await new Promise(resolve => setTimeout(resolve, 25)); elements.search.value='observatory'; "
                              "await runSearch(); await selectMessage(state.results[0].message_pk); "
                              "return {count: state.results.length, subject: state.view.subject}; })()")
            if answer != {"count": 1, "subject": "Portable message viewer"}:
                raise AssertionError(f"Rendered search/message failed: {answer}")
            for sort in ("date", "subject", "sender"):
                for direction in ("ascending", "descending"):
                    answer = evaluate(f"window.pywebview.api.search('observatory',0,'{sort}','{direction}')")
                    if len(answer["results"]) != 1:
                        raise AssertionError("Native sort failed")
            if sys.platform == "win32":
                system = import_module("System")
                forms = import_module("System.Windows.Forms")
                menus = []
                api.window.native.Invoke(system.Action(lambda: menus.extend(
                    control for control in api.window.native.Controls if isinstance(control, forms.MenuStrip))))
                if len(menus) != 1:
                    raise AssertionError("Reader accumulated native menu strips")
                labels = [str(item.Text) for heading in menus[0].Items for item in heading.DropDownItems]
                if not {"New", "Import…", "Document Options…", "Check for Updates…", "Update Settings…"}.issubset(labels):
                    raise AssertionError("Native Windows creation/import actions are missing")
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
            check_native_import(application, output)
            if sys.platform == "win32":
                from mailarchiver.scanner import scanner_availability
                if not scanner_availability().configured:
                    raise AssertionError("ClamAV is unavailable after native import")
            result.passed = True
        except Exception as error:
            result.error = f"{type(error).__name__}: {error}"
        finally:
            (output / "desktop.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
            application.request_quit(confirm_ingest=False)

    webview.start(exercise, gui="edgechromium" if sys.platform == "win32" else None,
                  http_server=False, private_mode=True, menu=application.menu())
    application.shutdown()


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
        subprocess.run([sys.executable, __file__, "--desktop-check", str(output.resolve())],
                       stdout=log, stderr=subprocess.STDOUT, check=True, timeout=90)
    if not DesktopResult.model_validate_json((output / "desktop.json").read_text(encoding="utf-8")).passed:
        raise AssertionError((output / "desktop.json").read_text(encoding="utf-8"))
    print(f"Native reader passed: {output}")


if __name__ == "__main__":
    main()
