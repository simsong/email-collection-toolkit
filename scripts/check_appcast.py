#!/usr/bin/env python3
# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Check published Sparkle feed structure and public-key authenticity, not DMG bytes."""

from __future__ import annotations

import argparse
import base64
import binascii
import re
import xml.etree.ElementTree as xml
from pathlib import Path
from urllib.parse import unquote, urlsplit

from mailarchiver.update_metadata import SPARKLE_PUBLIC_KEY

SPARKLE_NAMESPACE = "http://www.andymatuschak.org/xml-namespaces/sparkle"
SIGNATURE = f"{{{SPARKLE_NAMESPACE}}}edSignature"
VERSION = f"{{{SPARKLE_NAMESPACE}}}version"
RELEASE_PATH = "/simsong/email-collection-toolkit/releases/download/"
MAX_FEED_BYTES = 16 * 1024 * 1024
# Pinned Sparkle 2.10 signing.swift appends this block after the signed bytes.
SIGNING_BLOCK = re.compile(rb"<!-- sparkle-signatures:\nedSignature: ([A-Za-z0-9+/]{86}==)\nlength: ([0-9]+)\n-->\n?\Z")


def verify_feed(data: bytes, public_key: str) -> None:
    """Authenticate exact XML bytes with the application's public Ed25519 key."""
    from Cryptodome.Signature import eddsa  # pylint: disable=import-outside-toplevel

    block = SIGNING_BLOCK.search(data)
    if block is None:
        raise ValueError("Sparkle appcast has no embedded feed signature or an invalid signing block")
    content = data[:block.start()]
    if int(block.group(2)) != len(content):
        raise ValueError("Sparkle feed signed length does not match content")
    try:
        key = eddsa.import_public_key(base64.b64decode(public_key, validate=True))
        eddsa.new(key, "rfc8032").verify(content, base64.b64decode(block.group(1), validate=True))
    except (ValueError, binascii.Error) as error:
        raise ValueError("Sparkle feed signature verification failed") from error


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


def check_appcast(path: Path, tag: str = "", *, require_signed_feed: bool = False,
                  public_key: str = SPARKLE_PUBLIC_KEY) -> None:
    """Verify a bounded feed and exactly one item for the requested release."""
    try:
        with path.open("rb") as handle:
            data = handle.read(MAX_FEED_BYTES + 1)
        if len(data) > MAX_FEED_BYTES:
            raise ValueError("Sparkle appcast exceeds the 16 MiB feed limit")
        if require_signed_feed:
            verify_feed(data, public_key)
        channel = xml.fromstring(data).find("channel")
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
    parser.add_argument("--require-signed-feed", action="store_true")
    args = parser.parse_args()
    try:
        check_appcast(args.appcast, args.tag, require_signed_feed=args.require_signed_feed)
    except ValueError as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
