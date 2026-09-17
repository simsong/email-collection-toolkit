# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""OST/redundant PST requirements: real readers, cache evidence, bounded failures and exact dedup."""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import subprocess
import sys
from email import policy
from email.parser import BytesParser
from pathlib import Path

import pytest

from mailarchiver.pff_source import PffReceipt, is_mail_class
from mailarchiver.plugin_api import MailContainer, MailObject, SourceSpec
from mailarchiver.plugin_loader import load_plugins
from mailarchiver.pst_source import ImportReceipt, validate_record
from mailarchiver.standalone_verify import verify_archive
from tests.test_cli_processing import cli, configure

ROOT = Path(__file__).resolve().parents[1]
OST = ROOT / "tests/fixtures/pff/sample.ost"
PST = ROOT / "rust/mct-importer/tests/fixtures/mail.pst"
EMPTY_PST = ROOT / "rust/mct-importer/tests/fixtures/empty.pst"


@pytest.mark.parametrize(("message_class", "expected"), [
    ("ipm.note.Custom", True), ("REPORT.IPM.Note.NDR", True), ("IPM.Schedule.Meeting.Request", True),
    ("IPM.Contact", False), ("IPM.Appointment.Custom", False), ("IPM.Microsoft.ScheduleData.FreeBusy", False),
])
def test_mapi_mail_class_scope(message_class: str, expected: bool) -> None:
    """Mail and recognized non-mail families are delimited, case-insensitive class prefixes."""
    assert is_mail_class(message_class) is expected


@pytest.mark.parametrize("message_class", ["IPM.Noteish", "IPM.Unknown", "IPM.Contacts"])
def test_unknown_mapi_classes_are_incomplete_not_silently_excluded(message_class: str) -> None:
    """An unrecognized class may carry mail and must never count as a successful non-mail exclusion."""
    with pytest.raises(ValueError, match="unsupported MAPI message class"):
        is_mail_class(message_class)


def test_real_ost_in_process_preserves_cache_and_attachment_bytes(tmp_path: Path) -> None:
    """Genuine SO/version-23 OST emits stable MIME, marks unavailable embedded MSGs and keeps source fixity."""
    fixture = tmp_path / "cache with spaces.ost"
    shutil.copyfile(OST, fixture)
    before = fixture.read_bytes()
    assert before[8:12] == b"SO\x17\x00"
    registry = load_plugins(archive=tmp_path)
    source = next(item.implementation for item in registry.sources if item.manifest.kind == "file-folder")
    container, = [item for item in source.discover(SourceSpec(locator=str(fixture))) if isinstance(item, MailContainer)]
    assert container.parser_kind == "ost" and container.source.relationship.role == "cache"
    runs: list[list[MailObject]] = []
    for _attempt in range(2):
        records: list[MailObject] = []
        with pytest.raises(RuntimeError, match="libpff extraction incomplete"):
            for event in source.messages(container, None):
                if isinstance(event, MailObject):
                    validate_record(event.raw)
                    records.append(event)
        runs.append(records)
    assert runs[0] == runs[1]
    assert len(runs[0]) == 87
    for record in runs[0]:
        assert all(not part.defects for part in BytesParser(policy=policy.default).parsebytes(record.raw).walk())
    selected = next(item for item in runs[0] if item.cursor == "libpff:2182532")
    message = BytesParser(policy=policy.default).parsebytes(selected.raw)
    assert str(message["Subject"]) == "newsletter copy"
    assert not message.defects
    attachments = [item for item in message.iter_attachments() if item.get_filename() != "original-transport-headers.txt"]
    assert len(attachments) == 11
    image = attachments[0].get_payload(decode=True)
    assert isinstance(image, bytes) and len(image) == 20833
    assert hashlib.sha256(image).hexdigest() == "0f4ff85802be13ad86f82b7e6e886e5a9a6a77ddb84995bc5a7657a3d40c8f9a"
    html = next(part.get_payload(decode=True) for part in message.walk() if part.get_content_type() == "text/html")
    assert isinstance(html, bytes)
    assert hashlib.sha256(html).hexdigest() == "6d52c74376341391bbe58f07ea0fc880578a7fb4160c31eff6b6a2c19017da91"
    rtf_message = BytesParser(policy=policy.default).parsebytes(next(item.raw for item in runs[0] if item.cursor == "libpff:2182916"))
    rtf = next(part.get_payload(decode=True) for part in rtf_message.walk() if part.get_content_type() == "application/rtf")
    assert isinstance(rtf, bytes)
    assert hashlib.sha256(rtf).hexdigest() == "e2822fc116bc97613129eec5833d5728a347683452a4951c1ca3c8b4ddd39355"
    incomplete = [BytesParser(policy=policy.default).parsebytes(item.raw) for item in runs[0]
                  if b"X-Mailarchiver-Extraction-Incomplete:" in item.raw]
    assert len(incomplete) == 1 and "Undeliverable" in str(incomplete[0]["Subject"])
    for path in tmp_path.glob("processing-libpff/*/receipt.json"):
        receipt = PffReceipt.model_validate_json(path.read_text())
        assert receipt.process_id == os.getpid()
        assert (receipt.encountered, receipt.emitted, receipt.non_mail, receipt.errors) == (92, 87, 5, 1)
        assert not receipt.complete and receipt.content_type == 111
        assert receipt.source_sha256 == hashlib.sha256(before).hexdigest()
        assert "attachment method 5" in path.with_name("diagnostics.jsonl").read_text()
    assert fixture.read_bytes() == before


@pytest.mark.parametrize(("fixture", "extension", "kind"), [(OST, ".pst", "ost"), (EMPTY_PST, ".ost", "pst")])
def test_outlook_header_takes_precedence_over_extension(tmp_path: Path, fixture: Path, extension: str, kind: str) -> None:
    """Renaming a cache or store cannot switch the reader selected by its internal format."""
    path = tmp_path / ("renamed" + extension)
    shutil.copyfile(fixture, path)
    registry = load_plugins(archive=tmp_path)
    source = next(item.implementation for item in registry.sources if item.manifest.kind == "file-folder")
    container, = [item for item in source.discover(SourceSpec(locator=str(path))) if isinstance(item, MailContainer)]
    assert container.parser_kind == kind


def test_redundant_option_revisits_unchanged_pst_and_defaults_off(tmp_path: Path) -> None:
    """Enabling a second reader invalidates the checkpoint; an unchanged repeat then skips both."""
    archive = tmp_path / "archive"
    fixture = tmp_path / "empty.pst"
    shutil.copyfile(EMPTY_PST, fixture)
    owners = tmp_path / "owners.txt"
    owners.write_text("owner@example.test\n")
    arguments = ("ingest", "--no-scan", "--owner-names-file", str(owners), str(fixture))
    cli(archive, *arguments)
    assert len(list(archive.glob("processing-pst/*/receipt.json"))) == 1
    assert not (archive / "processing-libpff").exists()
    configure(archive, "pst", {"redundant_import": True})
    cli(archive, *arguments)
    assert len(list(archive.glob("processing-pst/*/receipt.json"))) == 2
    receipts = list(archive.glob("processing-libpff/*/receipt.json"))
    assert len(receipts) == 1
    assert PffReceipt.model_validate_json(receipts[0].read_text()).complete
    cli(archive, *arguments)
    assert len(list(archive.glob("processing-pst/*/receipt.json"))) == 2
    assert len(list(archive.glob("processing-libpff/*/receipt.json"))) == 1


def test_redundant_cli_runs_both_readers_after_partial_failure_and_deduplicates(tmp_path: Path) -> None:
    """A failed Rust pass cannot suppress libpff; retries keep all variants without multiplying content."""
    archive = tmp_path / "archive"
    archive.mkdir()
    configure(archive, "pst", {"redundant_import": True})
    owners = tmp_path / "owners.txt"
    owners.write_text("owner@example.test\n")
    command = [sys.executable, "-m", "mailarchiver", "--archive", str(archive), "ingest", "--no-scan",
               "--defer-content", "--owner-names-file", str(owners), str(PST)]
    counts: list[int] = []
    for _attempt in range(2):
        result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=90)
        assert result.returncode != 0 and "Redundant PST Import incomplete" in result.stderr
        with sqlite3.connect(archive / "archive.sqlite3") as db:
            counts.append(db.execute("SELECT count(*) FROM messages").fetchone()[0])
    assert counts == [25, 25]  # 11 complete Rust records and 14 libpff reconstructions, including flagged partials.
    for path in archive.glob("processing-pst/*/receipt.json"):
        assert ImportReceipt.model_validate_json(path.read_text()).emitted == 11
    for path in archive.glob("processing-libpff/*/receipt.json"):
        assert PffReceipt.model_validate_json(path.read_text()).emitted == 14
    with sqlite3.connect(archive / "processing.sqlite3") as db:
        assert db.execute("SELECT address FROM addresses WHERE address='saqib.razzaq@xp.local'").fetchone()
    assert not verify_archive(archive)


@pytest.mark.parametrize("setting", ["timeout_seconds", "max_message_bytes"])
def test_libpff_limits_leave_retryable_evidence(tmp_path: Path, setting: str) -> None:
    """A deadline or byte cap cannot silently mark an unprocessed cache complete."""
    configure(tmp_path, "ost", {setting: 0.000000001 if setting == "timeout_seconds" else 64})
    registry = load_plugins(archive=tmp_path)
    source = next(item.implementation for item in registry.sources if item.manifest.kind == "file-folder")
    container = next(item for item in source.discover(SourceSpec(locator=str(OST))) if isinstance(item, MailContainer))
    with pytest.raises((RuntimeError, TimeoutError)):
        list(source.messages(container, None))
    path, = tmp_path.glob("processing-libpff/*/receipt.json")
    receipt = PffReceipt.model_validate_json(path.read_text())
    assert not receipt.complete and receipt.emitted == 0 and receipt.failure


def test_ost_cli_directory_ingest_and_search(tmp_path: Path) -> None:
    """Documented directory ingest and search recover OST subjects through the real CLI pipeline."""
    directory = tmp_path / "source"
    directory.mkdir()
    shutil.copyfile(OST, directory / "mail.ost")
    owners = tmp_path / "owners.txt"
    owners.write_text("owner@example.test\n")
    archive = tmp_path / "archive"
    result = subprocess.run([sys.executable, "-m", "mailarchiver", "--archive", str(archive), "ingest", "--no-scan",
                             "--owner-names-file", str(owners), str(directory)],
                            capture_output=True, text=True, check=False, timeout=120)
    assert result.returncode != 0 and "libpff extraction incomplete" in result.stderr
    with sqlite3.connect(archive / "archive.sqlite3") as db:
        assert db.execute("SELECT count(*) FROM messages").fetchone() == (87,)
    cli(archive, "process", "--phase", "content")
    search = subprocess.run([sys.executable, "-m", "mailarchiver.mailsearch", "--archive", str(archive), "subject:newsletter"],
                            capture_output=True, text=True, check=False, timeout=30)
    assert search.returncode == 0 and "newsletter copy" in search.stdout
    assert not verify_archive(archive)
