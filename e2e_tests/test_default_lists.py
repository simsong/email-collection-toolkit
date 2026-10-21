# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Exercise the shipped generic list editor with its real Python bridge.
# Temporary templates and SQLite settings isolate changes from user preferences.
# Chromium drives selection, validation errors, save and reset through the UI.
# Assertions compare persisted data with the displayed state after each action.
# This verifies the documented personal-versus-archive configuration boundary.
"""Customizable Default Lists desktop editor requirements."""
from pathlib import Path
import re

from playwright.sync_api import Page, expect

from mailarchiver.gui_app import GUI_DIRECTORY
from mailarchiver.gui_default_lists import DefaultListsApi
from mailarchiver.list_catalog import OWNERS
from e2e_tests.test_gui_processing import expose


def test_editor_personal_archive_validation_reset(tmp_path: Path,page: Page) -> None:
    archive=tmp_path/'archive'
    archive.mkdir()
    directory=tmp_path/'personal'
    api=DefaultListsApi(archive,directory=directory)
    expose(page,api,('catalog','load','update'))
    page.goto((GUI_DIRECTORY/'default-lists.html').as_uri())
    expect(page.locator('#setup')).to_be_visible()
    page.locator('#yaml').fill('include: [alice@example.test]')
    page.locator('#save').click()
    expect(page.locator('#status')).to_have_text('Saved.')
    expect(page.locator('#setup')).not_to_be_visible()
    assert OWNERS.template(directory).include==['alice@example.test']
    page.locator('#scope').select_option('archive')
    expect(page.locator('#origin')).to_contain_text('personal template')
    page.locator('#yaml').fill('include: [archive@example.test]')
    page.locator('#save').click()
    expect(page.locator('#origin')).to_contain_text('archive configuration file')
    assert OWNERS.read(archive,directory).include==['archive@example.test']
    assert OWNERS.template(directory).include==['alice@example.test']
    page.locator('#reset').click()
    expect(page.locator('#yaml')).to_have_value(re.compile('alice@example.test'))
    page.locator('#list').select_option('reply-addresses')
    expect(page.locator('#description')).to_contain_text('Provider-scoped')
    page.locator('#yaml').fill('rules: [{id: bad, domains: ["*.com"], family: x}]')
    page.locator('#save').click()
    expect(page.locator('#error')).to_be_visible()
    expect(page.locator('#yaml')).to_have_value('rules: [{id: bad, domains: ["*.com"], family: x}]')
    page.locator('#reload').click()
    expect(page.locator('#yaml')).to_have_value(re.compile('github-reply'))
    page.locator('#list').select_option('display-policy')
    expect(page.locator('#scope')).to_have_value('personal')
    expect(page.locator('#scope option[value="archive"]')).to_be_disabled()
    page.screenshot(path=str(tmp_path/'default-lists.png'),full_page=True)
