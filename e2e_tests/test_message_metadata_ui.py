# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Exercise View Message Metadata through the shipped GUI and its real Python bridge.
# The (i) toolbar button and the control-click (context) menu on a result row and on
# the message pane must open the same grouped, read-only report. A message whose
# stored bytes begin with a ">From " envelope line must display its MIME body.
"""Requirements: message metadata is reachable by button and context menu; enveloped mail renders."""

import hashlib
from pathlib import Path

from playwright.sync_api import Page, expect

from e2e_tests.test_gui_processing import expose
from mailarchiver.gui_app import GUI_DIRECTORY, SEARCH_BRIDGE_METHODS, GuiApi
from tests.test_gui_service import MULTIPART_MESSAGE, make_gui_archive

ENVELOPED = b">From sender@example.net Fri Dec  5 08:49:19 2003\n" + MULTIPART_MESSAGE.replace(
    b"<multipart@example>", b"<enveloped@example>").replace(b"Subject: multipart message", b"Subject: enveloped note")


def test_metadata_button_and_context_menu_open_the_report(tmp_path: Path, page: Page) -> None:
    """The (i) button and control-click menus show grouped metadata; the body is not raw headers."""
    archive = make_gui_archive(tmp_path, ((ENVELOPED, hashlib.sha256(ENVELOPED).hexdigest(), "enveloped note",
                                            "2024-01-05T10:00:00+00:00"),))
    api = GuiApi(archive)
    expose(page, api, SEARCH_BRIDGE_METHODS)
    page.goto((GUI_DIRECTORY / "index.html").as_uri() + "?query=subject:enveloped")
    row = page.locator("#result-list .tabulator-row")
    expect(row).to_have_count(1)
    row.click()
    expect(page.locator("#message-subject")).to_have_text("enveloped note")
    expect(page.locator("#part-select option")).to_have_text(["Plain Text — part 2", "HTML — part 3", "Raw Source"])
    page.select_option("#part-select", label="Plain Text — part 2")
    expect(page.locator("#body-view")).to_contain_text("Plain version.")
    expect(page.locator("#body-view")).not_to_contain_text("Content-Type")

    dialog = page.locator("#metadata-dialog")
    page.click("#message-info")
    expect(dialog).to_be_visible()
    expect(dialog.locator("h3")).to_contain_text(["Message", "Hashes and storage", "Duplicates",
                                                  "MIME structure and attachments", "Extraction"])
    expect(dialog).to_contain_text("<enveloped@example>")
    expect(dialog).to_contain_text("report.pdf · application/pdf")
    page.click("#metadata-close")
    expect(dialog).to_be_hidden()

    menu = page.locator("#message-context-menu")
    row.click(button="right")
    expect(menu).to_be_visible()
    expect(page.locator("#context-copy")).to_be_hidden()
    page.keyboard.press("Escape")
    expect(menu).to_be_hidden()
    page.locator("#message-subject").click(button="right")
    expect(menu).to_be_visible()
    page.click("#context-view-metadata")
    expect(menu).to_be_hidden()
    expect(dialog).to_be_visible()
    expect(dialog).to_contain_text(hashlib.sha256(ENVELOPED).hexdigest())
