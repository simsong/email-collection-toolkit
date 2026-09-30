# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Migrate the one reviewed unsigned historical Sparkle feed.
# Pin the published XML bytes and release metadata before touching keys.
# Authenticate one mounted DMG and app, then test that same mount.
# Verify the archive signature and sign a separate feed copy.
# Never change the downloaded sources or publish an asset here.

"""Audit and sign the first unsigned Sparkle appcast without changing its source."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import os
import plistlib
import subprocess
import tempfile
import time
import xml.etree.ElementTree as xml
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path

from pydantic import BaseModel, Field

from mailarchiver.update_metadata import SPARKLE_PUBLIC_KEY
from scripts.check_appcast import MAX_FEED_BYTES, SIGNATURE, SPARKLE_NAMESPACE, check_appcast
from scripts.macos_signing import NATIVE_TRUST_ENV_PREFIXES, release_safe_environment
from scripts.update_appcast import (
    require_matching_key,
    sign_feed,
    signing_key,
    verify_signed_archive,
)

SPARKLE_VERSION = f"{{{SPARKLE_NAMESPACE}}}version"
SPARKLE_SHORT_VERSION = f"{{{SPARKLE_NAMESPACE}}}shortVersionString"
SPARKLE_CHANNEL = f"{{{SPARKLE_NAMESPACE}}}channel"
RELEASE_NOTES_LINK = f"{{{SPARKLE_NAMESPACE}}}releaseNotesLink"
FEED_URL = "https://simsong.github.io/email-collection-toolkit/updates/mac/appcast.xml"
ARCHIVE_MEDIA_TYPE = "application/octet-stream"
APP_NAME = "Email Collection Toolkit"
PLIST_SPARKLE_PUBLIC_KEY = "SUPublicEDKey"
FIRST_RELEASE_FEED_SHA256 = "de6d09cdc3e2efa508de9ba83d7701addd046660b18bcd3dcf3d5cb49efd8403"


class HistoricalRelease(BaseModel):
    """Pinned metadata for the single unsigned release-history migration."""

    tag: str
    title: str
    display_version: str
    sparkle_version: int
    channel: str
    archive_url: str
    archive_length: int = Field(gt=0)
    archive_signature: str


FIRST_RELEASE = HistoricalRelease(
    tag="v1.0.0a10",
    title="Email Collection Toolkit 1.0.0a10",
    display_version="1.0.0a10",
    sparkle_version=1_000_000_110,
    channel="preview",
    archive_url=(
        "https://github.com/simsong/email-collection-toolkit/releases/download/"
        "v1.0.0a10/Email-Collection-Toolkit-1.0.0a10-arm64.dmg"
    ),
    archive_length=160_093_151,
    archive_signature="Z6XrcShVFHwVV8T5sHRQSvUpvIsM224Fo1AkBA19WusIvVXtXFTi4rQ2B7MP3lyLw0AU8psWLUMV1+Y0P6iGCA==",
)


def audit_legacy_feed(data: bytes, archive: Path, release: HistoricalRelease) -> str:
    """Require the exact known a10 item and verify its signed-archive metadata."""
    if release != FIRST_RELEASE:
        raise ValueError("only the reviewed v1.0.0a10 history migration is supported")
    if len(data) > MAX_FEED_BYTES:
        raise ValueError("Sparkle appcast exceeds the 16 MiB feed limit")
    if hashlib.sha256(data).hexdigest() != FIRST_RELEASE_FEED_SHA256:
        raise ValueError("historical appcast bytes do not match the reviewed v1.0.0a10 asset")
    if b"<!-- sparkle-signatures:" in data:
        raise ValueError("historical feed already has a signing block; refusing to replace it")
    try:
        root = xml.fromstring(data)
    except xml.ParseError as error:
        raise ValueError("historical appcast is not valid XML") from error
    if root.tag != "rss" or root.attrib != {"version": "2.0"} or [child.tag for child in root] != ["channel"]:
        raise ValueError("historical feed is not the reviewed RSS 2.0 document")
    channel = root.find("channel")
    if channel is None or channel.attrib:
        raise ValueError("historical appcast has no channel")
    items = channel.findall("item")
    if len(items) != 1:
        raise ValueError("historical migration requires exactly one release item")
    if [child.tag for child in channel] != ["item", "title", "link", "description"]:
        raise ValueError("historical appcast channel structure differs from the reviewed feed")
    if (channel.findtext("title") != "Email Collection Toolkit updates"
            or channel.findtext("link") != FEED_URL
            or channel.findtext("description") != "Signed macOS release and preview updates."):
        raise ValueError("historical appcast channel metadata differs from the reviewed feed")
    item = items[0]
    if item.attrib or [child.tag for child in item] != [
        "title", "guid", SPARKLE_VERSION, SPARKLE_SHORT_VERSION, SPARKLE_CHANNEL, "pubDate", "enclosure",
    ]:
        raise ValueError("historical release item structure differs from the reviewed a10 item")
    enclosure = item.find("enclosure")
    if enclosure is None:
        raise ValueError("historical release has no archive enclosure")
    if item.find(RELEASE_NOTES_LINK) is not None:
        raise ValueError("external release notes require a separate signed migration")
    if item.findtext("guid") != release.tag or item.findtext("title") != release.title:
        raise ValueError("historical release identity differs from the reviewed a10 release")
    if item.findtext(SPARKLE_VERSION) != str(release.sparkle_version):
        raise ValueError("historical Sparkle build number differs from the reviewed a10 release")
    if item.findtext(SPARKLE_SHORT_VERSION) != release.display_version:
        raise ValueError("historical display version differs from the reviewed a10 release")
    if item.findtext(SPARKLE_CHANNEL) != release.channel:
        raise ValueError("historical update channel differs from the reviewed a10 release")
    if item.findtext("pubDate") != "Wed, 23 Sep 2026 10:26:21 GMT":
        raise ValueError("historical publication date differs from the reviewed a10 release")
    if enclosure.get("url") != release.archive_url:
        raise ValueError("historical DMG URL differs from the reviewed a10 release asset")
    if enclosure.get(SIGNATURE) != release.archive_signature:
        raise ValueError("historical Sparkle archive signature differs from the reviewed a10 asset")
    if enclosure.get("type") != ARCHIVE_MEDIA_TYPE:
        raise ValueError("historical DMG media type differs from the reviewed a10 release")
    if set(enclosure.attrib) != {"url", "length", "type", SIGNATURE}:
        raise ValueError("historical DMG enclosure contains unreviewed attributes")
    try:
        signature_bytes = base64.b64decode(release.archive_signature, validate=True)
        length = int(enclosure.get("length", ""))
    except (ValueError, binascii.Error) as error:
        raise ValueError("historical release has invalid archive signature metadata") from error
    if len(signature_bytes) != 64:
        raise ValueError("historical Sparkle archive signature has the wrong length")
    if length != release.archive_length or archive.stat().st_size != release.archive_length:
        raise ValueError("historical DMG length does not match the reviewed release asset")
    return release.archive_signature


def verify_embedded_app_key(app: Path, public_key: str) -> None:
    """Require the mounted app to trust the same key as its archive and feed."""
    info_path = app / "Contents/Info.plist"
    try:
        with info_path.open("rb") as source:
            info = plistlib.load(source)
    except (OSError, plistlib.InvalidFileException, ValueError) as error:
        raise ValueError("cannot read historical app Sparkle key") from error
    if not isinstance(info, dict) or info.get(PLIST_SPARKLE_PUBLIC_KEY) != public_key:
        raise ValueError("historical app Sparkle public key does not match the archive and feed key")


@contextmanager
def mounted_historical_dmg(dmg: Path) -> Iterator[Path]:
    """Mount a read-only historical DMG and detach it without recursive cleanup."""
    temporary = Path(tempfile.mkdtemp(prefix="sparkle-history-dmg-"))
    mount = temporary / "mounted"
    mount.mkdir()
    environment = release_safe_environment(os.environ, NATIVE_TRUST_ENV_PREFIXES)
    try:
        attached = subprocess.run(
            ["/usr/bin/hdiutil", "attach", "-readonly", "-nobrowse", "-mountpoint", str(mount), str(dmg)],
            capture_output=True, text=True, check=False, env=environment,
        )
        if attached.returncode:
            raise RuntimeError(f"could not mount historical DMG: {attached.stderr.strip()}")
        try:
            yield mount
        finally:
            detached = None
            for _ in range(10):
                detached = subprocess.run(["/usr/bin/hdiutil", "detach", str(mount)],
                                          capture_output=True, text=True, check=False, env=environment)
                if detached.returncode == 0:
                    break
                time.sleep(1)
            if detached is None or detached.returncode:
                raise RuntimeError(f"could not detach historical DMG: {detached.stderr if detached else ''}")
    finally:
        if not os.path.ismount(mount):
            try:
                mount.rmdir()
                temporary.rmdir()
            except OSError:
                pass


@contextmanager
def verify_historical_app_key(archive: Path, public_key: str) -> Iterator[Path]:
    """Keep the authenticated image mounted through the executable test."""
    environment = release_safe_environment(os.environ, NATIVE_TRUST_ENV_PREFIXES)
    subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(archive)],
                   check=True, env=environment)
    subprocess.run(["/usr/bin/xcrun", "stapler", "validate", str(archive)],
                   check=True, env=environment)
    subprocess.run(["/usr/sbin/spctl", "--assess", "--type", "open", "--context",
                    "context:primary-signature", str(archive)], check=True, env=environment)
    with mounted_historical_dmg(archive) as mount:
        app = mount / f"{APP_NAME}.app"
        subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)],
                       check=True, env=environment)
        subprocess.run(["/usr/sbin/spctl", "--assess", "--type", "execute", str(app)],
                       check=True, env=environment)
        verify_embedded_app_key(app, public_key)
        yield mount


def test_verified_mount(mount: Path, archive: Path) -> None:
    """Run the full mounted test against the app authenticated above."""
    subprocess.run(["make", "test-mounted-dmg", f"DMG={archive}", f"MOUNT={mount}"],
                   cwd=Path(__file__).parents[1], check=True,
                   env=release_safe_environment(os.environ, NATIVE_TRUST_ENV_PREFIXES))


def sign_historical_appcast(
    appcast: Path,
    archive: Path,
    output: Path,
    signer: Path,
    *,
    release: HistoricalRelease = FIRST_RELEASE,
    public_key: str = SPARKLE_PUBLIC_KEY,
) -> None:
    """Audit and sign a separate output copy, preserving the historical inputs."""
    if appcast.resolve() == output.resolve():
        raise ValueError("signed output must be a separate file from the source appcast")
    if output.exists():
        raise ValueError(f"refusing to replace existing signed output: {output}")
    if not output.parent.is_dir():
        raise ValueError(f"signed output directory does not exist: {output.parent}")
    with appcast.open("rb") as source:
        data = source.read(MAX_FEED_BYTES + 1)
    signature = audit_legacy_feed(data, archive, release)
    with verify_historical_app_key(archive, public_key) as mount:
        key = signing_key()
        require_matching_key(key, public_key)
        verify_signed_archive(archive, signer, key, signature)
        test_verified_mount(mount, archive)
    check_appcast(appcast, release.tag)

    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "wb", dir=output.parent, prefix=f".{output.name}.", suffix=".xml", delete=False
        ) as target:
            temporary = Path(target.name)
            target.write(data)
            target.flush()
            os.fsync(target.fileno())
        sign_feed(temporary, signer, key)
        check_appcast(temporary, release.tag, require_signed_feed=True, public_key=public_key)
        with temporary.open("r+b") as signed:
            os.fsync(signed.fileno())
        os.link(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--appcast", type=Path, required=True, help="downloaded unsigned v1.0.0a10 appcast.xml")
    parser.add_argument("--archive", type=Path, required=True, help="downloaded v1.0.0a10 DMG")
    parser.add_argument("--output", type=Path, required=True, help="new signed appcast output path")
    parser.add_argument("--tag", required=True, help="must be v1.0.0a10")
    parser.add_argument("--signer", type=Path, required=True, help="pinned Sparkle 2.10 sign_update tool")
    args = parser.parse_args()
    if args.tag != FIRST_RELEASE.tag:
        parser.error(f"this reviewed migration only supports {FIRST_RELEASE.tag}")
    try:
        sign_historical_appcast(args.appcast, args.archive, args.output, args.signer)
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
