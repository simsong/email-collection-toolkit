#!/usr/bin/env python3
# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Generate static website release links from public GitHub release metadata.
# Canonical tags are ordered independently for stable and preview channels.
# Direct buttons require uploaded, nonempty installers with exact repository URLs.
# Complete Mac/Windows releases take precedence over older Mac-only releases.
# Before Windows publication, historical Mac downloads remain available alone.
# Tags-only input retains release-note links but never invents installer assets.
# Pages authenticates the published appcast separately before deploying this data.
"""Write Zola release data from published release JSON or newline-delimited tags."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from pydantic import BaseModel, TypeAdapter
from mailarchiver.release_versions import release_metadata

STABLE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
PREVIEW = re.compile(r"^v(\d+)\.(\d+)\.(\d+)(a|b)([1-9]\d*)$")
REPOSITORY = "https://github.com/simsong/email-collection-toolkit"


class Asset(BaseModel):
    name: str
    size: int
    state: str
    browser_download_url: str


class PublishedRelease(BaseModel):
    tag_name: str
    draft: bool
    prerelease: bool
    assets: list[Asset]


class Links(BaseModel):
    tag: str = ""
    mac_url: str = ""
    windows_url: str = ""
    windows_help_url: str = ""

    @property
    def complete(self) -> bool:
        return bool(self.mac_url and self.windows_url and self.windows_help_url)


def choose(tags: list[str], pattern: re.Pattern[str]) -> str | None:
    candidates = [(tuple(int(part) for part in match.groups()), tag)
                  for tag in tags if (match := pattern.fullmatch(tag))]
    return max(candidates)[1] if candidates else None


def choose_preview(tags: list[str]) -> str | None:
    """Order canonical alpha/beta tags within their release series."""
    candidates = [((int(major), int(minor), int(patch), stage == "b", int(number)), tag)
                  for tag in tags
                  if (match := PREVIEW.fullmatch(tag))
                  for major, minor, patch, stage, number in [match.groups()]]
    return max(candidates)[1] if candidates else None


def installer_links(release: PublishedRelease) -> Links:
    """Exclude drafts, incomplete uploads and URLs outside the tagged release."""
    tag = release.tag_name
    if release.draft or not (STABLE.fullmatch(tag) or PREVIEW.fullmatch(tag)):
        return Links()
    try:
        _, channel, _, version = release_metadata(tag.removeprefix("v"))
    except ValueError:
        return Links()
    if release.prerelease != (channel == "preview"):
        return Links()
    names = [f"Email-Collection-Toolkit-{version}-arm64.dmg",
             f"ECT-{version}-windows-x64.msixbundle",
             f"ECT-{version}-windows-x64.zip", "appcast.xml"]
    available: list[str] = []
    for name in names:
        matching = [asset for asset in release.assets if asset.name == name]
        url = f"{REPOSITORY}/releases/download/{tag}/{name}"
        available.append(url if len(matching) == 1 and matching[0].size > 0
                         and matching[0].state == "uploaded" and matching[0].browser_download_url == url else "")
    mac, windows, help_url, feed = available
    if not feed or not mac:
        return Links()
    # A test-signed alpha must include the trust instructions alongside its bundle.
    if not help_url:
        windows = ""
    return Links(tag=tag, mac_url=mac, windows_url=windows, windows_help_url=help_url if windows else "")


def select_links(releases: list[PublishedRelease], preview: bool) -> Links:
    links = [installer_links(release) for release in releases]
    links = [link for link in links if link.tag and bool(PREVIEW.fullmatch(link.tag)) == preview]
    complete = [link for link in links if link.complete]
    candidates = complete or links
    tags = [link.tag for link in candidates]
    tag = choose_preview(tags) if preview else choose(tags, STABLE)
    return next((link for link in candidates if link.tag == tag), Links())


def release_block(link: Links, label: str) -> str:
    tag = link.tag
    version = tag or f"No {label} release yet"
    url = f"{REPOSITORY}/releases/tag/{tag}" if tag else f"{REPOSITORY}/releases"
    return (f'version = {json.dumps(version)}\ntag = {json.dumps(tag)}\nurl = {json.dumps(url)}\n'
            f'mac_url = {json.dumps(link.mac_url)}\nwindows_url = {json.dumps(link.windows_url)}\n'
            f'windows_help_url = {json.dumps(link.windows_help_url)}')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--releases-json", type=Path)
    parser.add_argument("--require-complete-tag", default="")
    args = parser.parse_args()
    if args.releases_json:
        releases = TypeAdapter(list[PublishedRelease]).validate_json(args.releases_json.read_bytes())
        if args.require_complete_tag and not any(installer_links(release).complete
                                                and release.tag_name == args.require_complete_tag for release in releases):
            parser.error("Requested release is not public with complete uploaded installers, trust package and appcast")
        stable, preview = select_links(releases, False), select_links(releases, True)
    else:
        if args.require_complete_tag:
            parser.error("--require-complete-tag requires --releases-json")
        tags = [line.strip() for line in sys.stdin if line.strip()]
        stable, preview = Links(tag=choose(tags, STABLE) or ""), Links(tag=choose_preview(tags) or "")
    current = stable if stable.tag else preview
    current_url = f"{REPOSITORY}/releases/tag/{current.tag}" if current.tag else f"{REPOSITORY}/releases"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        f'current_version = {json.dumps(current.tag or "Unreleased")}\ncurrent_url = {json.dumps(current_url)}\n\n'
        f"[stable]\n{release_block(stable, 'stable')}\n\n[preview]\n{release_block(preview, 'preview')}\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
