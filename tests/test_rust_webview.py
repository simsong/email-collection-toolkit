# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Exercise the shared production frontend with the real Rust JSON dispatcher.
# A subprocess supplies archive data through the same protocol as native IPC.
# Playwright provides only the transport connection and a headless browser.
# The test searches, sorts, selects, resizes, and finds text through real widgets.
# A Python-created archive proves compatibility and stays byte-for-byte unchanged.
# No native windows, private email, or simulated backend responses participate.
from hashlib import sha256
import json
from os import environ
from pathlib import Path
import subprocess
import sqlite3
import sys

from playwright.sync_api import Page, expect
import pytest

from test_mailsearch import make_archive

ROOT = Path(__file__).resolve().parents[1]


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
        expect(page.locator("#processing-open")).to_be_disabled()
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
