# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Exercise the shipped welcome page in a real browser.
# Hold the native picker bridge at its asynchronous response boundary.
# Verify both document actions remain disabled until that response settles.
# Cover cancellation and failure without opening native system dialogs.
# This browser test is separate from reader-only native packaging checks.
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect


@pytest.mark.parametrize("fails", [False, True])
def test_welcome_picker_serializes_document_actions(page: Page, fails: bool) -> None:
    """One pending native picker must exclude both Open and Create until settlement."""
    # Hold only the native bridge boundary; execute the shipped page and handlers.
    page.add_init_script("""window.pywebview={api:{
      open_archive_dialog:()=>new Promise((resolve,reject)=>{
        window.finishPicker=()=>resolve(false); window.failPicker=()=>reject(new Error('picker failed'));
      }), new_document:()=>{throw new Error('concurrent creation');}
    }};""")
    page.goto((Path(__file__).parents[1] / "gui/reader.html").as_uri())
    page.evaluate("window.dispatchEvent(new Event('pywebviewready'))")
    page.locator("#open").click()
    expect(page.locator("#open")).to_be_disabled()
    expect(page.locator("#new")).to_be_disabled()
    page.evaluate("window.failPicker()" if fails else "window.finishPicker()")
    expect(page.locator("#open")).to_be_enabled()
    expect(page.locator("#new")).to_be_enabled()
    expect(page.locator("#status")).to_contain_text("picker failed" if fails else "No archive opened")

