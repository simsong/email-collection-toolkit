# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Verify Mailbag metadata and manifests detect payload, tag, and layout damage."""

from __future__ import annotations

import csv
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path
from shutil import copytree
import pytest

from mailarchiver import standalone_verify
from mailarchiver.bagit import initialize_bag, refresh_tag_manifest, write_bag_checkpoint
from mailarchiver.catalog import create_catalog
from mailarchiver.standalone_verify import verify_archive

FIXTURE = Path(__file__).parent / "data" / "three-message-mailbag"


def test_fixture_generator_never_deletes_arbitrary_existing_output(tmp_path: Path) -> None:
    """Source safety: fixture regeneration must refuse unrelated directories and their bytes."""
    from tests.generate_bagit_fixture import generate
    output = tmp_path / "user-data"
    output.mkdir()
    original = output / "private.eml"
    original.write_bytes(b"user-owned mail")
    with pytest.raises(FileExistsError, match="refusing to replace"):
        generate(output)
    assert list(output.iterdir()) == [original] and original.read_bytes() == b"user-owned mail"


def test_new_bag_records_the_installed_application_version(tmp_path: Path) -> None:
    """Requirement: checkpoint provenance uses the same version as the installed application."""
    archive = tmp_path / "new.mailarchive"
    initialize_bag(archive)
    catalog = create_catalog(archive / "archive.sqlite3")
    try:
        write_bag_checkpoint(archive, catalog)
    finally:
        catalog.close()
    metadata = (archive / "bag-info.txt").read_text(encoding="utf-8").splitlines()
    assert f"Mailbag-Agent-Version: {version('mailarchiver')}" in metadata
    assert verify_archive(archive) == []


def test_checkpoint_ignores_extra_files_without_changing_them(tmp_path: Path) -> None:
    """ARCHIVE: editor backups warn without failing checkpoint publication or becoming email payload."""
    initialize_bag(tmp_path)
    extras = [tmp_path / "data/mbox/#INFECTED1.mbox#", tmp_path / "data/notes/backup.txt"]
    for extra in extras:
        extra.parent.mkdir(exist_ok=True)
        extra.write_bytes(b"synthetic user-owned backup\n")
    catalog = create_catalog(tmp_path / "archive.sqlite3")
    try:
        warnings = write_bag_checkpoint(tmp_path, catalog)
    finally:
        catalog.close()
    assert len(warnings) == 2 and all(warning.startswith("Extra file detected:") for warning in warnings)
    assert (tmp_path / "manifest-sha256.txt").read_bytes() == b""
    verified_warnings: list[str] = []
    assert verify_archive(tmp_path, on_warning=verified_warnings.append) == []
    assert verified_warnings == warnings
    assert all(extra.read_bytes() == b"synthetic user-owned backup\n" for extra in extras)


def test_extra_file_warning_does_not_hide_actual_mail_corruption(tmp_path: Path) -> None:
    """ARCHIVE: extras are ignored; canonical missing/changed mail must still fail verification."""
    archive = copytree(FIXTURE, tmp_path / "extra-bag")
    extra = archive / "data/mbox/#INFECTED1.mbox#"
    extra.write_bytes(b"synthetic editor backup\n")
    warnings: list[str] = []
    assert verify_archive(archive, on_warning=warnings.append) == []
    assert warnings == ["Extra file detected: data/mbox/#INFECTED1.mbox#. This file was ignored."]
    result = subprocess.run([sys.executable, "-I", str(Path(standalone_verify.__file__)), "-q", str(archive)],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0 and "WARNING Extra file detected:" in result.stderr
    assert "ERROR" not in result.stderr
    extra.write_bytes(b"a changed backup is still not preserved email\n")
    assert verify_archive(archive, on_warning=warnings.append) == []
    payload = archive / "data/mbox/three-messages.mbox"
    original = payload.read_bytes()
    payload.write_bytes(original + b"damage")
    assert any("SHA-256 mismatch" in error for error in verify_archive(archive, on_warning=warnings.append))
    payload.unlink()
    assert verify_archive(archive, on_warning=warnings.append)
    payload.write_bytes(original)
    extra.unlink()
    assert verify_archive(archive, on_warning=warnings.append) == []


def test_checked_in_three_message_mailbag_validates_without_sqlite() -> None:
    """Requirement: the preservation package validates using only its BagIt tags and payload."""
    result = subprocess.run(
        [sys.executable, "-I", str(Path(standalone_verify.__file__)), str(FIXTURE)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "OK three-messages.mbox: 3 messages" in result.stdout
    assert "Archive integrity verified." in result.stdout
    with (FIXTURE / "mailbag.csv").open(encoding="utf-8", newline="") as source:
        rows = list(csv.DictReader(source))
    assert len(rows) == 3
    assert [row["Attachments"] for row in rows] == ["0", "0", "1"]
    assert len({row["Mailbag-Message-ID"].casefold() for row in rows}) == 3


def test_validator_detects_payload_and_tag_corruption(tmp_path: Path) -> None:
    """Requirement: BagIt SHA-256 manifests detect both canonical and metadata changes."""
    payload_bag = copytree(FIXTURE, tmp_path / "payload-bag")
    payload = payload_bag / "data" / "mbox" / "three-messages.mbox"
    with payload.open("ab") as output:
        output.write(b"damage")

    payload_errors = verify_archive(payload_bag)

    assert any("manifest-sha256.txt: SHA-256 mismatch" in error for error in payload_errors)
    assert any("h1 mismatch" in error for error in payload_errors)

    tag_bag = copytree(FIXTURE, tmp_path / "tag-bag")
    mailbag_csv = tag_bag / "mailbag.csv"
    mailbag_csv.write_bytes(mailbag_csv.read_bytes().replace(b",1\r\n", b",2\r\n"))

    tag_errors = verify_archive(tag_bag)

    assert any("tagmanifest-sha256.txt: SHA-256 mismatch for mailbag.csv" in error for error in tag_errors)


def test_validator_recomputes_semantic_hash_after_tag_manifest_refresh(tmp_path: Path) -> None:
    """Requirement: replacing a tag checksum cannot conceal a false per-message digest."""
    archive = copytree(FIXTURE, tmp_path / "semantic-bag")
    integrity = archive / "integrity" / "three-messages.mbox.integrity"
    lines = integrity.read_text(encoding="utf-8").splitlines()
    fields = lines[-3].split("\t")
    fields[-1] = "h3:" + "0" * 64
    lines[-3] = "\t".join(fields)
    integrity.write_text("\n".join(lines) + "\n", encoding="utf-8")
    refresh_tag_manifest(archive)

    errors = verify_archive(archive)

    assert any("message 1 h3 mismatch" in error for error in errors)
    assert not any("tagmanifest-sha256.txt: SHA-256 mismatch" in error for error in errors)


def test_validator_enforces_mailbag_csv_split_size(tmp_path: Path) -> None:
    """Requirement: only a final split Mailbag CSV may contain fewer than 100,000 rows."""
    archive = copytree(FIXTURE, tmp_path / "split-bag")
    records = (archive / "mailbag.csv").read_bytes().splitlines(keepends=True)
    (archive / "mailbag-1.csv").write_bytes(b"".join(records[:2]))
    (archive / "mailbag-2.csv").write_bytes(b"".join(records[2:]))
    (archive / "mailbag.csv").unlink()
    refresh_tag_manifest(archive)

    errors = verify_archive(archive)

    assert any("mailbag-1.csv must contain 100000 message rows" in error for error in errors)


def test_validator_does_not_silently_skip_an_additional_manifest(tmp_path: Path) -> None:
    """Requirement: success means that no present BagIt manifest was left unchecked."""
    archive = copytree(FIXTURE, tmp_path / "additional-manifest-bag")
    (archive / "manifest-sha512.txt").write_text("unverified declaration\n", encoding="utf-8")

    errors = verify_archive(archive)

    assert "unsupported additional BagIt manifests: manifest-sha512.txt" in errors


def test_validator_rejects_root_level_legacy_mbox(tmp_path: Path) -> None:
    """Requirement: unsupported pre-BagIt output is never mistaken for a native tag file."""
    archive = copytree(FIXTURE, tmp_path / "legacy-output-bag")
    (archive / "old-layout.mbox").write_bytes(b"legacy output\n")

    errors = verify_archive(archive)

    assert "unsupported root-level legacy archive output: old-layout.mbox" in errors
