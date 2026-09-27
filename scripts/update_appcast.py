# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Append a Sparkle-signed, notarized macOS release archive to an appcast."""

from __future__ import annotations

import argparse
import base64
import binascii
import html
import os
import subprocess
import tomllib
import xml.etree.ElementTree as xml
from datetime import UTC, datetime
from email.utils import format_datetime
from pathlib import Path

from pydantic import BaseModel, Field, SecretStr

from mailarchiver.release_versions import PREVIEW_CHANNEL, release_metadata
from mailarchiver.update_metadata import MINIMUM_MACOS_VERSION
from mailarchiver.update_metadata import SPARKLE_PUBLIC_KEY

ROOT = Path(__file__).resolve().parents[1]
SPARKLE_NAMESPACE = "http://www.andymatuschak.org/xml-namespaces/sparkle"
SPARKLE_SIGNATURE = f"{{{SPARKLE_NAMESPACE}}}edSignature"
SPARKLE_VERSION = f"{{{SPARKLE_NAMESPACE}}}version"
SPARKLE_SHORT_VERSION = f"{{{SPARKLE_NAMESPACE}}}shortVersionString"
SPARKLE_CHANNEL = f"{{{SPARKLE_NAMESPACE}}}channel"
SPARKLE_MINIMUM_SYSTEM = f"{{{SPARKLE_NAMESPACE}}}minimumSystemVersion"
SPARKLE_HARDWARE = f"{{{SPARKLE_NAMESPACE}}}hardwareRequirements"
SPARKLE_PRIVATE_KEY_SECRET = "SPARKLE_ED25519_PRIVATE_KEY_BASE64"
OCTET_STREAM = "application/octet-stream"


class SignedArchive(BaseModel):
    """The public Sparkle signature generated for one immutable release archive."""

    signature: str = Field(min_length=1)
    length: int = Field(gt=0)


class AppcastRelease(BaseModel):
    """One appcast item derived from the package's validated release metadata."""

    tag: str
    channel: str
    sparkle_version: int
    display_version: str
    url: str
    archive: SignedArchive
    minimum_system: str = MINIMUM_MACOS_VERSION
    release_notes: str = ""


def signing_key() -> SecretStr:
    """Require a valid exported Sparkle key without exposing it in an exception."""
    value = SecretStr(os.environ.get(SPARKLE_PRIVATE_KEY_SECRET, ""))
    try:
        decoded = base64.b64decode(value.get_secret_value(), validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("Sparkle update-signing secret is not valid Base64") from None
    if len(decoded) not in (32, 64):
        raise ValueError("Sparkle update-signing secret is not an Ed25519 private key")
    return value


def require_matching_key(key: SecretStr, expected_public_key: str) -> None:
    """Reject a release key that existing installed applications cannot authenticate."""
    from Cryptodome.Signature import eddsa  # pylint: disable=import-outside-toplevel

    seed = base64.b64decode(key.get_secret_value(), validate=True)
    if len(seed) != 32:
        raise ValueError("Release signing requires a 32-byte Sparkle seed; legacy expanded keys need a reviewed migration")
    public = eddsa.import_private_key(seed).public_key().export_key(format="raw")
    if base64.b64encode(public).decode("ascii") != expected_public_key:
        raise ValueError("Sparkle release signing key does not match the application's embedded public key")


def signed_archive(archive: Path, signer: Path, key: SecretStr) -> SignedArchive:
    """Ask Sparkle to sign and then verify the final DMG."""
    result = subprocess.run([signer, "--ed-key-file", "-", archive], input=key.get_secret_value(),
                            capture_output=True, text=True, check=False,
                            env={name: value for name, value in os.environ.items()
                                 if name != SPARKLE_PRIVATE_KEY_SECRET})
    if result.returncode:
        raise RuntimeError("Sparkle archive signing failed")
    try:
        enclosure = xml.fromstring(f'<enclosure xmlns:sparkle="{SPARKLE_NAMESPACE}" {result.stdout.strip()}/>')
        signed = SignedArchive(signature=enclosure.attrib[SPARKLE_SIGNATURE], length=int(enclosure.attrib["length"]))
    except (KeyError, ValueError, xml.ParseError) as error:
        raise RuntimeError("Sparkle signer produced an invalid archive signature") from error
    verify_signed_archive(archive, signer, key, signed.signature)
    return signed


def verify_signed_archive(archive: Path, signer: Path, key: SecretStr, signature: str) -> None:
    """Have Sparkle verify the archive bytes with the exported update key."""
    result = subprocess.run([signer, "--verify", "--ed-key-file", "-", archive, signature],
                            input=key.get_secret_value(), capture_output=True, text=True, check=False,
                            env={name: value for name, value in os.environ.items()
                                 if name != SPARKLE_PRIVATE_KEY_SECRET})
    if result.returncode:
        raise RuntimeError("Sparkle archive signature verification failed")


def append_item(appcast: Path, release: AppcastRelease) -> None:
    """Add one release without altering prior channel history or replacing an item."""
    xml.register_namespace("sparkle", SPARKLE_NAMESPACE)
    document = xml.parse(appcast)
    channel = document.getroot().find("channel")
    if channel is None:
        raise ValueError("appcast has no RSS channel")
    if any(item.findtext("guid") == release.tag for item in channel.findall("item")):
        raise ValueError(f"appcast already contains {release.tag}")
    item = xml.Element("item")
    xml.SubElement(item, "title").text = f"Email Collection Toolkit {release.display_version}"
    xml.SubElement(item, "guid").text = release.tag
    xml.SubElement(item, SPARKLE_VERSION).text = str(release.sparkle_version)
    xml.SubElement(item, SPARKLE_SHORT_VERSION).text = release.display_version
    xml.SubElement(item, SPARKLE_MINIMUM_SYSTEM).text = release.minimum_system
    xml.SubElement(item, SPARKLE_HARDWARE).text = "arm64"
    if release.release_notes:
        xml.SubElement(item, "description").text = f"<pre>{html.escape(release.release_notes)}</pre>"
    if release.channel == PREVIEW_CHANNEL:
        xml.SubElement(item, SPARKLE_CHANNEL).text = PREVIEW_CHANNEL
    xml.SubElement(item, "pubDate").text = format_datetime(datetime.now(UTC), usegmt=True)
    xml.SubElement(item, "enclosure", {"url": release.url, "length": str(release.archive.length),
                                         "type": OCTET_STREAM, SPARKLE_SIGNATURE: release.archive.signature})
    channel.insert(0, item)
    xml.indent(document, space="  ")
    document.write(appcast, encoding="utf-8", xml_declaration=True)


def sign_feed(appcast: Path, signer: Path, key: SecretStr) -> None:
    """Sign the XML itself, then verify Sparkle's embedded signature before publication."""
    environment = {name: value for name, value in os.environ.items() if name != SPARKLE_PRIVATE_KEY_SECRET}
    for options in ([], ["--verify"]):
        result = subprocess.run([signer, "--ed-key-file", "-", *options, appcast],
                                input=key.get_secret_value(), capture_output=True, text=True,
                                check=False, env=environment)
        if result.returncode:
            raise RuntimeError("Sparkle feed signature verification failed" if options else "Sparkle feed signing failed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--appcast", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--signer", type=Path, required=True)
    args = parser.parse_args()
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    expected_tag, channel, sparkle_version, display_version = release_metadata(version)
    if args.tag != expected_tag:
        parser.error(f"{args.tag} does not match expected release tag {expected_tag}")
    key = signing_key()
    require_matching_key(key, SPARKLE_PUBLIC_KEY)
    append_item(args.appcast, AppcastRelease(tag=args.tag, channel=channel, sparkle_version=sparkle_version,
                                             display_version=display_version, url=args.url,
                                             release_notes=(ROOT / "doc/RELEASE_NOTES.md").read_text(encoding="utf-8"),
                                             archive=signed_archive(args.archive, args.signer, key)))
    sign_feed(args.appcast, args.signer, key)


if __name__ == "__main__":
    main()
