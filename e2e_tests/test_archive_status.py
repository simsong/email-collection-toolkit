# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Exercise archive status through the shipped GUI and its real Python bridge.
# Synthetic archives provide readiness and the current attachment notice.
# Additional notices enter the renderer as data, without replacing services.
# Browser geometry verifies that multiple lines reserve workspace height.
# Lifecycle checks keep empty and standalone windows free of archive banners.
# This validates the banner only; the separate Tika indexer remains planned.
"""Requirements: multiline archive status, safe text, and unobscured search panes."""

from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from e2e_tests.test_gui_processing import expose
from mailarchiver.gui_app import GUI_DIRECTORY, SEARCH_BRIDGE_METHODS, GuiApi, GuiStatus
from tests.test_gui_service import make_gui_archive

WIDTH = "width"
HEIGHT = "height"
NOTICE = "Currently only .txt email attachments are indexed"


@pytest.mark.parametrize("height", [600, 960])
def test_archive_status_lines_reserve_space_and_keep_link_status(
    tmp_path: Path, page: Page, height: int,
) -> None:
    """Archive notices remain separate from ingest/link status and never cover panes."""
    api = GuiApi(make_gui_archive(tmp_path))
    expose(page, api, SEARCH_BRIDGE_METHODS)
    page.set_viewport_size({WIDTH: 1000, HEIGHT: height})
    try:
        status = GuiStatus.model_validate(api.status())
        assert status.archive_status_messages == [NOTICE]
        page.goto((GUI_DIRECTORY / "index.html").as_uri())
        banner = page.locator("#archive-status-banner")
        messages = page.locator("#archive-status-messages p")
        expect(banner).to_be_visible()
        expect(messages).to_have_text([NOTICE])
        notices = [NOTICE, '<img src=x onerror="throw new Error(\'unsafe\')">', "Another archive notice"]
        page.evaluate("messages => renderArchiveStatus(messages)", notices)
        expect(messages).to_have_text(notices)
        expect(banner.locator("img")).to_have_count(0)
        assert page.evaluate("""() => {
            const banner = document.querySelector('#archive-status-banner').getBoundingClientRect();
            const workspace = document.querySelector('.workspace').getBoundingClientRect();
            const lines = [...document.querySelectorAll('#archive-status-messages p')]
                .map(line => line.getBoundingClientRect());
            return banner.left === 0 && banner.right === innerWidth && banner.bottom === innerHeight
                && workspace.bottom <= banner.top && workspace.height > 0
                && lines.every((line, index) => index === 0 || lines[index - 1].bottom <= line.top);
        }""")
        ingest = page.locator("#ingest-status-line")
        expect(ingest).to_be_visible()
        original = ingest.inner_text()
        page.evaluate("showLinkDestination('https://example.org/destination')")
        expect(ingest).to_have_text("https://example.org/destination")
        expect(messages).to_have_text(notices)
        page.evaluate("clearLinkDestination()")
        expect(ingest).to_have_text(original)
        page.evaluate("messages => renderArchiveStatus(messages)", [NOTICE])
        expect(messages).to_have_text([NOTICE])
        page.emulate_media(media="print")
        expect(banner).to_be_hidden()
    finally:
        api.close()


@pytest.mark.parametrize("standalone", [False, True])
def test_archive_status_is_hidden_without_archive_or_in_message_window(
    tmp_path: Path, page: Page, standalone: bool,
) -> None:
    """Readiness and standalone-message modes govern the whole banner."""
    api = GuiApi(make_gui_archive(tmp_path) if standalone else None)
    expose(page, api, SEARCH_BRIDGE_METHODS)
    try:
        status = GuiStatus.model_validate(api.status())
        assert status.archive_status_messages == ([NOTICE] if standalone else [])
        page.goto((GUI_DIRECTORY / "index.html").as_uri() + ("?standalone=1" if standalone else ""))
        if standalone:
            expect(page.locator("#search")).to_be_enabled()
        else:
            expect(page.locator("#search")).to_be_disabled()
        expect(page.locator("#archive-status-banner")).to_be_hidden()
    finally:
        api.close()
