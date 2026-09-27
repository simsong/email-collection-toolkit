# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Verify the real pinned Sparkle signer accepts the release key transport."""

import base64
from pathlib import Path
import xml.etree.ElementTree as xml

import pytest

from scripts.update_appcast import AppcastRelease, SPARKLE_HARDWARE, SPARKLE_MINIMUM_SYSTEM, SPARKLE_PRIVATE_KEY_SECRET, append_item, require_matching_key, sign_feed, signed_archive, signing_key, verify_signed_archive
from scripts.check_appcast import check_appcast
from scripts.sign_historical_appcast import FIRST_RELEASE, audit_legacy_feed


def test_historical_feed_audit_pins_a10_release_and_archive(tmp_path: Path) -> None:
    """Issue #91: migration must not sign a feed with changed historical metadata."""
    release = FIRST_RELEASE
    appcast = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle" version="2.0">'
        '<channel><item><title>Email Collection Toolkit 1.0.0a10</title><guid>v1.0.0a10</guid>'
        '<sparkle:version>1000000110</sparkle:version>'
        '<sparkle:shortVersionString>1.0.0a10</sparkle:shortVersionString>'
        '<sparkle:channel>preview</sparkle:channel><pubDate>Wed, 23 Sep 2026 10:26:21 GMT</pubDate><enclosure '
        f'url="{release.archive_url}" length="{release.archive_length}" '
        f'type="application/octet-stream" sparkle:edSignature="{release.archive_signature}" />'
        '</item><title>Email Collection Toolkit updates</title>'
        '<link>https://simsong.github.io/email-collection-toolkit/updates/mac/appcast.xml</link>'
        '<description>Signed macOS release and preview updates.</description></channel></rss>'
    ).encode()
    archive = tmp_path / "v1.0.0a10.dmg"
    with archive.open("wb") as handle:
        handle.truncate(release.archive_length)

    assert audit_legacy_feed(appcast, archive, release) == release.archive_signature

    changed = appcast.replace(b"<sparkle:channel>preview</sparkle:channel>",
                              b"<sparkle:channel>stable</sparkle:channel>")
    with pytest.raises(ValueError, match="update channel differs"):
        audit_legacy_feed(changed, archive, release)
    with pytest.raises(ValueError, match="does not match the reviewed release asset"):
        audit_legacy_feed(appcast.replace(b"length=\"160093151\"", b"length=\"1\""), archive, release)


def test_real_signer_accepts_exported_seed_on_standard_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A 32-byte exported seed signs an archive without a key file or Keychain access."""
    archive = tmp_path / "fixture.dmg"
    archive.write_bytes(b"synthetic Sparkle signing fixture")
    signer = Path(__file__).parents[1] / ".tools/sparkle/2.10.0/bin/sign_update"
    if not signer.exists():
        pytest.skip("Sparkle developer tools are not installed; run make test-sparkle-signing")
    monkeypatch.setenv(SPARKLE_PRIVATE_KEY_SECRET, base64.b64encode(bytes(range(32))).decode("ascii"))

    result = signed_archive(archive, signer, signing_key())

    assert result.length == archive.stat().st_size
    assert len(base64.b64decode(result.signature, validate=True)) == 64
    archive.write_bytes(b"tampered Sparkle signing fixture")
    with pytest.raises(RuntimeError, match="signature verification failed"):
        verify_signed_archive(archive, signer, signing_key(), result.signature)


def test_real_signer_signs_xml_and_rejects_modified_feed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Issue #91: signed archive attributes alone cannot authenticate feed contents."""
    signer = Path(__file__).parents[1] / ".tools/sparkle/2.10.0/bin/sign_update"
    if not signer.exists():
        pytest.skip("Sparkle developer tools are not installed; run make test-sparkle-signing")
    import subprocess

    monkeypatch.setenv(SPARKLE_PRIVATE_KEY_SECRET, base64.b64encode(bytes(range(32))).decode("ascii"))
    appcast = tmp_path / "appcast.xml"
    appcast.write_text('<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel><title>Fixture</title></channel></rss>')
    archive = tmp_path / "fixture.dmg"
    archive.write_bytes(b"synthetic update archive")
    append_item(appcast, AppcastRelease(tag="v1.0.0a10", channel="preview", sparkle_version=1000000110,
                                       display_version="1.0.0a10", release_notes="Notes <with> text",
                                       url="https://github.com/simsong/email-collection-toolkit/releases/download/v1.0.0a10/fixture.dmg",
                                       archive=signed_archive(archive, signer, signing_key())))
    with pytest.raises(ValueError, match="no embedded feed signature"):
        check_appcast(appcast, require_signed_feed=True)
    sign_feed(appcast, signer, signing_key())
    from Cryptodome.Signature import eddsa

    public = base64.b64encode(eddsa.import_private_key(bytes(range(32))).public_key().export_key(format="raw")).decode("ascii")
    check_appcast(appcast, require_signed_feed=True, public_key=public)
    with pytest.raises(ValueError, match="signature verification failed"):
        check_appcast(appcast, require_signed_feed=True)
    item = xml.parse(appcast).getroot().find("channel/item")
    assert item is not None
    assert item.findtext(SPARKLE_MINIMUM_SYSTEM) == "15.0"
    assert item.findtext(SPARKLE_HARDWARE) == "arm64"
    assert item.findtext("description") == "<pre>Notes &lt;with&gt; text</pre>"
    signed = appcast.read_bytes()
    assert b"edSignature" in signed
    appcast.write_bytes(signed.replace(b"Fixture", b"Changed"))
    with pytest.raises(ValueError, match="signature verification failed"):
        check_appcast(appcast, require_signed_feed=True, public_key=public)
    verified = subprocess.run([signer, "--verify", "--ed-key-file", "-", appcast],
                              input=signing_key().get_secret_value(), capture_output=True, text=True, check=False)
    assert verified.returncode != 0
    assert "failed to pass signing verification" in (verified.stdout + verified.stderr).lower()


def test_release_key_must_match_embedded_public_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Issue #91: a valid signature under a different key cannot update existing clients."""
    from Cryptodome.Signature import eddsa

    seed = bytes(range(32))
    monkeypatch.setenv(SPARKLE_PRIVATE_KEY_SECRET, base64.b64encode(seed).decode("ascii"))
    public = eddsa.import_private_key(seed).public_key().export_key(format="raw")
    require_matching_key(signing_key(), base64.b64encode(public).decode("ascii"))
    with pytest.raises(ValueError, match="does not match"):
        require_matching_key(signing_key(), base64.b64encode(bytes(32)).decode("ascii"))
    for expanded_size in (64, 96):
        monkeypatch.setenv(SPARKLE_PRIVATE_KEY_SECRET, base64.b64encode(bytes(expanded_size)).decode("ascii"))
        with pytest.raises(ValueError, match="reviewed migration"):
            signing_key()
