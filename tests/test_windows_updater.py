# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Verify authenticated feed filtering against the current release mapper.
# Tampering, unsigned metadata, and incompatible package identities are rejected.
# Exercise the actual local HTTP gateway without substituting network methods.
# On Windows, initialize the prepared native SDK and retain its callback objects.
# Tests never launch an installer or change a production archive.
"""Requirements: signed Windows update discovery and historical Rust isolation."""
from __future__ import annotations

import base64
from importlib.metadata import version
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen
import xml.etree.ElementTree as xml

from Cryptodome.Signature import eddsa
import pytest

from mailarchiver.release_versions import release_metadata
from mailarchiver.updates import UpdateService, UpdateStatus
from mailarchiver.windows_update_feed import FeedGateway, PACKAGE_NAMESPACE, SPARKLE, filtered_feed
from scripts.update_appcast import AppcastRelease, SignedArchive, append_item
from scripts.check_appcast import check_appcast
from mailarchiver.application import ApplicationController, ApplicationPreferencesStore
from mailarchiver.windows_update_preferences import UpdatePreferencesApi


def test_published_windows_item_reaches_the_authenticated_reader(tmp_path: Path) -> None:
    """The release publisher must emit the identity that the installed updater selects."""
    tag, channel, build, display = release_metadata(version("mailarchiver"))
    path = tmp_path / "appcast.xml"
    path.write_text("<rss><channel/></rss>", encoding="utf-8")
    append_item(path, AppcastRelease(tag=tag, channel=channel, sparkle_version=build,
        display_version=display, platform="windows", hardware=None,
        url=f"https://github.com/simsong/email-collection-toolkit/releases/download/{tag}/fixture.msixbundle",
        archive=SignedArchive(signature=base64.b64encode(bytes(64)).decode(), length=100)))
    data = path.read_bytes()
    key = eddsa.import_private_key(bytes(range(32)))
    signature = base64.b64encode(eddsa.new(key, "rfc8032").sign(data))
    signed = data + b"<!-- sparkle-signatures:\nedSignature: " + signature + f"\nlength: {len(data)}\n-->\n".encode()
    public = base64.b64encode(key.public_key().export_key(format="raw")).decode()
    selected = xml.fromstring(filtered_feed(signed, public, "preview")).findall("channel/item/enclosure")
    assert len(selected) == 1 and selected[0].get("url", "").endswith("fixture.msixbundle")
    check_appcast(path, tag, expected_platforms=frozenset({"windows"}))
    for field, invalid in (("packageIdentity", "ECT.LocalTest"), ("architecture", "arm64")):
        tree = xml.fromstring(data)
        enclosure = tree.find("channel/item/enclosure")
        assert enclosure is not None
        enclosure.set(f"{{{PACKAGE_NAMESPACE}}}{field}", invalid)
        path.write_bytes(xml.tostring(tree))
        with pytest.raises(ValueError, match="incompatible package"):
            check_appcast(path, tag, expected_platforms=frozenset({"windows"}))


def test_windows_settings_report_failed_save_and_restore_choices(tmp_path: Path) -> None:
    """Settings must expose actual persistence failures and retain prior choices."""
    path = tmp_path / "preferences.json"
    controller = ApplicationController(ApplicationPreferencesStore(path))
    before = controller.preferences
    path.mkdir()
    sentinel = path / "existing"
    sentinel.write_bytes(b"preserve")
    service = UpdateService(UpdateStatus(version=version("mailarchiver"), channel="release",
                                        automatic_checks=False), lambda: False)
    api = UpdatePreferencesApi(service,
        lambda channel, automatic: controller.configure_updates(channel, automatic, strict=True))
    with pytest.raises(OSError, match="Could not write application preferences"):
        api.save("preview", True)
    assert service.status.channel == "release" and not service.status.automatic_checks
    assert controller.preferences == before
    assert sentinel.read_bytes() == b"preserve"


def signed_feed(identity: str = "ECT.PythonReader") -> tuple[bytes, str]:
    tag, channel, build, _ = release_metadata(version("mailarchiver"))
    key = eddsa.import_private_key(bytes(range(32)))  # Purpose-made test key, never a release key.
    document = (f'<rss xmlns:sparkle="{SPARKLE}" xmlns:ect="{PACKAGE_NAMESPACE}"><channel>'
        f'<item><guid>{tag}</guid><sparkle:version>{build}</sparkle:version>'
        f'<sparkle:channel>{channel}</sparkle:channel><enclosure sparkle:os="windows" '
        f'ect:packageIdentity="{identity}" ect:architecture="x64" '
        f'url="https://github.com/simsong/email-collection-toolkit/releases/download/{tag}/fixture.msix" '
        f'length="100" sparkle:edSignature="{base64.b64encode(bytes(64)).decode()}"/></item>'
        '<item><enclosure sparkle:os="macos" url="https://example.test/fixture.dmg"/></item>'
        '</channel></rss>\n').encode()
    signature = base64.b64encode(eddsa.new(key, "rfc8032").sign(document))
    return (document + b"<!-- sparkle-signatures:\nedSignature: " + signature
            + f"\nlength: {len(document)}\n-->\n".encode(),
            base64.b64encode(key.public_key().export_key(format="raw")).decode())


def test_signed_feed_filters_platform_package_and_channel() -> None:
    data, key = signed_feed()
    result = xml.fromstring(filtered_feed(data, key, "preview"))
    enclosures = result.findall("channel/item/enclosure")
    assert len(enclosures) == 1 and enclosures[0].get("url", "").endswith("fixture.msix")
    assert enclosures[0].get(f"{{{PACKAGE_NAMESPACE}}}packageIdentity") == "ECT.PythonReader"
    _, channel, _, _ = release_metadata(version("mailarchiver"))
    assert len(xml.fromstring(filtered_feed(data, key, "release")).findall("channel/item")) == int(channel == "release")
    rust, key = signed_feed("ECT.LocalTest")
    assert xml.fromstring(filtered_feed(rust, key, "preview")).findall("channel/item") == []


def test_complete_feed_signature_rejects_metadata_tampering() -> None:
    data, key = signed_feed()
    for invalid in (data.replace(b"fixture.msix", b"altered.msix"), data + b"extra", data.split(b"<!--")[0]):
        with pytest.raises(ValueError, match="signature"):
            filtered_feed(invalid, key, "preview")


def test_gateway_rejects_other_paths_before_remote_access() -> None:
    errors: list[str] = []
    gateway = FeedGateway("https://example.invalid/feed", "unused", lambda: "preview", errors.append)
    try:
        with pytest.raises(HTTPError) as error:
            urlopen(gateway.url + "/wrong", timeout=2)
        assert error.value.code == 404 and errors == []
    finally:
        gateway.close()
    assert not gateway.thread.is_alive()


@pytest.mark.skipif(os.name != "nt", reason="Actual WinSparkle Windows DLL")
def test_native_sdk_initialization_and_source_install_fence() -> None:
    from mailarchiver.winsparkle import WinSparkleBackend
    service = UpdateService(UpdateStatus(version=version("mailarchiver"), channel="preview"), lambda: False)
    library = Path(__file__).parents[1] / ".tmp/winsparkle/WinSparkle.dll"
    backend = WinSparkleBackend(service, library, lambda: None, installed=False)
    try:
        assert service.status.available and not service.status.automatic_checks
        assert service.status.build == str(release_metadata(service.status.version)[2])
        assert backend.ready_callback() == 0
        assert backend.installer_callback("fixture.msix") == -1
        service.configure("release", True)
        assert service.status.channel == "release" and not service.status.automatic_checks
    finally:
        backend.close()
    assert not backend.scheduler.is_alive() and not backend.gateway.thread.is_alive()
