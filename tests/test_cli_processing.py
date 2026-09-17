# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""Production CLI requirements: durable phases, exact child bytes, evidence and rank routing."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from mailarchiver.layout import mbox_directory
from mailarchiver.standalone_verify import verify_archive
from mailarchiver.pst_source import ImportReceipt
from tests.test_end_to_end import mailbox_message_bytes


def cli(archive: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run([sys.executable, "-m", "mailarchiver", "--archive", str(archive), *arguments],
                            capture_output=True, text=True, check=False, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def ingest(tmp_path: Path, raw: bytes, *arguments: str) -> tuple[Path, Path]:
    source = tmp_path / "source.eml"
    source.write_bytes(raw)
    owners = tmp_path / "owners.txt"
    owners.write_text("owner@example.test\n")
    archive = tmp_path / "archive"
    cli(archive, "ingest", "--no-scan", "--owner-names-file", str(owners), *arguments, str(source))
    return archive, source


HEADER = (b"From: Sender <sender@lab.example.ac.uk>\r\nTo: owner@example.test\r\n"
          b"Date: Tue, 02 Jan 2024 10:00:00 +0000\r\nMessage-ID: <parent@example.test>\r\n"
          b"Subject: Parent\r\nMIME-Version: 1.0\r\n")


def test_cli_deferred_content_resume_and_manual_evidence(tmp_path: Path) -> None:
    """Headers are available after ingest; resumable signatures merge subdomains and preserve edits."""
    raw = HEADER + b"Content-Type: text/plain; charset=utf-8\r\n\r\nHello\r\n-- \r\nother@dept.example.ac.uk\r\n"
    archive, source = ingest(tmp_path, raw, "--defer-content")
    with sqlite3.connect(archive / "processing.sqlite3") as db:
        assert db.execute("SELECT address FROM addresses ORDER BY address").fetchall() == [
            ("owner@example.test",), ("sender@lab.example.ac.uk",)]
        assert db.execute("SELECT count(*) FROM invocations WHERE kind='mime'").fetchone() == (0,)
        db.execute("UPDATE persons SET canonical_name='Manual Sender',manual=1 WHERE canonical_name='Sender'")
    pending = json.loads(cli(archive, "processing-status").stdout)
    assert pending["pending"] == 1 and pending["failed"] == 0
    source.unlink()
    cli(archive, "process", "--max-jobs", "1")
    assert json.loads(cli(archive, "processing-status").stdout)["pending"] > 0
    cli(archive, "process")
    assert json.loads(cli(archive, "processing-status").stdout)["pending"] == 0
    entries = json.loads(cli(archive, "identities", "addresses", "--domain", "example.ac.uk", "--start", "2024-01-02", "--end", "2024-01-02").stdout)
    assert len(entries) == 2 and all(entry["messages"] == 1 for entry in entries)
    assert json.loads(cli(archive, "identities", "addresses", "--start", "2025-01-01").stdout) == []
    sender = next(entry for entry in entries if entry["canonical_name"] == "Manual Sender")
    other = next(entry for entry in entries if entry["address"].startswith("other@"))
    cli(archive, "identities", "merge-person", "--subject", str(other["person_id"]), "--target", str(sender["person_id"]))
    orgs = json.loads(cli(archive, "identities", "organizations").stdout)
    for org in orgs:
        cli(archive, "identities", "affiliate", "--subject", str(sender["person_id"]), "--target", str(org["organization_id"]), "--start", "2020-01-01")
    cli(archive, "process", "--reprocess")
    with sqlite3.connect(archive / "processing.sqlite3") as db:
        assert db.execute("SELECT name FROM organizations ORDER BY name").fetchall() == [("example.ac.uk",), ("example.test",)]
        assert db.execute("SELECT count(*) FROM addresses WHERE address='other@dept.example.ac.uk'").fetchone() == (1,)
        assert db.execute("SELECT count(*) FROM persons WHERE canonical_name='Manual Sender' AND manual=1").fetchone() == (1,)
        assert db.execute("SELECT count(*) FROM invocations WHERE kind='clamav'").fetchone() == (1,)
        assert db.execute("SELECT DISTINCT start_date,end_date FROM affiliations WHERE manual=0").fetchall() == [
            ("2024-01-02T10:00:00+00:00", "2024-01-02T10:00:00+00:00")]
        assert db.execute("SELECT count(*) FROM affiliations WHERE manual=1 AND start_date='2020-01-01' AND end_date IS NULL").fetchone() == (2,)
        assert db.execute("SELECT count(*) FROM person_addresses WHERE person_id=?", (sender["person_id"],)).fetchone() == (2,)
    assert mailbox_message_bytes(mbox_directory(archive) / "2024-Archive1.mbox") == [raw]
    assert not verify_archive(archive)


def test_cli_html_preferred_to_rtf_and_attachment_scope(tmp_path: Path) -> None:
    """HTML fallback is synthetic; a plain attachment must not suppress body conversion."""
    raw = HEADER + (b'Content-Type: multipart/mixed; boundary="mix"\r\n\r\n'
        b'--mix\r\nContent-Type: text/html; charset=utf-8\r\n\r\n<p>htmltoken</p>\r\n'
        b'--mix\r\nContent-Type: text/rtf\r\n\r\n{\\rtf1 rtftoken}\r\n'
        b'--mix\r\nContent-Type: text/plain\r\nContent-Disposition: attachment; filename="note.txt"\r\n\r\nattachmenttoken\r\n'
        b'--mix--\r\n')
    archive, source = ingest(tmp_path, raw, "--index-attachments")
    with sqlite3.connect(archive / "processing.sqlite3") as db:
        parts = db.execute("SELECT scope,synthetic,text FROM content_parts ORDER BY scope").fetchall()
        assert parts == [("attachment", 0, "attachmenttoken"), ("body", 1, "htmltoken")]
    with sqlite3.connect(archive / "search.sqlite3") as db:
        content = db.execute("SELECT content FROM message_fts").fetchone()[0]
        attachment_content = db.execute("SELECT content FROM attachment_fts").fetchone()[0]
        assert "htmltoken" in content and "rtftoken" not in content and "attachmenttoken" not in content
        assert "attachmenttoken" in attachment_content
    assert source.read_bytes() == raw
    assert mailbox_message_bytes(mbox_directory(archive) / "2024-Archive1.mbox") == [raw]
    assert not verify_archive(archive)


@pytest.mark.parametrize("dated", [True, False])
def test_cli_attached_message_is_deduplicated_first_class_message(tmp_path: Path, dated: bool) -> None:
    """Attached RFC 5322 bytes become a searchable tagged message with parent path and inherited scan."""
    child = (b"From: child@sub.example.test\r\nTo: owner@example.test\r\n"
             b"Message-ID: <child@example.test>\r\nDate: Wed, 03 Jan 2024 12:00:00 +0000\r\n"
             b"Subject: Child\r\n\r\nchildtoken\r\n")
    if not dated:
        child = child.replace(b"Date: Wed, 03 Jan 2024 12:00:00 +0000\r\n", b"")
    raw = HEADER + (b'Content-Type: multipart/mixed; boundary="mix"\r\n\r\n'
        b'--mix\r\nContent-Type: text/plain\r\n\r\nparenttoken\r\n'
        b'--mix\r\nContent-Type: message/rfc822\r\nContent-Disposition: attachment; filename="child.eml"\r\n\r\n'
        + child + b'\r\n--mix--\r\n')
    archive, source = ingest(tmp_path, raw)
    child_hash = hashlib.sha256(child).hexdigest()
    with sqlite3.connect(archive / "processing.sqlite3") as db:
        assert db.execute("SELECT parent_message_id,part_path FROM occurrences WHERE message_id=?", (child_hash,)).fetchall() == [
            (hashlib.sha256(raw).hexdigest(), "[2]")]
        assert db.execute("SELECT name,background_color FROM tags JOIN message_tags USING(tagid) WHERE message_id=?", (child_hash,)).fetchone() == ("attachment", "#f2f2f2")
        assert db.execute("SELECT scan_status FROM message_state WHERE message_id=?", (child_hash,)).fetchone() == ("not-scanned",)
        assert db.execute("SELECT count(*) FROM invocations WHERE kind='clamav'").fetchone() == (1,)
    source.write_bytes(child)
    cli(archive, "ingest", "--no-scan", "--owner-names-file", str(tmp_path / "owners.txt"), str(source))
    with sqlite3.connect(archive / "processing.sqlite3") as db:
        assert db.execute("SELECT count(*) FROM occurrences WHERE message_id=?", (child_hash,)).fetchone() == (2,)
        assert db.execute("SELECT count(*) FROM invocations WHERE kind='clamav'").fetchone() == (1,)
    assert set(mailbox_message_bytes(mbox_directory(archive) / "2024-Archive1.mbox")) == {raw, child}
    assert not verify_archive(archive)


def test_cli_pst_partial_import_preserves_evidence_and_valid_records(tmp_path: Path) -> None:
    """Real Rust importer partial failure retains its tail and files prior complete records once."""
    fixture = Path(__file__).resolve().parents[1] / "rust/mct-importer/tests/fixtures/mail.pst"
    source = tmp_path / "mail.pst"
    source.write_bytes(fixture.read_bytes())
    owners = tmp_path / "owners.txt"
    owners.write_text("owner@example.test\n")
    archive = tmp_path / "archive"
    command = [sys.executable, "-m", "mailarchiver", "--archive", str(archive), "ingest",
               "--no-scan", "--owner-names-file", str(owners), str(source)]
    for _attempt in range(2):
        result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=60)
        assert result.returncode != 0 and "PST extraction incomplete" in result.stderr
        with sqlite3.connect(archive / "archive.sqlite3") as db:
            assert db.execute("SELECT count(*) FROM messages").fetchone() == (11,)
    receipts = list((archive / "processing-pst").glob("*/receipt.json"))
    assert len(receipts) == 2
    for path in receipts:
        receipt = ImportReceipt.model_validate_json(path.read_text())
        assert receipt.emitted == 11 and receipt.exit_code == 1
        assert receipt.source_sha256 == hashlib.sha256(fixture.read_bytes()).hexdigest()
        assert path.with_name("output.mboxrd").exists()
        assert "errors=2" in path.with_name("stderr.txt").read_text()
    cli(archive, "process", "--phase", "content")
    assert source.read_bytes() == fixture.read_bytes()
    assert not verify_archive(archive)
