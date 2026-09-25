#!/usr/bin/env python3
# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Check published Sparkle feed structure and signature metadata, not DMG cryptography."""

from __future__ import annotations

import argparse
import xml.etree.ElementTree as xml
from pathlib import Path
from urllib.parse import unquote, urlsplit

SPARKLE_NAMESPACE = "http://www.andymatuschak.org/xml-namespaces/sparkle"
SIGNATURE = f"{{{SPARKLE_NAMESPACE}}}edSignature"
VERSION = f"{{{SPARKLE_NAMESPACE}}}version"
RELEASE_PATH = "/simsong/email-collection-toolkit/releases/download/"


def valid_release_url(url: str, tag: str) -> bool:
    """Accept only a DMG asset under this repository's exact tagged release."""
    if not tag.startswith("v") or any(character in tag for character in "/\\%?#"):
        return False
    parsed = urlsplit(url)
    prefix = f"{RELEASE_PATH}{tag}/"
    if (parsed.scheme != "https" or parsed.netloc != "github.com" or parsed.query or parsed.fragment
            or not parsed.path.startswith(prefix)):
        return False
    name = unquote(parsed.path[len(prefix):])
    return bool(name and "/" not in name and "\\" not in name and name.endswith(".dmg"))


def check_appcast(path: Path, tag: str = "") -> None:
    """Require signature metadata and exactly one item for the requested release."""
    try:
        channel = xml.parse(path).getroot().find("channel")
    except (OSError, xml.ParseError) as error:
        raise ValueError(f"cannot read Sparkle appcast: {path}") from error
    if channel is None:
        raise ValueError("Sparkle appcast has no channel")
    matches = 0
    for item in channel.findall("item"):
        guid = item.findtext("guid")
        enclosure = item.find("enclosure")
        if not guid or enclosure is None or not enclosure.get(SIGNATURE) or not item.findtext(VERSION):
            raise ValueError("Sparkle appcast contains an unsigned or incomplete item")
        try:
            length = int(enclosure.get("length", ""))
        except ValueError as error:
            raise ValueError("Sparkle appcast contains an invalid archive length") from error
        if length <= 0 or not valid_release_url(enclosure.get("url", ""), guid):
            raise ValueError("Sparkle appcast contains an invalid archive enclosure")
        if guid == tag:
            matches += 1
    if tag and matches != 1:
        raise ValueError(f"Sparkle appcast must contain exactly one signed item for {tag}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("appcast", type=Path)
    parser.add_argument("--tag", default="")
    args = parser.parse_args()
    try:
        check_appcast(args.appcast, args.tag)
    except ValueError as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
