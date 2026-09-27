# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Verify the real pinned Sparkle signer accepts the release key transport."""

import base64
from contextlib import contextmanager
import hashlib
import io
from pathlib import Path
import subprocess
import tarfile
import xml.etree.ElementTree as xml

import pytest

from scripts.update_appcast import AppcastRelease, SPARKLE_HARDWARE, SPARKLE_MINIMUM_SYSTEM, SPARKLE_PRIVATE_KEY_SECRET, append_item, require_matching_key, sign_feed, signed_archive, signing_key, verify_signed_archive
from scripts.check_appcast import check_appcast
import scripts.sign_historical_appcast as historical_signer
from scripts.sign_historical_appcast import FIRST_RELEASE, audit_legacy_feed, sign_historical_appcast
from scripts.macos_signing import RELEASE_SECRET_NAMES


@pytest.mark.parametrize("failure", [None, 0, 1, 2, 3, 4, 5])
def test_historical_trust_checks_precede_execution_and_stop_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: int | None,
) -> None:
    """Issue #91: no mounted code executes before image/app trust gates pass."""
    import plistlib

    archive = tmp_path / "historical.dmg"
    mount = tmp_path / "mounted"
    app = mount / f"{historical_signer.APP_NAME}.app"
    info = app / "Contents/Info.plist"
    info.parent.mkdir(parents=True)
    info.write_bytes(plistlib.dumps({historical_signer.PLIST_SPARKLE_PUBLIC_KEY: "fixture-public-key"}))
    expected = [
        ["/usr/bin/codesign", "--verify", "--strict", str(archive)],
        ["/usr/bin/xcrun", "stapler", "validate", str(archive)],
        ["/usr/sbin/spctl", "--assess", "--type", "open", "--context",
         "context:primary-signature", str(archive)],
        ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)],
        ["/usr/sbin/spctl", "--assess", "--type", "execute", str(app)],
        ["make", "test-dmg", f"DMG={archive}"],
    ]
    commands: list[list[str]] = []
    for name in RELEASE_SECRET_NAMES:
        monkeypatch.setenv(name, "fixture-secret")

    @contextmanager
    def mounted_fixture(path: Path):
        assert path == archive
        assert commands == expected[:3]
        yield mount

    def run(command: list[str], *, check: bool, env: dict[str, str],
            cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        assert check
        assert all(name not in env for name in RELEASE_SECRET_NAMES)
        if command[0] == "make":
            assert cwd == Path(__file__).parents[1]
        index = len(commands)
        commands.append(command)
        if index == failure:
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0)

    # The protected historical DMG cannot be replaced with a synthetic signed
    # image. Substitute only native trust/mount boundaries to exercise ordering.
    monkeypatch.setattr(historical_signer, "mounted_historical_dmg", mounted_fixture)
    monkeypatch.setattr(historical_signer.subprocess, "run", run)
    if failure is None:
        historical_signer.verify_historical_app_key(archive, "fixture-public-key")
        assert commands == expected
    else:
        with pytest.raises(subprocess.CalledProcessError):
            historical_signer.verify_historical_app_key(archive, "fixture-public-key")
        assert commands == expected[:failure + 1]


def test_historical_feed_audit_pins_a10_bytes_and_archive(tmp_path: Path) -> None:
    """Issue #91: migration rejects lexical and semantic changes to the published feed."""
    release = FIRST_RELEASE
    appcast = Path(__file__).parent / "fixtures/sparkle/v1.0.0a10-appcast.xml"
    data = appcast.read_bytes()
    archive = tmp_path / "v1.0.0a10.dmg"
    with archive.open("wb") as handle:
        handle.truncate(release.archive_length)

    assert audit_legacy_feed(data, archive, release) == release.archive_signature

    mutations = (
        data.replace(b"<channel>", b"<channel><!-- changed -->"),
        data.replace(b"\n", b"\r\n", 1),
        data.replace(b"<sparkle:channel>preview</sparkle:channel>",
                     b"<sparkle:channel>stable</sparkle:channel>"),
        data.replace(b'length="160093151"', b'length="1"'),
    )
    for changed in mutations:
        with pytest.raises(ValueError, match="appcast bytes do not match"):
            audit_legacy_feed(changed, archive, release)


def test_historical_app_info_plist_requires_matching_sparkle_key(tmp_path: Path) -> None:
    """Issue #91: the installed historical app must trust the key that signs its feed."""
    import plistlib
    from scripts.sign_historical_appcast import verify_embedded_app_key

    app = tmp_path / "Email Collection Toolkit.app"
    info_path = app / "Contents/Info.plist"
    info_path.parent.mkdir(parents=True)
    public_key = "fixture-public-key"
    info_path.write_bytes(plistlib.dumps({"SUPublicEDKey": public_key}))
    verify_embedded_app_key(app, public_key)
    with pytest.raises(ValueError, match="does not match"):
        verify_embedded_app_key(app, "different-public-key")


def test_sparkle_tools_rejects_a_modified_cached_signer(tmp_path: Path) -> None:
    """Issue #91: signing prerequisites verify cached binaries against the pinned archive."""
    repository = Path(__file__).parents[1]
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    archive = downloads / "Sparkle-2.10.0.tar.xz"
    binaries = {"./bin/generate_keys": b"key generator", "./bin/sign_update": b"trusted signer"}
    with tarfile.open(archive, "w:xz") as bundle:
        for name, data in binaries.items():
            member = tarfile.TarInfo(name)
            member.mode = 0o755
            member.size = len(data)
            bundle.addfile(member, io.BytesIO(data))
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    tools = tmp_path / "sparkle"
    command = [
        "make", "sparkle-tools", f"SPARKLE_DOWNLOAD_DIR={downloads}",
        f"SPARKLE_DIR={tools}", f"SPARKLE_SHA256={checksum}",
    ]

    extracted = subprocess.run(command, cwd=repository, capture_output=True, text=True, check=False)
    assert extracted.returncode == 0, extracted.stdout + extracted.stderr
    (tools / "bin/sign_update").write_bytes(b"tampered signer")

    rejected = subprocess.run(command, cwd=repository, capture_output=True, text=True, check=False)
    assert rejected.returncode != 0
    assert "cached Sparkle tool differs from verified archive: sign_update" in rejected.stderr


def test_historical_signing_orchestration_preserves_source_and_creates_verified_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #91: signing writes a separately verified feed and preserves pinned inputs."""
    from Cryptodome.Signature import eddsa

    signer = Path(__file__).parents[1] / ".tools/sparkle/2.10.0/bin/sign_update"
    if not signer.exists():
        pytest.skip("Sparkle developer tools are not installed; run make test-sparkle-signing")
    monkeypatch.setenv(SPARKLE_PRIVATE_KEY_SECRET, base64.b64encode(bytes(range(32))).decode("ascii"))
    public_key = base64.b64encode(
        eddsa.import_private_key(bytes(range(32))).public_key().export_key(format="raw")
    ).decode("ascii")
    source = Path(__file__).parent / "fixtures/sparkle/v1.0.0a10-appcast.xml"
    appcast = tmp_path / "appcast.xml"
    appcast.write_bytes(source.read_bytes())
    original = appcast.read_bytes()
    archive = tmp_path / "v1.0.0a10.dmg"
    with archive.open("wb") as handle:
        handle.truncate(FIRST_RELEASE.archive_length)
    output = tmp_path / "signed-appcast.xml"
    verified_archives: list[tuple[Path, Path, str]] = []

    def verify_fixture_archive(path: Path, tool: Path, _key: object, signature: str) -> None:
        assert path == archive
        assert tool == signer
        verified_archives.append((path, tool, signature))

    # The historical DMG is not checked in; its signature is independently covered
    # by the real signer test above, so this test isolates feed-signing orchestration.
    monkeypatch.setattr(historical_signer, "verify_signed_archive", verify_fixture_archive)
    verified_app_keys: list[tuple[Path, str]] = []

    def verify_fixture_app(path: Path, expected_key: str) -> None:
        assert path == archive
        verified_app_keys.append((path, expected_key))

    monkeypatch.setattr(historical_signer, "verify_historical_app_key", verify_fixture_app)
    sign_historical_appcast(appcast, archive, output, signer, public_key=public_key)

    assert appcast.read_bytes() == original
    assert len(verified_archives) == 1
    assert verified_archives[0][2] == FIRST_RELEASE.archive_signature
    assert verified_app_keys == [(archive, public_key)]
    check_appcast(output, FIRST_RELEASE.tag, require_signed_feed=True, public_key=public_key)
    assert b"<!-- sparkle-signatures:" in output.read_bytes()
    with pytest.raises(ValueError, match="refusing to replace existing"):
        sign_historical_appcast(appcast, archive, output, signer, public_key=public_key)


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
