# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Exercise the HTML release notes embedded in the actual appcast writer.
# Render current repository notes in the browser used by GUI acceptance.
# Check visible headings, readable wrapping and the absence of document boilerplate.
# Save light and dark previews for visual review of the updater's content.
# Native installer signatures are covered separately by real Sparkle signer tests.

"""Delivery: the signed feed's update-note HTML must render as readable rich text."""

from pathlib import Path
import tomllib
from typing import Literal
import xml.etree.ElementTree as xml

from playwright.sync_api import Page, expect
import pytest

from mailarchiver.release_versions import release_metadata
from scripts.update_appcast import AppcastRelease, SignedArchive, append_item

WIDTH, HEIGHT = "width", "height"


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_appcast_notes_render_without_markdown_or_horizontal_scroll(
    tmp_path: Path, page: Page, theme: Literal["light", "dark"],
) -> None:
    """Delivery: inspect the exact embedded description, rather than a separate HTML template."""
    root = Path(__file__).resolve().parents[1]
    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    tag, channel, build, display = release_metadata(version)
    appcast = tmp_path / "appcast.xml"
    appcast.write_text('<rss version="2.0"><channel/></rss>')
    append_item(appcast, AppcastRelease(
        tag=tag, channel=channel, sparkle_version=build, display_version=display,
        url=f"https://github.com/simsong/email-collection-toolkit/releases/download/{tag}/fixture.dmg",
        archive=SignedArchive(signature="fixture", length=1),
        release_notes=(root / "doc/RELEASE_NOTES.md").read_text(),
    ))
    description = xml.parse(appcast).findtext("channel/item/description")
    assert description
    page.set_viewport_size({WIDTH: 720, HEIGHT: 480})
    page.emulate_media(color_scheme=theme)
    page.set_content(description)
    expect(page.get_by_role("heading", name="Readable update notes", exact=True)).to_have_count(1)
    expect(page.locator("h1")).to_have_count(0)
    expect(page.locator("body")).not_to_contain_text("<!-- Copyright")
    expect(page.locator("body")).not_to_contain_text("Entries describe their recorded revisions")
    expect(page.locator("body")).not_to_contain_text("## Readable update notes")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert page.locator("body").evaluate("element => getComputedStyle(element).fontFamily").startswith("-apple-system")
    output = root / ".tmp" / f"update-notes-{theme}.png"
    output.parent.mkdir(exist_ok=True)
    page.screenshot(path=str(output))
