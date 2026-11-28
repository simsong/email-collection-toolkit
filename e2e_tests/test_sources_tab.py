# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Exercise the search window's Search / Sources tabs through the shipped GUI and real bridge.
# A fixture archive with a recorded local source and an IMAP account is opened; the test
# switches tabs, toggles a source's checkbox (persisted in config.yaml), opens the IMAP
# Config… dialog, and checks that window-opening buttons end in "…".
"""Requirement: Sources lists imported sources with checkboxes and IMAP Config…; tabs switch views."""

from pathlib import Path

from playwright.sync_api import Page, expect

from e2e_tests.test_gui_processing import expose
from mailarchiver.archive_config import load_archive_config, save_archive_config
from mailarchiver.gui_app import GUI_DIRECTORY, SEARCH_BRIDGE_METHODS, GuiApi
from mailarchiver.gui_sources import record_local_sources
from mailarchiver.imap_models import ImapAccount
from tests.test_gui_service import make_gui_archive


def test_sources_tab_lists_sources_and_configures_imap(tmp_path: Path, page: Page) -> None:
    """Tabs replace the search view with the source list; checkboxes persist; Config… shows settings."""
    archive = make_gui_archive(tmp_path)
    mail = tmp_path / "Mail"
    mail.mkdir()
    record_local_sources(archive, [str(mail)])
    config = load_archive_config(archive)
    account = ImapAccount(server="imap.example.test", port=993, username="owner@example.test")
    config.imap_sources = [account]
    save_archive_config(archive, config)
    expose(page, GuiApi(archive), SEARCH_BRIDGE_METHODS)
    page.goto((GUI_DIRECTORY / "index.html").as_uri())

    tools = page.locator(".archive-tools")
    expect(tools.get_by_role("tab")).to_have_text(["Search", "Sources"])
    expect(tools.locator("button:not([role=tab])")).to_have_text(
        ["Names and addresses…", "Institutions…", "Rescan Imports…"])
    expect(page.locator("#search")).to_be_visible()

    page.get_by_role("tab", name="Sources").click()
    expect(page.get_by_role("tab", name="Sources")).to_have_attribute("aria-selected", "true")
    expect(page.locator("#search")).to_be_hidden()
    rows = page.locator("#source-list li")
    expect(rows).to_have_count(2)
    expect(rows.nth(0)).to_contain_text("Mail")
    expect(rows.nth(1)).to_contain_text("IMAP: owner@example.test")
    expect(rows.nth(0).get_by_role("button")).to_have_count(0)  # Config… is only for IMAP accounts.

    rows.nth(0).get_by_role("checkbox").uncheck()
    expect(rows.nth(0).get_by_role("checkbox")).not_to_be_checked()
    page.wait_for_function("() => !document.querySelector('#source-list input').disabled")
    assert not load_archive_config(archive).local_sources[0].enabled

    rows.nth(1).get_by_role("button", name="Config…").click()
    dialog = page.locator("#imap-config-dialog")
    expect(dialog).to_be_visible()
    expect(page.locator("#imap-config-server")).to_have_value("imap.example.test")
    expect(page.locator("#imap-config-username")).to_have_value("owner@example.test")
    page.locator("#imap-config-server").fill("mail.example.test")
    page.get_by_role("button", name="Save").click()
    expect(page.locator("#imap-config-error")).to_contain_text("Enter the password")
    page.get_by_role("button", name="Cancel").click()
    expect(dialog).to_be_hidden()

    page.get_by_role("tab", name="Search").click()
    expect(page.locator("#search")).to_be_visible()
    expect(page.locator("#sources-panel")).to_be_hidden()
