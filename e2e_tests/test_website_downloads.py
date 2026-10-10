# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Exercise the homepage download controls through a real browser and HTTP server.
# Build Zola pages from public-shaped synthetic release and asset metadata.
# Browser contexts exercise scripting enabled and disabled without mocking APIs.
# Check both static buttons, direct URLs, missing assets, and responsive layout.
# Absent assets retain usable release links with an explicit availability notice.
# These checks download no installers and never publish a website or release.
"""Requirements: platform download buttons; update preferences belong in the app."""
from __future__ import annotations

from collections.abc import Iterator
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import subprocess
import sys
from threading import Thread

from packaging.version import Version
from playwright.sync_api import Browser, expect
from pydantic import TypeAdapter
import pytest

from scripts.update_site_releases import Asset, PublishedRelease, installer_links

ROOT = Path(__file__).parents[1]
DOWNLOADS = "https://github.com/simsong/email-collection-toolkit/releases/"


def release(tag: str, windows: bool = True) -> PublishedRelease:
    """Provide installer assets to the actual metadata generator, not the UI."""
    version = tag.removeprefix("v")
    names = [f"Email-Collection-Toolkit-{version}-arm64.dmg", "appcast.xml"]
    if windows:
        names.extend([f"ECT-{version}-windows-x64.msixbundle", f"ECT-{version}-windows-x64.zip"])
    return PublishedRelease(tag_name=tag, draft=False, prerelease=Version(version).is_prerelease,
                            assets=[Asset(name=name, size=100, state="uploaded",
                                          browser_download_url=f"{DOWNLOADS}download/{tag}/{name}") for name in names])


@pytest.fixture(params=["both", "preview-only", "preview-both", "newer-stable", "empty"], scope="module")
def download_site(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, list[PublishedRelease]]]:
    """Serve the actual rendered website; each publication state has a distinct origin."""
    if not shutil.which("zola"):
        pytest.skip("Zola is required; make website-download-check runs these cases after CI provisions it")
    publications = {
        "both": [release("v2.0.0"), release("v2.1.0b1")],
        "preview-only": [release("v2.0.0a1", windows=False)],
        "preview-both": [release("v2.0.0a1")],
        "newer-stable": [release("v2.0.0"), release("v1.9.0b1")],
        "empty": [],
    }
    releases = publications[request.param]
    directory = tmp_path_factory.mktemp("website-downloads")
    site, output = directory / "site", directory / "public"
    shutil.copytree(ROOT / "website", site)
    source = directory / "releases.json"
    source.write_bytes(TypeAdapter(list[PublishedRelease]).dump_json(releases))
    subprocess.run([sys.executable, str(ROOT / "scripts/update_site_releases.py"), "--releases-json", str(source),
                    "--output", str(site / "data/releases.toml")], check=True, capture_output=True, text=True)
    with ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(output))) as server:
        base_url = f"http://127.0.0.1:{server.server_port}/"
        subprocess.run(["zola", "--root", str(site), "build", "--base-url", base_url,
                        "--output-dir", str(output)], check=True, capture_output=True, text=True)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield base_url, releases
        finally:
            server.shutdown()
            thread.join(timeout=5)


@pytest.mark.parametrize("scripting", [True, False])
def test_static_downloads(browser: Browser, download_site: tuple[str, list[PublishedRelease]], scripting: bool) -> None:
    """Both platform links work with or without scripts, using complete published releases."""
    url, publications = download_site
    with browser.new_context(java_script_enabled=scripting) as context:
        page = context.new_page()
        page.goto(url)
        expect(page.get_by_role("link", name="Show all installers", exact=True)).to_have_attribute("href", DOWNLOADS)
        expect(page.locator("select")).to_have_count(0)
        selected = next((item for item in publications if not item.prerelease), None)
        selected = selected or next(iter(publications), None)
        links = installer_links(selected) if selected else None
        for label, asset, platform in (
            ("Download Windows installer", links.windows_url if links else "", "Windows"),
            ("Download macOS installer", links.mac_url if links else "", "macOS"),
        ):
            button = page.get_by_role("link", name=label, exact=True)
            expect(button).to_be_visible()
            expect(button).to_have_attribute("href", asset or DOWNLOADS)
            if not asset:
                expect(page.get_by_text(f"No published {platform} installer", exact=False)).to_be_visible()
        if selected and selected.prerelease:
            expect(page.get_by_text("Preview release:", exact=False)).to_be_visible()
        else:
            expect(page.get_by_text("Preview release:", exact=False)).to_have_count(0)
        if links and links.windows_help_url:
            expect(page.locator("#installer-downloads > p").get_by_role(
                "link", name="Windows certificate and installation instructions", exact=True)).to_have_attribute(
                    "href", links.windows_help_url)
            expect(page.get_by_text("Before installing on Windows", exact=False)).to_be_visible()
        page.get_by_text("Platform links and installation instructions", exact=True).click()
        for publication in publications:
            published = installer_links(publication)
            expect(page.locator(f'details a[href="{published.mac_url}"]')).to_be_visible()
            if published.windows_url:
                expect(page.locator(f'details a[href="{published.windows_url}"]')).to_be_visible()
                expect(page.locator(f'details a[href="{published.windows_help_url}"]')).to_be_visible()


@pytest.mark.parametrize("width", [390, 1280])
def test_download_controls_fit_the_viewport(browser: Browser, download_site: tuple[str, list[PublishedRelease]], width: int) -> None:
    """The rendered download buttons remain readable on mobile and desktop."""
    url, publications = download_site
    with browser.new_context(viewport={"width": width, "height": 960}) as context:
        page = context.new_page()
        page.goto(url)
        expect(page.get_by_role("link", name="Download macOS installer", exact=True)).to_be_visible()
        for control in ("#download-windows", "#download-macos", ".actions .secondary"):
            bounds = page.locator(control).bounding_box()
            assert bounds is not None
            assert bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= width
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        if len(publications) == 2 and publications[-1].tag_name == "v2.1.0b1":
            output = ROOT / ".tmp/website-downloads"
            output.mkdir(parents=True, exist_ok=True)
            page.locator("#installer-downloads").screenshot(path=str(output / f"downloads-{width}.png"))
