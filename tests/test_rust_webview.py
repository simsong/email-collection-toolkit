# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Exercise the shared production frontend with the real Rust JSON dispatcher.
# A subprocess supplies archive data through the same protocol as native IPC.
# Playwright provides only the transport connection and a headless browser.
# The test searches, sorts, selects, resizes, and finds text through real widgets.
# A Python-created archive proves compatibility and stays byte-for-byte unchanged.
# No native windows or private email participate. The startup boundary test
# observes outgoing IPC without simulating native dialog or archive responses.
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from queue import Queue
from hashlib import sha256
import json
from os import environ
from pathlib import Path
import subprocess
import sqlite3
import sys
import time
from typing import Literal

from playwright.sync_api import Page, expect
from pydantic import BaseModel, Field, JsonValue
import pytest
from mailarchiver.mailbox_tree import FilterSet, FilterSetPreferences, FilterSetStore, preferences_lock
from mailarchiver.catalog import create_search
from mailarchiver.search import index_message

from test_mailsearch import make_archive
from test_gui_service import MULTI_HTML_MESSAGE, make_gui_archive

ROOT = Path(__file__).resolve().parents[1]


class FilterRequest(BaseModel):
    id: int = 1
    method: str
    args: list[str | bool | list[str]] = Field(default_factory=list)


class WireRequest(BaseModel):
    method: str
    args: list[JsonValue] = Field(default_factory=list)


class StartupReply(BaseModel):
    id: int
    result: bool | None
    error: str | None


class FilterReply(BaseModel):
    id: int
    result: FilterSetPreferences | None
    error: str | None


def test_startup_buttons_send_native_requests_without_reader_initialization(page: Page) -> None:
    """Startup requirement: every action reaches IPC without archive toolbar errors."""
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    # Observe the native wire boundary; no dialogs or backend replies are faked.
    page.add_init_script("window.sentRequests=[]; window.ipc={postMessage:request=>window.sentRequests.push(JSON.parse(request))};")
    for script in ["bridge.js", "shell.js"]:
        page.add_init_script(path=ROOT / "rust/mailsearch-gui" / script)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(ROOT / "rust/mailsearch-gui")))
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        page.goto(f"http://127.0.0.1:{server.server_port}/welcome.html")
        expect(page.get_by_role("button", name="New archive…", exact=True)).to_be_disabled()
        page.get_by_role("button", name="Open archive…", exact=True).click()
        page.get_by_role("button", name="Quit", exact=True).click()
        # Exercise the production capability callback that enables the third control.
        page.evaluate("window.__rustWelcomeCapabilities({write_available:true})")
        page.get_by_role("button", name="New archive…", exact=True).click()
        requests = [FilterRequest.model_validate(value) for value in page.evaluate("window.sentRequests")]
        assert {request.method for request in requests[:2]} == {"shell_status", "welcome_status"}
        assert [request.method for request in requests[2:]] == ["welcome_open", "quit", "welcome_new"]
        assert [request.id for request in requests] == [1, 2, 3, 4, 5]
        assert all(not request.args for request in requests)
        assert page.evaluate("Object.keys(window.pywebview.api).sort()") == ["check_updates", "preferences_save", "quit", "shell_status", "welcome_new", "welcome_open", "welcome_status"]
        expect(page.locator("#rust-shell-dialog")).not_to_be_visible()
        assert not errors
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("operation", ["save", "rename", "delete"])
def test_shared_filter_mutations_reload_after_python_rust_lock(tmp_path: Path, operation: Literal["save", "rename", "delete"]) -> None:
    """Shared-filter requirement: real writers retain updates published while waiting."""
    binary = environ.get("RUST_WEBVIEW_BINARY")
    if not binary:
        pytest.skip("run make test-rust-webview")
    archive, _ = make_archive(tmp_path)
    before = [(p.relative_to(archive), sha256(p.read_bytes()).hexdigest()) for p in sorted(archive.rglob("*")) if p.is_file()]
    home = tmp_path / "home"
    root = home / "Library/Preferences" if sys.platform == "darwin" else home
    path = root / "mailarchiver/filter-sets.json"
    store = FilterSetStore(path)
    for name in ["Rust-old", "Python-old"]:
        store.save(FilterSet(name=name))
    process = subprocess.Popen([binary, "--rpc", str(archive)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True,
                               env={**environ, "HOME": str(home), "APPDATA": str(home), "XDG_CONFIG_HOME": str(home),
                                    "ECT_RUST_ENGINE_PYTHON": str(tmp_path / "missing-helper")})
    writer: subprocess.Popen[str] | None = None
    replies: Queue[str] = Queue()
    try:
        assert process.stdin is not None and process.stdout is not None

        def receive() -> None:
            assert process.stdout is not None
            replies.put(process.stdout.readline())

        def send(request: FilterRequest) -> None:
            assert process.stdin is not None
            process.stdin.write(request.model_dump_json() + "\n")
            process.stdin.flush()
            Thread(target=receive, daemon=True).start()

        send(FilterRequest(method="saved_filter_sets"))
        initial = FilterReply.model_validate_json(replies.get(timeout=10))
        assert initial.error is None and initial.result is not None
        assert len(initial.result.filter_sets) == 2
        with preferences_lock(path):
            args: list[str | bool | list[str]] = (["Rust", False, []] if operation == "save"
                                                 else ["Rust-old", "Rust"] if operation == "rename" else ["Rust-old"])
            send(FilterRequest(method=f"{operation}_filter_set", args=args))
            ready = tmp_path / "python.ready"
            writer = subprocess.Popen([sys.executable, "-c", """
from pathlib import Path
import sys
from mailarchiver.mailbox_tree import FilterSet, FilterSetStore
path, ready, operation = sys.argv[1:]
store = FilterSetStore(Path(path))
Path(ready).touch()
if operation == "save":
    store.save(FilterSet(name="Python"))
elif operation == "rename":
    store.rename("Python-old", "Python")
else:
    store.delete("Python-old")
""", str(path), str(ready), operation], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            deadline = time.monotonic() + 10
            while not ready.exists():
                assert writer.poll() is None, "Python writer exited before entering mutation"
                assert time.monotonic() < deadline, "Python writer did not start"
                time.sleep(0.01)
            time.sleep(0.2)
            assert replies.empty(), "Rust mutation bypassed the shared lock"
            assert process.poll() is None and writer.poll() is None
            baseline = store.read()
            baseline.filter_sets.append(FilterSet(name="Baseline"))
            path.write_text(baseline.model_dump_json(), encoding="utf-8")
        reply = FilterReply.model_validate_json(replies.get(timeout=10))
        assert reply.error is None, reply.error
        _, errors = writer.communicate(timeout=10)
        assert writer.returncode == 0, errors
        expected = ({"Rust-old", "Python-old", "Rust", "Python", "Baseline"} if operation == "save"
                    else {"Rust", "Python", "Baseline"} if operation == "rename" else {"Baseline"})
        assert {item.name for item in store.read().filter_sets} == expected
        assert path.with_suffix(".lock").exists()
    finally:
        for child in [writer, process]:
            if child is not None:
                if child.poll() is None:
                    child.kill()
                child.communicate(timeout=10)
    after = [(p.relative_to(archive), sha256(p.read_bytes()).hexdigest()) for p in sorted(archive.rglob("*")) if p.is_file()]
    assert after == before


@pytest.mark.parametrize("mixed", [False, True])
def test_reader_remains_usable_without_the_optional_archive_engine(page: Page, tmp_path: Path, mixed: bool) -> None:
    """Capabilities: unavailable writers cannot prompt or break real Rust search/read."""
    binary = environ.get("RUST_WEBVIEW_BINARY")
    if not binary:
        pytest.skip("run make test-rust-webview")
    archive, raw = make_archive(tmp_path)
    if mixed:
        # Legacy mboxo only quoted bare From lines; already-quoted lines stayed intact.
        raw += b"From plain\n>From retained\n>>From nested"
        record = b"From fixture Thu Jan 1 00:00:00 1970\n" + b"".join(
            (b">" if line.startswith(b"From ") else b"") + line for line in raw.splitlines(keepends=True)
        ) + b"\n"
        with sqlite3.connect(archive / "archive.sqlite3") as database:
            filename = database.execute("SELECT filename FROM mbox_generations").fetchone()[0]
            database.execute("UPDATE messages SET sha256=?", (sha256(raw).hexdigest(),))
            database.execute("UPDATE locations SET byte_offset=0,byte_length=?", (len(record),))
        (archive / "data/mbox" / filename).write_bytes(record)
        (archive / "search.sqlite3").unlink()
        search = create_search(archive / "search.sqlite3")
        try:
            index_message(search, raw, False)
            search.commit()
        finally:
            search.close()
    before = [(p.relative_to(archive), sha256(p.read_bytes()).hexdigest()) for p in sorted(archive.rglob("*")) if p.is_file()]
    process = subprocess.Popen([binary, "--rpc", str(archive)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True,
                               env={**environ, "ECT_RUST_ENGINE_PYTHON": str(tmp_path / "missing-python")})
    try:
        def transport(request: str) -> object:
            assert process.stdin is not None and process.stdout is not None
            process.stdin.write(request + "\n")
            process.stdin.flush()
            return json.loads(process.stdout.readline())
        page.expose_function("__rustTestTransport", transport)
        page.add_init_script(path=ROOT / "rust/mailsearch-gui/bridge.js")
        page.goto((ROOT / "gui/index.html").as_uri())
        expect(page.locator(".archive-tools .status")).to_have_text("Archive engine unavailable")
        for label in ["Import…", "Owner emails…", "Import history", "Names and addresses", "Institutions", "Continue Processing"]:
            expect(page.get_by_role("button", name=label, exact=True)).to_be_disabled()
        assert page.evaluate("window.pywebview.api.new_archive().then(()=>false,error=>error.message)")
        assert not page.locator("dialog[open]").count()
        page.locator("#search").fill("meeting agenda")
        page.locator("#search").press("Enter")
        expect(page.locator("#result-status")).to_have_text("1 message")
        page.locator("#result-list .result").first.click()
        expect(page.locator("#message-subject")).to_have_text("planning meeting")
        expect(page.locator("#body-view")).to_contain_text("Meeting agenda.")
        if mixed:
            expect(page.locator("#body-view")).to_contain_text("From plain\n>From retained\n>>From nested")
        expect(page.locator("#error")).to_be_hidden()
    finally:
        assert process.stdin is not None
        process.stdin.close()
        process.wait(timeout=6)
    after = [(p.relative_to(archive), sha256(p.read_bytes()).hexdigest()) for p in sorted(archive.rglob("*")) if p.is_file()]
    assert after == before


def test_existing_interface_with_rust_backend(page: Page, tmp_path: Path) -> None:
    """Rust UI requirement: preserve original interactions using real archive data."""
    binary = environ.get("RUST_WEBVIEW_BINARY")
    if not binary:
        pytest.skip("run make test-rust-webview")
    archive, _ = make_archive(tmp_path)
    # More than one scan slice, including date ties, using real indexed bytes.
    with sqlite3.connect(archive / "archive.sqlite3") as database:
        database.execute("WITH RECURSIVE seq(x) AS (VALUES(2) UNION ALL SELECT x+1 FROM seq WHERE x<1600) INSERT INTO messages SELECT x,printf('copy%d@example.test',x),sha256,sender_address_pk,subject,date_utc,date_source,category FROM seq CROSS JOIN messages WHERE message_pk=1")
        database.execute("INSERT INTO locations SELECT message_pk,1,(SELECT byte_offset FROM locations WHERE message_pk=1),(SELECT byte_length FROM locations WHERE message_pk=1) FROM messages WHERE message_pk>1")
        # Ordinary frontend search must exclude quarantine even for indexed bytes.
        for message_id, category in ((1601, "INFECTED"), (1602, "MALFORMED")):
            database.execute("INSERT INTO messages SELECT ?,?,sha256,sender_address_pk,subject,date_utc,date_source,? FROM messages WHERE message_pk=1",
                             (message_id, f"quarantine{message_id}@example.test", category))
    before = [(p.relative_to(archive), sha256(p.read_bytes()).hexdigest()) for p in sorted(archive.rglob("*")) if p.is_file()]
    process = subprocess.Popen([binary, "--rpc", str(archive)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)
    try:
        def transport(request: str) -> object:
            assert process.stdin is not None and process.stdout is not None
            process.stdin.write(request + "\n")
            process.stdin.flush()
            response = process.stdout.readline()
            assert response, "Rust dispatcher exited unexpectedly"
            return json.loads(response)

        page.expose_function("__rustTestTransport", transport)
        page.add_init_script(path=ROOT / "rust/mailsearch-gui/bridge.js")
        page.goto((ROOT / "gui/index.html").as_uri())
        expect(page.locator("#search")).to_be_enabled()
        expect(page.locator("#processing-open")).to_be_enabled(timeout=15000)
        page.evaluate("""() => {
            window.batchEvidence = [];
            window.holdNextSearch = true;
            const original = window.pywebview.api.search_advance;
            window.pywebview.api.search_advance = async (...args) => {
                window.batchEvidence.push({rows: document.querySelectorAll('#result-list .result').length,
                    status: document.getElementById('result-status').textContent});
                if (args[1] === 2 && window.holdNextSearch) {
                    window.holdNextSearch = false;
                    await new Promise(resolve => { window.releaseSearch = resolve; });
                }
                return original(...args);
            };
        }""")
        page.locator("#search").fill('from:sender@example.net subject:"planning meeting" agenda')
        page.locator("#search").press("Enter")
        expect(page.locator("#result-status")).to_have_text("Searching in background… 1,024 messages shown")
        assert page.evaluate("batchEvidence.length === 2 && batchEvidence.every(e => e.rows > 0 && e.status.includes('Searching in background'))")
        # Pause only the real protocol acknowledgement, never fabricate results.
        # Message reads must work while the independent search is unfinished.
        page.locator("#result-list .result").first.click()
        expect(page.locator("#message-subject")).to_have_text("planning meeting")
        expect(page.locator("#body-view")).to_contain_text("Meeting agenda.")
        selected = page.evaluate("state.selected")
        page.evaluate("releaseSearch()")
        expect(page.locator("#result-status")).to_have_text("1,600 messages · 1,024 shown; scroll for more")
        assert page.evaluate("state.selected") == selected
        assert page.evaluate("state.results.length") == 1024
        # Scrolling fetches bounded display pages without rerunning the search.
        for expected_count in (1536, 1600):
            page.locator("#result-list .tabulator-tableholder").evaluate("e => { e.scrollTop = e.scrollHeight; }")
            page.wait_for_function("count => state.results.length === count", arg=expected_count)
        expect(page.locator("#result-status")).to_have_text("1,600 messages")
        assert page.evaluate("new Set(state.results.map(r => r.message_pk)).size") == 1600
        # Replace an unfinished query; delayed old acknowledgements/results cannot
        # repopulate the new result list, including after clearing the search.
        page.evaluate("window.holdNextSearch = true; window.releaseSearch = null")
        page.locator("#sort-by").select_option("subject")
        page.wait_for_function("typeof window.releaseSearch === 'function'")
        page.locator("#search").fill("zzzznomatch")
        page.locator("#search").press("Enter")
        expect(page.locator("#result-status")).to_have_text("0 messages")
        page.evaluate("releaseSearch()")
        assert page.evaluate("state.results.length") == 0
        page.locator("#search").fill("agenda")
        page.locator("#search").press("Enter")
        expect(page.locator("#result-status")).to_contain_text("1,600 messages")
        page.locator("#search").fill("")
        page.locator("#search").press("Enter")
        expect(page.locator("#result-status")).to_have_text("Enter a search.")
        assert page.evaluate("state.results.length") == 0
        page.locator("#search").fill("agenda")
        page.locator("#search").press("Enter")
        expect(page.locator("#result-status")).to_contain_text("1,600 messages")
        page.locator("#result-list .result").first.click()
        expect(page.locator("#body-view")).to_contain_text("Meeting agenda.")
        expect(page.locator("#message-headers")).to_contain_text("recipient@example.net")
        expect(page.get_by_label("Message preview", exact=True).first).to_contain_text("Meeting agenda")
        splitter = page.locator("#message-splitter")
        width_before = page.locator("#results-pane").evaluate("e => e.getBoundingClientRect().width")
        splitter.focus()
        splitter.press("ArrowRight")
        width_after = page.locator("#results-pane").evaluate("e => e.getBoundingClientRect().width")
        assert width_after > width_before
        page.locator("#body-view").click()
        page.keyboard.press("Meta+f" if sys.platform == "darwin" else "Control+f")
        expect(page.locator("#message-find")).to_be_visible()
        page.locator("#message-find-query").fill("agenda")
        expect(page.locator("#message-find-status")).to_contain_text("1")
        expect(page.locator("#error")).to_be_hidden()
        output = ROOT / ".tmp/rust-gui-existing-interface.png"
        page.screenshot(path=str(output), full_page=True)
    finally:
        if process.stdin is not None:
            process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=5)
    after = [(p.relative_to(archive), sha256(p.read_bytes()).hexdigest()) for p in sorted(archive.rglob("*")) if p.is_file()]
    assert after == before


def test_rust_mime_filters_and_dates_in_shared_widgets(page: Page, tmp_path: Path) -> None:
    """Reader migration: real MIME, date and attachment controls preserve source bytes."""
    binary = environ.get("RUST_WEBVIEW_BINARY")
    if not binary:
        pytest.skip("run make test-rust-webview")
    archive = make_gui_archive(tmp_path, ((MULTI_HTML_MESSAGE, "multi-html@example", "multi HTML message", "2024-01-04T11:00:00+00:00"),))
    before = [(p.relative_to(archive), sha256(p.read_bytes()).hexdigest()) for p in sorted(archive.rglob("*")) if p.is_file()]
    process = subprocess.Popen([binary, "--rpc", str(archive)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)
    try:
        def transport(request: str) -> object:
            assert process.stdin is not None and process.stdout is not None
            process.stdin.write(request + "\n")
            process.stdin.flush()
            response = process.stdout.readline()
            assert response, "Rust dispatcher exited unexpectedly"
            return json.loads(response)

        page.expose_function("__rustTestTransport", transport)
        page.add_init_script(path=ROOT / "rust/mailsearch-gui/bridge.js")
        page.goto((ROOT / "gui/index.html").as_uri())
        expect(page.locator("#search")).to_be_enabled()
        expect(page.locator("#search-attachments")).to_be_enabled()
        page.locator("#search").fill("Appendixquartz")
        page.locator("#search").press("Enter")
        expect(page.locator("#result-status")).to_have_text("0 messages")
        page.locator("#search-attachments").check()
        expect(page.locator("#result-status")).to_have_text("1 message")
        page.locator("#result-list .result").first.click()
        expect(page.locator("#message-subject")).to_have_text("multipart message")
        expect(page.frame_locator("#body-view iframe").locator("body")).to_contain_text("HTML version.")
        expect(page.locator("#remote-content")).to_be_visible()
        expect(page.locator("#attachment-list")).to_contain_text("report.pdf")
        # Rust attachment opening must use a rendered dialog, never WKWebView confirm().
        open_pdf = page.locator("#attachment-list .attachment").filter(has_text="report.pdf").get_by_role("button", name="Open", exact=True)
        open_pdf.click()
        confirmation = page.get_by_role("dialog", name="Open attachment", exact=True)
        expect(confirmation).to_be_visible()
        expect(confirmation).to_contain_text("report.pdf may contain active or unrecognized content")
        confirmation.get_by_role("button", name="Cancel", exact=True).click()
        expect(confirmation).to_have_count(0)
        expect(page.locator("#error")).to_be_hidden()
        open_pdf.click()
        expect(confirmation).to_be_visible()
        confirmation.press("Escape")
        expect(confirmation).to_have_count(0)
        expect(page.locator("#error")).to_be_hidden()
        assert page.frame_locator("#body-view iframe").locator("script").count() == 0
        assert page.frame_locator("#body-view iframe").locator("img[src^='https://']").count() == 0
        assert page.frame_locator("#body-view iframe").locator("img[src^='data:image/png;']").count() == 1
        # Explicit MIME preference survives keyboard selection and different part IDs.
        plain = page.locator("#part-select option").filter(has_text="Plain Text").first.get_attribute("value")
        assert plain is not None
        page.locator("#part-select").select_option(plain)
        expect(page.locator("#body-view iframe")).to_have_count(0)
        page.locator("#search").fill("from:sender")
        page.locator("#search").press("Enter")
        expect(page.locator("#result-status")).to_have_text("3 messages")
        page.locator("#result-list .result").filter(has_text="multipart message").click()
        expect(page.locator("#message-content")).to_be_visible()
        expect(page.locator("#part-select")).to_have_value(plain)
        page.locator("#result-list").dispatch_event("keydown", {"key": "ArrowDown", "bubbles": True})
        expect(page.locator("#message-subject")).to_have_text("annual plan")
        page.locator("#result-list").dispatch_event("keydown", {"key": "ArrowUp", "bubbles": True})
        expect(page.locator("#message-subject")).to_have_text("multipart message")
        expect(page.locator("#body-view iframe")).to_have_count(0)
        expect(page.locator("#part-select")).to_have_value(plain)
        # Missing Plain Text falls back without forgetting the choice, and retained
        # HTML must prefer the complete part over a preceding short alternative.
        page.locator("#result-list").dispatch_event("keydown", {"key": "ArrowUp", "bubbles": True})
        expect(page.locator("#message-subject")).to_have_text("multi HTML message")
        expect(page.frame_locator("#body-view iframe").locator("body")).to_contain_text("Complete web report")
        page.locator("#result-list").dispatch_event("keydown", {"key": "ArrowDown", "bubbles": True})
        expect(page.locator("#message-subject")).to_have_text("multipart message")
        expect(page.locator("#part-select")).to_have_value(plain)
        expect(page.locator("#body-view iframe")).to_have_count(0)
        html = page.locator("#part-select option").filter(has_text="HTML").first.get_attribute("value")
        assert html is not None
        page.locator("#part-select").select_option(html)
        expect(page.frame_locator("#body-view iframe").locator("body")).to_contain_text("HTML version.")
        page.locator("#result-list").dispatch_event("keydown", {"key": "ArrowUp", "bubbles": True})
        expect(page.locator("#message-subject")).to_have_text("multi HTML message")
        expect(page.frame_locator("#body-view iframe").locator("body")).to_contain_text("Complete web report")
        expect(page.locator("#part-select")).to_have_value("2")
        page.locator("#result-list").dispatch_event("keydown", {"key": "ArrowDown", "bubbles": True})
        expect(page.locator("#message-subject")).to_have_text("multipart message")
        expect(page.frame_locator("#body-view iframe").locator("body")).to_contain_text("HTML version.")
        page.locator("#part-select").select_option("-1")
        expect(page.locator("#body-view")).to_contain_text("Content-Type: multipart/mixed")
        page.locator("#search").fill('date:"January 3, 2024"')
        page.locator("#search").press("Enter")
        expect(page.locator("#result-status")).to_have_text("3 messages")
        page.locator("#search").fill("after:1/3/2024")
        page.locator("#search").press("Enter")
        expect(page.locator("#result-status")).to_have_text("0 messages")
        page.locator("#show-original-folders").check()
        expect(page.locator("#mailbox-tree")).to_contain_text("mail")
        expect(page.locator("#error")).to_be_hidden()
        page.locator("#search").fill("Appendixquartz")
        page.locator("#search").press("Enter")
        expect(page.locator("#result-status")).to_have_text("1 message")
        page.locator("#result-list .result").first.click()
        open_pdf.click()
        confirmation.get_by_role("button", name="Open", exact=True).click()
        expect(confirmation).to_have_count(0)
        # The real headless dispatcher must still prohibit the approved OS launch.
        expect(page.locator("#error")).to_contain_text("requires the native desktop window")
    finally:
        if process.stdin is not None:
            process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=5)
    after = [(p.relative_to(archive), sha256(p.read_bytes()).hexdigest()) for p in sorted(archive.rglob("*")) if p.is_file()]
    assert after == before


def test_rust_archive_editors_use_real_services(page: Page, tmp_path: Path) -> None:
    """Migration: existing editor widgets persist settings through the supervised engine."""
    binary = environ.get("RUST_WEBVIEW_BINARY")
    if not binary:
        pytest.skip("run make test-rust-webview")
    archive = make_gui_archive(tmp_path)
    canonical = [(p, sha256(p.read_bytes()).hexdigest()) for p in (archive / "data/mbox").glob("*.mbox")]
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(ROOT / "gui")))
    serving = Thread(target=server.serve_forever, daemon=True)
    serving.start()
    process = subprocess.Popen([binary, "--rpc", str(archive)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)
    invoked: list[str] = []
    try:
        def transport(request: str) -> object:
            invoked.append(json.loads(request)["method"])
            assert process.stdin is not None and process.stdout is not None
            process.stdin.write(request + "\n")
            process.stdin.flush()
            return json.loads(process.stdout.readline())
        page.expose_function("__rustTestTransport", transport)
        page.add_init_script(path=ROOT / "rust/mailsearch-gui/bridge.js")
        # Observe the actual port transfer so a compromised editor can forge a call.
        page.add_init_script("""window.addEventListener('message', event => {
            if(event.data?.type === 'ect-editor') window.editorPort = event.ports[0];
        });""")
        page.goto(f"http://127.0.0.1:{server.server_port}/index.html")
        expect(page.get_by_role("button", name="Owner emails…", exact=True)).to_be_enabled(timeout=15000)
        # Native menus use the same promise-returning action as the toolbar.
        assert page.evaluate("window.pywebview.api.open_options().catch(error => { throw error; })") is True
        editor = page.frame_locator(".rust-workflow iframe")
        expect(editor.locator("#owner-include")).to_be_enabled()
        expect(page.get_by_role("dialog", name="Owner emails", exact=True)).to_be_visible()
        # IPC isolation: scripts run, but cannot access the main document/API.
        assert editor.locator("body").evaluate("""() => {
            try { return Boolean(parent.pywebview.api); } catch(error) { return error.name; }
        }""") == "SecurityError"
        assert editor.locator("body").evaluate("Object.keys(window.pywebview.api).sort()") == ["status", "update"]
        rejected = editor.locator("body").evaluate("""() => new Promise(resolve => {
            window.editorPort.addEventListener('message', event => {
                if(event.data.id === 999999) resolve(event.data.error);
            });
            window.editorPort.postMessage({id:999999,method:'identity_update',args:[]});
        })""")
        assert rejected == "Editor method is not allowed."
        assert "identity_update" not in invoked
        editor.locator("#owner-include").fill("fixture@example.test")
        editor.locator("#save").click()
        expect(editor.locator("#saved")).to_be_visible()
        page.screenshot(path=str(ROOT / ".tmp/rust-owner-editor.png"), full_page=True)
        page.locator(".rust-workflow > button").click()
        page.get_by_role("button", name="Owner emails…", exact=True).click()
        expect(page.frame_locator(".rust-workflow iframe").locator("#owner-include")).to_have_value("fixture@example.test")
        page.locator(".rust-workflow > button").click()
        page.get_by_role("button", name="Names and addresses", exact=True).click()
        expect(page.frame_locator(".rust-workflow iframe").locator("#summary")).to_contain_text("0 of 0 addresses")
        page.locator(".rust-workflow > button").click()
        page.get_by_role("button", name="Import history", exact=True).click()
        expect(page.frame_locator(".rust-workflow iframe").locator("#history-count")).to_have_text("0 runs")
        page.locator(".rust-workflow > button").click()
        expect(page.locator("#error")).to_be_hidden()
    finally:
        assert process.stdin is not None
        process.stdin.close()
        process.wait(timeout=6)
        server.shutdown()
        server.server_close()
        serving.join(timeout=2)
    assert all(sha256(path.read_bytes()).hexdigest() == digest for path, digest in canonical)


def test_opening_warning_survives_reader_handoff_and_drag_cache_is_revoked(page: Page, tmp_path: Path) -> None:
    """Open/update recovery: real failed preferences survive navigation; stale exports expire."""
    binary = environ.get("RUST_WEBVIEW_BINARY")
    if not binary:
        pytest.skip("run make test-rust-webview")
    archive, _ = make_archive(tmp_path)
    home = tmp_path / "home"
    base = home / "Library/Application Support" if sys.platform == "darwin" else home
    base.mkdir(parents=True)
    blocked = base / "Email Collection Toolkit"
    blocked.write_bytes(b"Existing user-owned preference parent")
    environment = {**environ, "HOME": str(home), "LOCALAPPDATA": str(home), "XDG_CONFIG_HOME": str(home),
                   "ECT_RUST_ENGINE_PYTHON": str(tmp_path / "missing-helper")}
    process = subprocess.Popen([binary, "--opening-reader-rpc", str(archive)], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=environment)
    methods: list[str] = []
    try:
        def transport(request: str) -> object:
            assert process.stdin is not None and process.stdout is not None
            methods.append(WireRequest.model_validate_json(request).method)
            process.stdin.write(request + "\n")
            process.stdin.flush()
            response = process.stdout.readline()
            assert response, "Real opening/reader dispatcher exited"
            return json.loads(response)
        reply = transport(FilterRequest(method="opening_start").model_dump_json())
        startup = StartupReply.model_validate(reply)
        assert startup.error is None and startup.result is True
        page.expose_function("__rustTestTransport", transport)
        page.add_init_script(path=ROOT / "rust/mailsearch-gui/bridge.js")
        page.goto((ROOT / "gui/index.html").as_uri())
        expect(page.locator("#error")).to_be_visible()
        expect(page.locator("#error")).to_contain_text("recent-document preference could not be saved")
        assert page.evaluate("window.pywebview.api.opening_notices()") == []
        page.locator("#search").fill("meeting agenda")
        page.locator("#search").press("Enter")
        expect(page.locator("#result-status")).to_have_text("1 message")
        page.locator("#result-list .result").first.click()
        expect(page.locator("#body-view")).to_contain_text("Meeting agenda.")
        # Cached values are input state, not fabricated backend responses. The
        # recovered preparation must reach the actual Rust dispatcher again.
        page.evaluate("state.dragExports.set('1',{token:'revoked-token'}); state.dragPreparing.add('1')")
        page.evaluate("window.dispatchEvent(new Event('mailarchiver-exports-invalidated'))")
        assert page.evaluate("state.dragExports.size + state.dragPreparing.size") == 0
        page.evaluate("prepareDrag([1])")
        assert methods.count("prepare_drag") == 1
        assert page.evaluate("state.dragExports.size") == 0  # Cocoa dragging is unavailable headlessly.
        # A real late dispatcher reply must not clear a newer generation's lock.
        assert page.evaluate("""async()=>{
            const pending=prepareDrag([1]);
            window.dispatchEvent(new Event('mailarchiver-exports-invalidated'));
            state.dragPreparing.add('1');
            await pending;
            return state.dragPreparing.has('1');
        }""")
        assert blocked.read_bytes() == b"Existing user-owned preference parent"
    finally:
        assert process.stdin is not None
        process.stdin.close()
        process.wait(timeout=6)
        assert process.stderr is not None
        assert process.returncode == 0, process.stderr.read()
