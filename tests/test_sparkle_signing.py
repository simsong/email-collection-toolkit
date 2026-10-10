# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Exercise the Sparkle release signer with disposable key material.
# Check archive and feed verification using the pinned real tool.
# Audit the historical feed's exact bytes and app-key policy.
# Keep native trust boundaries isolated when the old DMG is unavailable.
# Assert that release secrets never enter unrelated signer subprocesses.

"""Verify the real pinned Sparkle signer accepts the release key transport."""

import base64
from contextlib import contextmanager
import hashlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tomllib
import xml.etree.ElementTree as xml
from typing import Literal

import pytest

from scripts.update_appcast import AppcastRelease, SPARKLE_HARDWARE, SPARKLE_MINIMUM_SYSTEM, SPARKLE_PRIVATE_KEY_SECRET, append_item, require_matching_key, sign_feed, signed_archive, signing_key, verify_signed_archive
from scripts.check_appcast import check_appcast
import scripts.sign_historical_appcast as historical_signer
from scripts.sign_historical_appcast import FIRST_RELEASE, audit_legacy_feed, sign_historical_appcast
from scripts.macos_signing import NATIVE_TRUST_ENV_PREFIXES, RELEASE_SECRET_NAMES, release_safe_environment
from mailarchiver.release_versions import release_metadata


@pytest.mark.parametrize("failure", [None, 0, 1, 2, 3, 4])
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
    info.write_bytes(plistlib.dumps({
        historical_signer.PLIST_SPARKLE_PUBLIC_KEY: "fixture-public-key",
        historical_signer.PLIST_BUNDLE_VERSION: str(FIRST_RELEASE.sparkle_version),
        historical_signer.PLIST_SHORT_VERSION: FIRST_RELEASE.display_version,
    }))
    expected = [
        ["/usr/bin/codesign", "--verify", "--strict", str(archive)],
        ["/usr/bin/xcrun", "stapler", "validate", str(archive)],
        ["/usr/sbin/spctl", "--assess", "--type", "open", "--context",
         "context:primary-signature", str(archive)],
        ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)],
        ["/usr/sbin/spctl", "--assess", "--type", "execute", str(app)],
    ]
    commands: list[list[str]] = []
    for name in RELEASE_SECRET_NAMES:
        monkeypatch.setenv(name, "fixture-secret")
    for name in ("PYTHONPATH", "DYLD_INSERT_LIBRARIES", "LD_PRELOAD", "ARCHIVE_OVERRIDE",
                 "DEVELOPER_DIR", "TOOLCHAINS", "SDKROOT", "CODESIGN_ALLOCATE"):
        monkeypatch.setenv(name, "fixture-override")

    @contextmanager
    def mounted_fixture(path: Path):
        assert path == archive
        assert commands == expected[:3]
        yield mount

    def run(command: list[str], *, check: bool, env: dict[str, str],
            cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        assert check
        assert all(name not in env for name in RELEASE_SECRET_NAMES)
        assert all(name not in env for name in
                   ("PYTHONPATH", "DYLD_INSERT_LIBRARIES", "LD_PRELOAD", "ARCHIVE_OVERRIDE",
                    "DEVELOPER_DIR", "TOOLCHAINS", "SDKROOT", "CODESIGN_ALLOCATE"))
        assert env["PATH"] == "/usr/bin:/bin:/usr/sbin:/sbin"
        assert cwd is None
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
        with historical_signer.verify_historical_app_key(archive, "fixture-public-key") as verified_mount:
            assert verified_mount == mount
            assert commands == expected
        assert commands == expected
    else:
        with pytest.raises(subprocess.CalledProcessError):
            with historical_signer.verify_historical_app_key(archive, "fixture-public-key"):
                pytest.fail("failed trust check must not expose a mount")
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


def test_private_historical_copy_survives_source_replacement(tmp_path: Path) -> None:
    """Issue #91: mounted and signed bytes stay fixed when the download path changes."""
    archive = tmp_path / "download.dmg"
    archive.write_bytes(b"original image bytes")
    copied_path: Path | None = None
    with historical_signer.private_archive_copy(archive) as copied:
        copied_path = copied
        assert copied != archive
        assert copied.read_bytes() == b"original image bytes"
        assert copied.stat().st_mode & 0o777 == 0o400
        assert copied.parent.stat().st_mode & 0o777 == 0o700
        archive.unlink()
        archive.write_bytes(b"replacement image bytes")
        assert copied.read_bytes() == b"original image bytes"
    assert copied_path is not None and not copied_path.exists()


def test_private_historical_copy_rejects_mutation(tmp_path: Path) -> None:
    """Issue #91: an altered private image cannot produce a signed feed."""
    archive = tmp_path / "download.dmg"
    archive.write_bytes(b"original image bytes")
    copied_path: Path | None = None
    with pytest.raises(RuntimeError, match="changed during verification"):
        with historical_signer.private_archive_copy(archive) as copied:
            copied_path = copied
            copied.chmod(0o600)
            copied.write_bytes(b"tampered image bytes")
    assert copied_path is not None and not copied_path.exists()


def test_historical_app_info_plist_requires_matching_sparkle_key(tmp_path: Path) -> None:
    """Issue #91: mounted app key and versions must match the reviewed feed."""
    import plistlib
    from scripts.sign_historical_appcast import verify_embedded_app_key

    app = tmp_path / "Email Collection Toolkit.app"
    info_path = app / "Contents/Info.plist"
    info_path.parent.mkdir(parents=True)
    public_key = "fixture-public-key"
    values = {
        historical_signer.PLIST_SPARKLE_PUBLIC_KEY: public_key,
        historical_signer.PLIST_BUNDLE_VERSION: str(FIRST_RELEASE.sparkle_version),
        historical_signer.PLIST_SHORT_VERSION: FIRST_RELEASE.display_version,
    }
    info_path.write_bytes(plistlib.dumps(values))
    verify_embedded_app_key(app, public_key)
    with pytest.raises(ValueError, match="does not match"):
        verify_embedded_app_key(app, "different-public-key")
    for field in (historical_signer.PLIST_BUNDLE_VERSION, historical_signer.PLIST_SHORT_VERSION):
        changed = dict(values)
        changed[field] = "wrong-version"
        info_path.write_bytes(plistlib.dumps(changed))
        with pytest.raises(ValueError, match="version does not match"):
            verify_embedded_app_key(app, public_key)


def test_historical_mount_test_does_not_run_make_from_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #91: PATH substitution cannot turn the mounted test into a no-op."""
    bin_directory = tmp_path / "bin"
    bin_directory.mkdir()
    marker = tmp_path / "fake-make-ran"
    fake_make = bin_directory / "make"
    fake_make.write_text(
        "#!/bin/sh\n"
        "# This disposable fixture impersonates make in PATH.\n"
        "# It records an invocation instead of testing a mounted app.\n"
        "# The historical migration must never execute this file.\n"
        "# Its marker detects a silently skipped trust test.\n"
        "# The test uses an unmounted path so the real driver fails.\n"
        f"touch {marker}\n",
        encoding="utf-8",
    )
    fake_make.chmod(0o700)
    monkeypatch.setenv("PATH", str(bin_directory))
    mount = tmp_path / "not-mounted"
    archive = tmp_path / "not-a-dmg"
    mount.mkdir()
    archive.write_bytes(b"fixture")
    with pytest.raises(subprocess.CalledProcessError):
        historical_signer.test_verified_mount(mount, archive)
    assert not marker.exists()


def test_historical_mount_interpreter_retains_locked_project() -> None:
    """Issue #91: the credential-scrubbed native driver still imports its locked project."""
    repository = Path(__file__).parents[1]
    environment = release_safe_environment(os.environ, NATIVE_TRUST_ENV_PREFIXES)
    environment["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
    result = subprocess.run(
        [str(historical_signer.mounted_test_interpreter()), "-c",
         "from mailarchiver.self_test import SelfTestReport; import pydantic"],
        cwd=repository, env=environment, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr


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


def test_sparkle_tools_rejects_missing_member_with_empty_cached_executable(tmp_path: Path) -> None:
    """Issue #91: an absent tar member cannot pass comparison with an empty cache file."""
    repository = Path(__file__).parents[1]
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    archive = downloads / "Sparkle-2.10.0.tar.xz"
    data = b"key generator"
    with tarfile.open(archive, "w:xz") as bundle:
        member = tarfile.TarInfo("./bin/generate_keys")
        member.mode = 0o755
        member.size = len(data)
        bundle.addfile(member, io.BytesIO(data))
    tools = tmp_path / "sparkle/bin"
    tools.mkdir(parents=True)
    (tools / "generate_keys").write_bytes(data)
    (tools / "generate_keys").chmod(0o700)
    (tools / "sign_update").touch(mode=0o700)
    result = subprocess.run(
        ["make", "sparkle-tools", f"SPARKLE_DOWNLOAD_DIR={downloads}",
         f"SPARKLE_DIR={tools.parent}",
         f"SPARKLE_SHA256={hashlib.sha256(archive.read_bytes()).hexdigest()}"],
        cwd=repository, capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0
    assert "verified Sparkle archive is missing tool: sign_update" in result.stderr


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
    stages: list[str] = []

    def verify_fixture_archive(path: Path, tool: Path, _key: object, signature: str) -> None:
        assert path != archive
        assert path.exists() and path.stat().st_size == FIRST_RELEASE.archive_length
        assert tool == signer
        verified_archives.append((path, tool, signature))
        stages.append("archive-verified")

    # The historical DMG is not checked in; its signature is independently covered
    # by the real signer test above, so this test isolates feed-signing orchestration.
    monkeypatch.setattr(historical_signer, "verify_signed_archive", verify_fixture_archive)
    verified_app_keys: list[tuple[Path, str]] = []

    @contextmanager
    def verify_fixture_app(path: Path, expected_key: str):
        assert path != archive
        assert path.exists() and path.stat().st_size == FIRST_RELEASE.archive_length
        verified_app_keys.append((path, expected_key))
        stages.append("mount-open")
        yield tmp_path / "mounted"
        stages.append("mount-closed")

    def test_fixture_mount(mount: Path, path: Path) -> None:
        assert mount == tmp_path / "mounted"
        assert path == verified_archives[0][0]
        assert stages == ["mount-open", "archive-verified"]
        stages.append("mounted-test")

    monkeypatch.setattr(historical_signer, "verify_historical_app_key", verify_fixture_app)
    monkeypatch.setattr(historical_signer, "test_verified_mount", test_fixture_mount)
    sign_historical_appcast(appcast, archive, output, signer, public_key=public_key)

    assert appcast.read_bytes() == original
    assert len(verified_archives) == 1
    assert verified_archives[0][2] == FIRST_RELEASE.archive_signature
    assert verified_app_keys == [(verified_archives[0][0], public_key)]
    assert stages == ["mount-open", "archive-verified", "mounted-test", "mount-closed"]
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


def test_signer_subprocess_receives_no_apple_or_loader_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #91: real archive/XML signing isolates unrelated protected inputs."""
    real_signer = Path(__file__).parents[1] / ".tools/sparkle/2.10.0/bin/sign_update"
    if not real_signer.exists():
        pytest.skip("Sparkle developer tools are not installed; run make test-sparkle-signing")
    for name in RELEASE_SECRET_NAMES:
        monkeypatch.setenv(name, "fixture-secret")
    monkeypatch.setenv(SPARKLE_PRIVATE_KEY_SECRET, base64.b64encode(bytes(range(32))).decode("ascii"))
    for name in ("PYTHONPATH", "DYLD_INSERT_LIBRARIES", "LD_PRELOAD", "ARCHIVE_OVERRIDE"):
        monkeypatch.setenv(name, "fixture-override")
    wrapper = tmp_path / "checked-signer"
    wrapper.write_text(
        f"#!{sys.executable}\n"
        "# Check the environment before delegating to the pinned signer.\n"
        "# This disposable fixture runs only within the signing test.\n"
        "# It observes variable names, not protected secret values.\n"
        "# The real Sparkle signer still performs every signing operation.\n"
        "# A leaked variable makes the test fail before signing.\n"
        "import os, sys\n"
        f"for name in {(*RELEASE_SECRET_NAMES, 'PYTHONPATH', 'DYLD_INSERT_LIBRARIES', 'LD_PRELOAD', 'ARCHIVE_OVERRIDE')!r}:\n"
        "    if name in os.environ: raise SystemExit('signer received protected environment')\n"
        f"os.execv({str(real_signer)!r}, [{str(real_signer)!r}, *sys.argv[1:]])\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o700)
    archive = tmp_path / "fixture.dmg"
    archive.write_bytes(b"signed archive environment fixture")
    assert signed_archive(archive, wrapper, signing_key()).length == archive.stat().st_size
    feed = tmp_path / "appcast.xml"
    feed.write_text('<rss version="2.0"><channel><title>Fixture</title></channel></rss>', encoding="utf-8")
    sign_feed(feed, wrapper, signing_key())
    assert b"<!-- sparkle-signatures:" in feed.read_bytes()


def test_update_appcast_make_entrypoint_reaches_key_gate_without_secret(tmp_path: Path) -> None:
    """Issue #91: release publisher imports shared signing code when run as a script."""
    repository = Path(__file__).parents[1]
    if not (repository / ".tools/sparkle/2.10.0/bin/sign_update").exists():
        pytest.skip("Sparkle developer tools are not installed; run make test-sparkle-signing")
    version = tomllib.loads((repository / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    tag = release_metadata(version)[0]
    result = subprocess.run(
        ["make", "update-appcast", f"APPCAST={tmp_path / 'missing.xml'}",
         f"ARCHIVE={tmp_path / 'missing.dmg'}", f"RELEASE_TAG={tag}",
         "RELEASE_URL=https://example.invalid/missing.dmg"],
        cwd=repository, env=release_safe_environment(os.environ, NATIVE_TRUST_ENV_PREFIXES),
        capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0
    assert "Release signing requires a 32-byte Sparkle seed" in result.stderr
    assert "ModuleNotFoundError" not in result.stderr
    assert not (tmp_path / "missing.xml").exists()


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


def test_mixed_feed_authenticates_both_installers_and_complete_xml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Shared update requirement: one signed feed carries distinct platform items."""
    from Cryptodome.Signature import eddsa

    signer = Path(__file__).parents[1] / ".tools/sparkle/2.10.0/bin/sign_update"
    assert signer.exists(), "Run make test-sparkle-signing to provision the verified tools"
    seed = bytes(range(32))
    monkeypatch.setenv(SPARKLE_PRIVATE_KEY_SECRET, base64.b64encode(seed).decode("ascii"))
    public = base64.b64encode(eddsa.import_private_key(seed).public_key().export_key(format="raw")).decode("ascii")
    version = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())["project"]["version"]
    tag, channel, build, display = release_metadata(version)
    appcast = tmp_path / "mixed.xml"
    appcast.write_text('<rss><channel><title>Fixture</title></channel></rss>\n', encoding="utf-8")
    prefix = f"https://github.com/simsong/email-collection-toolkit/releases/download/{tag}"
    releases = []
    cases: tuple[tuple[Literal["macos", "windows"], str], ...] = (("macos", "dmg"), ("windows", "msixbundle"))
    for platform, extension in cases:
        archive = tmp_path / f"fixture.{extension}"
        archive.write_bytes(f"synthetic {platform} installer bytes".encode())
        signed = signed_archive(archive, signer, signing_key())
        release = AppcastRelease(tag=tag, channel=channel, sparkle_version=build, display_version=display,
                                url=f"{prefix}/{archive.name}", archive=signed, platform=platform,
                                hardware="arm64" if platform == "macos" else None)
        releases.append(release)
        append_item(appcast, release)
        archive.write_bytes(b"tampered installer")
        with pytest.raises(RuntimeError, match="signature verification failed"):
            verify_signed_archive(archive, signer, signing_key(), signed.signature)
    with pytest.raises(ValueError, match="already contains"):
        append_item(appcast, releases[0])
    sign_feed(appcast, signer, signing_key())
    check_appcast(appcast, tag, require_signed_feed=True, public_key=public,
                  expected_platforms=frozenset({"macos", "windows"}))
    assert {enclosure.get("{http://www.andymatuschak.org/xml-namespaces/sparkle}os")
            for enclosure in xml.parse(appcast).findall("channel/item/enclosure")} == {"macos", "windows"}
    appcast.write_bytes(appcast.read_bytes().replace(b"fixture.msixbundle", b"changed.msixbundle"))
    with pytest.raises(ValueError, match="signature verification failed"):
        check_appcast(appcast, tag, require_signed_feed=True, public_key=public)
