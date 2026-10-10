# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Publish platform-specific installers into one authenticated Sparkle appcast.
# Validate version and archive metadata before writing the feed.
# Supply the protected Ed25519 seed only through signer standard input.
# Verify archive and XML signatures before release publication.
# Keep Apple and build-environment credentials away from the signer.

"""Append signed macOS and Windows installers, then authenticate the complete feed."""

from __future__ import annotations

import argparse
import base64
import binascii
import html
import os
import subprocess
import tempfile
import tomllib
import xml.etree.ElementTree as xml
from datetime import UTC, datetime
from email.utils import format_datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, SecretStr

from mailarchiver.release_versions import PREVIEW_CHANNEL, release_metadata
from mailarchiver.update_metadata import MINIMUM_MACOS_VERSION
from mailarchiver.update_metadata import SPARKLE_PUBLIC_KEY
from mailarchiver.windows_update_feed import PACKAGE_IDENTITY, PACKAGE_NAMESPACE
from scripts.macos_signing import NATIVE_TRUST_ENV_PREFIXES, release_safe_environment

ROOT = Path(__file__).resolve().parents[1]
SPARKLE_NAMESPACE = "http://www.andymatuschak.org/xml-namespaces/sparkle"
SPARKLE_SIGNATURE = f"{{{SPARKLE_NAMESPACE}}}edSignature"
SPARKLE_VERSION = f"{{{SPARKLE_NAMESPACE}}}version"
SPARKLE_SHORT_VERSION = f"{{{SPARKLE_NAMESPACE}}}shortVersionString"
SPARKLE_CHANNEL = f"{{{SPARKLE_NAMESPACE}}}channel"
SPARKLE_MINIMUM_SYSTEM = f"{{{SPARKLE_NAMESPACE}}}minimumSystemVersion"
SPARKLE_HARDWARE = f"{{{SPARKLE_NAMESPACE}}}hardwareRequirements"
SPARKLE_OS = f"{{{SPARKLE_NAMESPACE}}}os"
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
    platform: Literal["macos", "windows"] = "macos"
    hardware: str | None = "arm64"


def signing_key() -> SecretStr:
    """Require a valid exported Sparkle key without exposing it in an exception."""
    value = SecretStr(os.environ.get(SPARKLE_PRIVATE_KEY_SECRET, ""))
    try:
        decoded = base64.b64decode(value.get_secret_value(), validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("Sparkle update-signing secret is not valid Base64") from None
    if len(decoded) != 32:
        raise ValueError("Release signing requires a 32-byte Sparkle seed; legacy expanded keys need a reviewed migration")
    return value


def require_matching_key(key: SecretStr, expected_public_key: str) -> None:
    """Reject a release key that existing installed applications cannot authenticate."""
    from Cryptodome.Signature import eddsa  # pylint: disable=import-outside-toplevel

    seed = base64.b64decode(key.get_secret_value(), validate=True)
    # Sparkle 2.10 common_cli/Secret.swift decodes modern exports as 32-byte seeds.
    # sign_update/main.swift retains an obsolete 64/96-byte diagnostic on failure.
    if len(seed) != 32:
        raise ValueError("Release signing requires a 32-byte Sparkle seed; legacy expanded keys need a reviewed migration")
    public = eddsa.import_private_key(seed).public_key().export_key(format="raw")
    if base64.b64encode(public).decode("ascii") != expected_public_key:
        raise ValueError("Sparkle release signing key does not match the application's embedded public key")


def signed_archive(archive: Path, signer: Path, key: SecretStr) -> SignedArchive:
    """Ask Sparkle to sign and then verify the final installer bytes."""
    result = subprocess.run([signer, "--ed-key-file", "-", archive], input=key.get_secret_value(),
                            capture_output=True, text=True, check=False,
                            env=release_safe_environment(os.environ, NATIVE_TRUST_ENV_PREFIXES))
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
                            env=release_safe_environment(os.environ, NATIVE_TRUST_ENV_PREFIXES))
    if result.returncode:
        raise RuntimeError("Sparkle archive signature verification failed")


def append_item(appcast: Path, release: AppcastRelease) -> None:
    """Add one release without altering prior channel history or replacing an item."""
    xml.register_namespace("sparkle", SPARKLE_NAMESPACE)
    document = xml.parse(appcast)
    channel = document.getroot().find("channel")
    if channel is None:
        raise ValueError("appcast has no RSS channel")
    for existing in channel.findall("item"):
        enclosure = existing.find("enclosure")
        if enclosure is None:
            raise ValueError("Historical item lacks an installer enclosure")
        # Previous authenticated feeds contain only DMGs with no OS attribute.
        # Classify them before Windows sees the mixed feed, then re-sign all XML.
        platform = enclosure.get(SPARKLE_OS)
        if platform is None:
            if not enclosure.get("url", "").endswith(".dmg"):
                raise ValueError("Cannot classify historical installer platform")
            platform = "macos"
            enclosure.set(SPARKLE_OS, platform)
        if existing.findtext("guid") == release.tag and platform == release.platform:
            raise ValueError(f"appcast already contains {release.tag} for {platform}")
    item = xml.Element("item")
    xml.SubElement(item, "title").text = f"Email Collection Toolkit {release.display_version}"
    xml.SubElement(item, "guid").text = release.tag
    xml.SubElement(item, SPARKLE_VERSION).text = str(release.sparkle_version)
    xml.SubElement(item, SPARKLE_SHORT_VERSION).text = release.display_version
    xml.SubElement(item, SPARKLE_MINIMUM_SYSTEM).text = release.minimum_system
    if release.hardware:
        xml.SubElement(item, SPARKLE_HARDWARE).text = release.hardware
    if release.release_notes:
        xml.SubElement(item, "description").text = f"<pre>{html.escape(release.release_notes)}</pre>"
    if release.channel == PREVIEW_CHANNEL:
        xml.SubElement(item, SPARKLE_CHANNEL).text = PREVIEW_CHANNEL
    xml.SubElement(item, "pubDate").text = format_datetime(datetime.now(UTC), usegmt=True)
    enclosure = xml.SubElement(item, "enclosure", {"url": release.url, "length": str(release.archive.length),
                                         "type": OCTET_STREAM, SPARKLE_SIGNATURE: release.archive.signature,
                                         SPARKLE_OS: release.platform})
    if release.platform == "windows":
        enclosure.set(f"{{{PACKAGE_NAMESPACE}}}packageIdentity", PACKAGE_IDENTITY)
        enclosure.set(f"{{{PACKAGE_NAMESPACE}}}architecture", "x64")
    channel.insert(0, item)
    xml.indent(document, space="  ")
    document.write(appcast, encoding="utf-8", xml_declaration=True)


def sign_feed(appcast: Path, signer: Path, key: SecretStr) -> None:
    """Sign the XML itself, then verify Sparkle's embedded signature before publication."""
    environment = release_safe_environment(os.environ, NATIVE_TRUST_ENV_PREFIXES)
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
    parser.add_argument("--windows-archive", type=Path)
    parser.add_argument("--windows-url")
    args = parser.parse_args()
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    expected_tag, channel, sparkle_version, display_version = release_metadata(version)
    if args.tag != expected_tag:
        parser.error(f"{args.tag} does not match expected release tag {expected_tag}")
    if bool(args.windows_archive) != bool(args.windows_url):
        parser.error("Windows archive and URL must be supplied together")
    # Do all signing before modifying XML, and publish through a distinct file.
    # No failed Windows signature may leave an apparently complete candidate.
    key = signing_key()
    require_matching_key(key, SPARKLE_PUBLIC_KEY)
    releases = [AppcastRelease(tag=args.tag, channel=channel, sparkle_version=sparkle_version,
                              display_version=display_version, url=args.url,
                              release_notes=(ROOT / "doc/RELEASE_NOTES.md").read_text(encoding="utf-8"),
                              archive=signed_archive(args.archive, args.signer, key))]
    if args.windows_archive:
        releases.append(AppcastRelease(tag=args.tag, channel=channel, sparkle_version=sparkle_version,
                                       display_version=display_version, url=args.windows_url,
                                       archive=signed_archive(args.windows_archive, args.signer, key),
                                       platform="windows", hardware=None, minimum_system="10.0.19041"))
    from scripts.check_appcast import check_appcast  # pylint: disable=import-outside-toplevel
    # Validate the private snapshot, not a path that can change after validation.
    with tempfile.NamedTemporaryFile(prefix=".appcast-", suffix=".xml", dir=args.appcast.parent, delete=False) as pending:
        candidate = Path(pending.name)
        with args.appcast.open("rb") as source:
            pending.write(source.read(16 * 1024 * 1024 + 1))
    try:
        # The history fetcher permits an empty seed only for the first tag;
        # every populated history must have a valid complete-feed signature.
        history = xml.parse(candidate).getroot().find("channel")
        check_appcast(candidate, require_signed_feed=history is not None and bool(history.findall("item")))
        for release in releases:
            append_item(candidate, release)
        sign_feed(candidate, args.signer, key)
        check_appcast(candidate, args.tag, require_signed_feed=True,
                      expected_platforms=frozenset(release.platform for release in releases))
        candidate.replace(args.appcast)
    finally:
        candidate.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
