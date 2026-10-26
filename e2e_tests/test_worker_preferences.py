# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Exercise shipped preference controls through their real application bridge.
# Browser tests cover persistence, numeric validation and launch-only overrides.
# A separate macOS subprocess exercises actual Cocoa radio and text controls.
# Every test owns a disposable preference file and starts no acquisition work.
# Native test windows are placed on the leftmost attached display.
# These checks establish preference behavior without touching personal settings.
"""Worker preference UI requirements across HTML and native Cocoa controls."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

import pytest
from playwright.sync_api import Page, expect

from mailarchiver.application import ApplicationController, ApplicationPreferences, ApplicationPreferencesStore
from mailarchiver.gui_app import PyWebViewApplication
from mailarchiver.windows_update_preferences import PAGE, UpdatePreferencesApi
from mailarchiver.worker_preferences import WorkerPreferences, logical_cpus
from e2e_tests.test_gui_processing import expose


@pytest.mark.parametrize("override", [None, 2])
def test_worker_preferences_controls_persist_and_respect_cli(tmp_path: Path, page: Page, override: int | None) -> None:
    """Preferences save real worker choices; CLI locks controls and preserves the saved choice."""
    store = ApplicationPreferencesStore(tmp_path / "preferences.json")
    store.write(ApplicationPreferences())
    controller = ApplicationController(store)
    app = PyWebViewApplication(controller, workers_override=override)
    api = UpdatePreferencesApi(app.updates, controller.configure_updates, app.worker_settings, app.save_preferences)
    expose(page, api, ("state", "save", "check", "close_saved"))
    try:
        page.goto("data:text/html," + quote(PAGE))
        page.wait_for_function("document.body.dataset.ready === 'true'")
        expect(page.get_by_role("button", name="Save settings")).to_have_count(0)
        expect(page.locator("#check")).to_be_disabled()
        half = page.locator('input[value="half"]')
        count = page.get_by_label("Custom worker count")
        expect(half).to_be_checked()
        expect(count).to_have_attribute("max", str(logical_cpus() * 2))
        expect(page.locator("#all-cpus")).to_have_text(str(logical_cpus()))
        if override is None:
            expect(half).to_be_enabled()
            page.locator('input[value="all"]').check()
            page.evaluate("savePreferences()")
            expect(page.locator("#status")).to_contain_text("Settings saved.")
            assert store.read().workers.mode == "all"
            count.fill(str(2 * logical_cpus() + 1))
            page.locator('input[value="custom"]').check()
            expect(page.locator("#status")).to_contain_text("Enter a whole number")
            assert store.read().workers.mode == "all"
            count.fill("2.5")
            count.dispatch_event("change")
            expect(page.locator("#status")).to_contain_text("Enter a whole number")
            assert store.read().workers.mode == "all"
            page.evaluate("window.savePreferencesAndClose()")
            assert not api._close_ready
            expect(count).to_be_enabled()
            assert store.read().workers.mode == "all"
            count.fill("1")
            page.evaluate("savePreferences()")
            assert store.read().workers == WorkerPreferences(mode="custom", custom_count=1)
            page.reload()
            expect(page.locator('input[value="custom"]')).to_be_checked()
            expect(count).to_have_value("1")
            # Delay only delivery of a real saved result to exercise newer invalid edits.
            page.evaluate("""() => {
              window.originalPreferenceSave=pywebview.api.save;
              pywebview.api.save=async(...args)=>{
                const accepted=await window.originalPreferenceSave(...args);
                await new Promise(resolve=>{window.releasePreferenceResponse=resolve;});
                return accepted;
              };
              document.getElementById('channel').dispatchEvent(new Event('change'));
              window.pendingPreferenceSave=saveQueue;
            }""")
            page.wait_for_function("typeof window.releasePreferenceResponse === 'function'")
            count.fill(str(2 * logical_cpus() + 1))
            page.locator("#automatic").uncheck()
            expect(page.locator("#status")).to_contain_text("Enter a whole number")
            page.evaluate("async()=>{window.releasePreferenceResponse();await window.pendingPreferenceSave;}")
            expect(page.locator("#automatic")).not_to_be_checked()
            expect(page.locator("#status")).to_contain_text("Enter a whole number")
            page.evaluate("refresh()")
            expect(page.locator("#status")).to_contain_text("Enter a whole number")
            assert store.read().automatic_update_checks
            page.evaluate("()=>{pywebview.api.save=window.originalPreferenceSave;}")
            # Hold a genuine persistence failure so an old Check action cannot hide a newer error.
            store.path.unlink()
            store.path.mkdir()
            count.fill("1")
            page.evaluate("""()=>{
              window.releasePreferenceResponse=null;
              pywebview.api.save=async(...args)=>{
                try{return await window.originalPreferenceSave(...args);}
                finally{await new Promise(resolve=>{window.releasePreferenceResponse=resolve;});}
              };
              window.pendingCheck=document.getElementById('check').onclick();
            }""")
            page.wait_for_function("typeof window.releasePreferenceResponse === 'function'")
            count.fill(str(2 * logical_cpus() + 1))
            page.locator("#automatic").check()
            expect(page.locator("#status")).to_contain_text("Enter a whole number")
            page.evaluate("async()=>{window.releasePreferenceResponse();await window.pendingCheck;}")
            expect(page.locator("#status")).to_contain_text("Enter a whole number")
            store.path.rmdir()
            store.write(controller.preferences)
            page.evaluate("()=>{pywebview.api.save=window.originalPreferenceSave;}")
            count.fill("2")
            page.evaluate("""()=>{
              window.releasePreferenceResponse=null;
              pywebview.api.save=async(...args)=>{
                const accepted=await window.originalPreferenceSave(...args);
                await new Promise(resolve=>{window.releasePreferenceResponse=resolve;});
                return accepted;
              };
              window.pendingClose=window.savePreferencesAndClose();
            }""")
            page.wait_for_function("typeof window.releasePreferenceResponse === 'function'")
            expect(count).to_be_disabled()
            expect(page.locator("#automatic")).to_be_disabled()
            expect(page.locator("#channel")).to_be_disabled()
            assert not api._close_ready
            page.evaluate("refresh()")
            expect(page.locator("#check")).to_be_disabled()
            page.evaluate("async()=>{window.releasePreferenceResponse();await window.pendingClose;}")
            assert api._close_ready
            assert store.read().workers == WorkerPreferences(mode="custom", custom_count=2)
        else:
            expect(half).to_be_disabled()
            expect(count).to_be_disabled()
            expect(page.locator("#workers-note")).to_have_text("# workers set to 2 on command line")
            page.locator("#automatic").uncheck()
            page.evaluate("savePreferences()")
            assert not store.read().automatic_update_checks
            assert store.read().workers.mode == "half"
            with pytest.raises(ValueError, match="on command line"):
                api.save("release", True, "custom", 1)
        previews = Path.cwd() / ".tmp/gui-previews"
        previews.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(previews / f"worker-preferences-{override or 'editable'}.png"), full_page=True)
    finally:
        app.shutdown()


@pytest.mark.parametrize("override", [False, True])
@pytest.mark.skipif(sys.platform != "darwin" or os.environ.get("MAILARCHIVER_NATIVE_APPLICATION_E2E") != "1",
                    reason="opt in to visible macOS preference acceptance")
def test_native_worker_preferences(tmp_path: Path, override: bool) -> None:
    """Modeless Cocoa traffic-light Close saves edits and preserves CLI-locked worker choices."""
    from scripts.run_gui import development_bundle, development_environment
    executable = development_bundle(tmp_path / "launcher")
    result = subprocess.run([str(executable), str(Path(__file__).with_name("native_worker_preferences_probe.py")),
                             str(tmp_path / "preferences.json"), *(["--workers", "2"] if override else [])],
                            env=development_environment(), capture_output=True, text=True, timeout=40, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Native worker preferences passed" in result.stdout, result.stdout + result.stderr
