# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Exercise the homepage download controls through a real browser and HTTP server.
# Build Zola pages from public-shaped synthetic release and asset metadata.
# Browser contexts supply desktop and mobile user agents without mocking APIs.
# Check platform labels, direct URLs, missing assets, and responsive layout.
# Unknown platforms, absent assets, and disabled JavaScript retain usable links.
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
WINDOWS = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/145.0.0.0 Safari/537.36"
MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/26.0 Safari/605.1.15"
IPAD = "Mozilla/5.0 (iPad; CPU OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1"
LINUX = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/145.0.0.0 Safari/537.36"


def release(tag: str, windows: bool = True) -> PublishedRelease:
    """Provide installer assets to the actual metadata generator, not the UI."""
    version = tag.removeprefix("v")
    names = [f"Email-Collection-Toolkit-{version}-arm64.dmg", "appcast.xml"]
    if windows:
        names.extend([f"ECT-{version}-windows-x64.msixbundle", f"ECT-{version}-windows-x64.zip"])
    return PublishedRelease(tag_name=tag, draft=False, prerelease=Version(version).is_prerelease,
                            assets=[Asset(name=name, size=100, state="uploaded",
                                          browser_download_url=f"{DOWNLOADS}download/{tag}/{name}") for name in names])


@pytest.fixture(params=["both", "preview-only", "newer-stable", "empty"], scope="module")
def download_site(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, list[PublishedRelease]]]:
    """Serve the actual rendered website; each publication state has a distinct origin."""
    if not shutil.which("zola"):
        pytest.skip("Zola is required; make website-download-check runs these cases after CI provisions it")
    publications = {
        "both": [release("v2.0.0"), release("v2.1.0b1")],
        "preview-only": [release("v2.0.0a1", windows=False)],
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


@pytest.mark.parametrize(("agent", "platform", "label"), [
    (WINDOWS, "windows", "Download Windows installer"), (MAC, "mac", "Download macOS installer"),
    (IPAD, "", "Download the installers"), (LINUX, "", "Download the installers"),
])
def test_detected_platform(browser: Browser, download_site: tuple[str, list[PublishedRelease]],
                                       agent: str, platform: str, label: str) -> None:
    """Correct labels and published URLs remain usable without a website stream selector."""
    url, publications = download_site
    with browser.new_context(user_agent=agent) as context:
        page = context.new_page()
        page.goto(url)
        button = page.get_by_role("link", name=label, exact=True)
        expect(button).to_be_visible()
        expect(page.get_by_role("link", name="Show all installers", exact=True)).to_have_attribute("href", DOWNLOADS)
        expect(page.locator("select")).to_have_count(0)
        selected = next((item for item in publications if not item.prerelease), None)
        selected = selected or next(iter(publications), None)
        links = installer_links(selected) if selected else None
        asset = (links.mac_url if platform == "mac" else links.windows_url) if links and platform else ""
        expect(button).to_have_attribute("href", asset or DOWNLOADS)
        if platform and not asset:
            expect(page.locator("#download-status")).to_contain_text("No published installer")


def test_download_links_without_javascript(browser: Browser, download_site: tuple[str, list[PublishedRelease]]) -> None:
    """Disabling scripting leaves both generic buttons and all available platform links."""
    url, publications = download_site
    with browser.new_context(java_script_enabled=False) as context:
        page = context.new_page()
        page.goto(url)
        for label in ("Download the installers", "Show all installers"):
            expect(page.get_by_role("link", name=label, exact=True)).to_have_attribute("href", DOWNLOADS)
        expect(page.locator("select")).to_have_count(0)
        page.get_by_text("Platform links and installation instructions", exact=True).click()
        for publication in publications:
            links = installer_links(publication)
            expect(page.locator(f'a[href="{links.mac_url}"]')).to_be_visible()
            if links.windows_url:
                expect(page.locator(f'a[href="{links.windows_url}"]')).to_be_visible()
                expect(page.locator(f'a[href="{links.windows_help_url}"]')).to_be_visible()


@pytest.mark.parametrize("width", [390, 1280])
def test_download_controls_fit_the_viewport(browser: Browser, download_site: tuple[str, list[PublishedRelease]], width: int) -> None:
    """The rendered download buttons remain readable on mobile and desktop."""
    url, publications = download_site
    with browser.new_context(user_agent=MAC, viewport={"width": width, "height": 960}) as context:
        page = context.new_page()
        page.goto(url)
        expect(page.get_by_role("link", name="Download macOS installer", exact=True)).to_be_visible()
        for control in ("#download-installer", ".actions .secondary"):
            bounds = page.locator(control).bounding_box()
            assert bounds is not None
            assert bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= width
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        if len(publications) == 2 and publications[-1].tag_name == "v2.1.0b1":
            output = ROOT / ".tmp/website-downloads"
            output.mkdir(parents=True, exist_ok=True)
            page.locator("#installer-downloads").screenshot(path=str(output / f"downloads-{width}.png"))
