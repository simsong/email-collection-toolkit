# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""About uses real resource metadata and update service, without downloading definitions."""
from __future__ import annotations

import os
from pathlib import Path

from playwright.sync_api import Page, expect

from e2e_tests.test_gui_processing import expose
from mailarchiver.application import ApplicationController, ApplicationPreferencesStore
from mailarchiver.clamav_definitions import DATABASE_ENV, UPDATE_ENV, UPDATER_ENV
from mailarchiver.gui_app import AboutApi, GUI_DIRECTORY, PyWebViewApplication
from tests.test_clamav_definitions import write_headers


def test_about_age_warning_and_update_failure_preserve_definitions(tmp_path: Path, page: Page) -> None:
    database = tmp_path / "definitions"
    write_headers(database)
    # Old signed-header metadata exercises presentation only; no scan claims are made.
    original = (database / "daily.cvd").read_bytes()
    settings = {DATABASE_ENV: str(database), UPDATE_ENV: str(tmp_path / "updates"),
                UPDATER_ENV: str(tmp_path / "unavailable-freshclam")}
    saved = {key: os.environ.get(key) for key in settings}
    os.environ.update(settings)
    try:
        app = PyWebViewApplication(ApplicationController(ApplicationPreferencesStore(tmp_path / "preferences.json")))
        api = AboutApi(app)
        expose(page, api, ("status", "update_definitions"))
        page.goto((GUI_DIRECTORY / "about.html").as_uri())
        warning = page.locator("#definition-warning")
        expect(warning).to_be_visible()
        expect(warning).to_contain_text("more than three months old")
        expect(warning).to_have_css("background-color", "rgb(255, 223, 112)")
        expect(page.locator("#definitions")).to_contain_text("daily:12")
        for theme in ("light", "dark"):
            page.emulate_media(color_scheme=theme)
            expect(warning).to_have_css("color", "rgb(36, 27, 0)")
            evidence = Path(__file__).resolve().parents[1] / ".tmp" / f"about-clamav-{theme}.png"
            evidence.parent.mkdir(exist_ok=True)
            page.screenshot(path=str(evidence), full_page=True)
        button = page.get_by_role("button", name="Update virus definitions", exact=True)
        button.click()
        expect(page.locator("#definition-update-status")).to_contain_text("Update failed")
        expect(button).to_be_enabled()
        assert (database / "daily.cvd").read_bytes() == original
        assert not (tmp_path / "updates/active.json").exists()
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
