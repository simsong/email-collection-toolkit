# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Exercise release staging with temporary installer artifacts and typed evidence.
# Windows establishes certificate trust; these checks establish artifact consistency.
# Hash inventories must cover exactly the base bundle and public signing evidence.
# Tampering, duplicate records, test identities and missing timestamps must fail.
# Successful ZIPs exclude the synthetic upgrade and certificate-import helpers.
# This covers the public MSIX staging requirement, not live Microsoft signing.
"""Public Windows release artifact staging regressions."""
from pathlib import Path
import zipfile

from pydantic import TypeAdapter, ValidationError
import pytest

from scripts.release_files import FileHash, SigningVerification, digest, stage


def write_inventory(windows: Path, names: tuple[str, ...] = ("base.msixbundle", "signing-verification.json")) -> None:
    """Serialize the aliased inventory emitted by Windows PowerShell."""
    hashes = [FileHash(Algorithm="SHA256", Hash=digest(windows / name), Path=f"C:\\bundle\\{name}") for name in names]
    (windows / "sha256.json").write_bytes(TypeAdapter(list[FileHash]).dump_json(hashes, by_alias=True))


def artifacts(tmp_path: Path) -> Path:
    """Create actual files for hash validation without claiming certificate trust."""
    windows = tmp_path / "windows"
    windows.mkdir()
    (windows / "base.msixbundle").write_bytes(b"candidate fixture bytes")
    (windows / "upgrade.msixbundle").write_bytes(b"CI-only upgrade fixture")
    evidence = SigningVerification(signing_mode="public-trust", publisher="CN=Approved Fixture",
                                   base_sha256=digest(windows / "base.msixbundle"), timestamp_required=True)
    (windows / "signing-verification.json").write_text(evidence.model_dump_json(), encoding="utf-8")
    (windows / "README.txt").write_text("Open base.msixbundle. Verify sha256.json.", encoding="utf-8")
    write_inventory(windows)
    return windows


def test_stage_public_bundle_excludes_upgrade_and_test_trust(tmp_path: Path) -> None:
    windows = artifacts(tmp_path)
    bundle = stage(windows, tmp_path / "release")
    assert bundle.read_bytes() == (windows / "base.msixbundle").read_bytes()
    with zipfile.ZipFile(bundle.with_suffix(".zip")) as archive:
        assert set(archive.namelist()) == {bundle.name, "signing-verification.json", "README.txt", "SHA256SUMS"}
        assert bundle.name in archive.read("README.txt").decode()
        assert digest(bundle) in archive.read("SHA256SUMS").decode()
    assert not (bundle.parent / "local-test.cer").exists()


@pytest.mark.parametrize("name", ["base.msixbundle", "signing-verification.json"])
def test_stage_rejects_tampered_artifact(tmp_path: Path, name: str) -> None:
    windows = artifacts(tmp_path)
    with (windows / name).open("ab") as stream:
        stream.write(b"tampering")
    with pytest.raises(ValueError, match="hash mismatch"):
        stage(windows, tmp_path / "release")
    assert not (tmp_path / "release").exists()


def test_stage_rejects_duplicate_inventory(tmp_path: Path) -> None:
    windows = artifacts(tmp_path)
    write_inventory(windows, ("base.msixbundle", "base.msixbundle"))
    with pytest.raises(ValueError, match="hash mismatch"):
        stage(windows, tmp_path / "release")


@pytest.mark.parametrize("publisher,base_hash", [("CN=ECT Local Test", None), ("CN=Approved Fixture", "0" * 64)])
def test_stage_rejects_wrong_identity_or_bundle(tmp_path: Path, publisher: str, base_hash: str | None) -> None:
    windows = artifacts(tmp_path)
    evidence = SigningVerification(signing_mode="public-trust", publisher=publisher,
                                   base_sha256=base_hash or digest(windows / "base.msixbundle"), timestamp_required=True)
    (windows / "signing-verification.json").write_text(evidence.model_dump_json(), encoding="utf-8")
    write_inventory(windows)
    with pytest.raises(ValueError, match="public signing evidence"):
        stage(windows, tmp_path / "release")


def test_stage_rejects_test_certificate_inventory(tmp_path: Path) -> None:
    windows = artifacts(tmp_path)
    (windows / "local-test.cer").write_bytes(b"test certificate")
    write_inventory(windows, ("base.msixbundle", "local-test.cer"))
    with pytest.raises(ValueError, match="hash mismatch"):
        stage(windows, tmp_path / "release")


def test_stage_rejects_missing_timestamp_evidence(tmp_path: Path) -> None:
    windows = artifacts(tmp_path)
    path = windows / "signing-verification.json"
    path.write_text(path.read_text().replace('"timestamp_required":true', '"timestamp_required":false'), encoding="utf-8")
    write_inventory(windows)
    with pytest.raises(ValidationError):
        stage(windows, tmp_path / "release")
