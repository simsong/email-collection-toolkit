#!/usr/bin/env python3
# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Reject a missing, malformed, or unsigned published Sparkle appcast."""

from __future__ import annotations

import argparse
import xml.etree.ElementTree as xml
from pathlib import Path

SPARKLE_NAMESPACE = "http://www.andymatuschak.org/xml-namespaces/sparkle"
SIGNATURE = f"{{{SPARKLE_NAMESPACE}}}edSignature"
VERSION = f"{{{SPARKLE_NAMESPACE}}}version"


def check_appcast(path: Path, tag: str = "") -> None:
    """Require each listed archive to be signed and the requested release to exist."""
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
        if length <= 0 or not enclosure.get("url", "").startswith("https://"):
            raise ValueError("Sparkle appcast contains an invalid archive enclosure")
        if guid == tag:
            matches += 1
            if f"/releases/download/{tag}/" not in enclosure.get("url", ""):
                raise ValueError(f"Sparkle appcast item {tag} points to the wrong release")
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
