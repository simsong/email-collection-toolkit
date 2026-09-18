# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""PLUGINS.md: bound the whole nested MIME tree before releasing child jobs."""
import base64
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from mailarchiver.gui_processing import unfinished_work
from mailarchiver.processing.mime import CHUNK, MimeLimits, extract_parts
from mailarchiver.standalone_verify import verify_archive
from tests.test_cli_processing import HEADER, cli, configure, ingest


def attached(raw: bytes, boundary: bytes) -> bytes:
    return (HEADER + b'Content-Type: multipart/mixed; boundary="' + boundary + b'"\r\n\r\n--'
            + boundary + b'\r\nContent-Type: message/rfc822\r\nContent-Transfer-Encoding: base64\r\n\r\n'
            + base64.b64encode(raw) + b'\r\n--' + boundary + b'--\r\n')


@pytest.mark.parametrize("processor,setting,value,diagnostic", [
    ("mime", "max_expanded_bytes", 256, "expanded-byte"),
    ("mime", "max_child_messages", 1, "count limit"),
    ("mime", "max_parts", 1, "part limit"),
    ("mime", "max_depth", 1, "depth limit"),
    ("attached-message", "max_depth", 1, "depth limit"),
])
def test_root_budget_failure_and_retry(tmp_path: Path, processor: str, setting: str, value: int, diagnostic: str) -> None:
    """Nested children share a preflight budget; a failed rank publishes none."""
    raw = attached(attached(HEADER + b"\r\nleaf", b"inner"), b"outer")
    archive, source = ingest(tmp_path, raw, "--defer-content")
    configure(archive, processor, {setting: value})
    failed = subprocess.run([sys.executable, "-m", "mailarchiver", "--archive", str(archive),
                             "process"], capture_output=True, text=True, check=False, timeout=30)
    assert failed.returncode != 0 and diagnostic in failed.stderr
    with sqlite3.connect(archive / "archive.sqlite3") as database:
        assert database.execute("SELECT count(*) FROM messages").fetchone() == ((2,) if processor == "attached-message" else (1,))
    with sqlite3.connect(archive / "processing.sqlite3") as database:
        assert database.execute("SELECT count(*) FROM jobs WHERE status='failed'").fetchone()[0] > 0
    assert source.read_bytes() == raw and not verify_archive(archive)
    assert unfinished_work(archive).incomplete and unfinished_work(archive).failed
    configure(archive, processor, {})
    cli(archive, "process")
    with sqlite3.connect(archive / "archive.sqlite3") as database:
        assert database.execute("SELECT count(*) FROM messages").fetchone() == (3,)
    assert not verify_archive(archive)


@pytest.mark.parametrize("suffix", [b"=41\r\n", b"=\r\nnext", b"=41", b"=\nnext"])
def test_streamed_quoted_printable_chunk_boundaries(tmp_path: Path, suffix: bytes) -> None:
    """Budgeted QP writes preserve escapes and soft breaks crossing a read chunk."""
    import quopri
    payload = b"a" * (CHUNK - 1) + suffix
    source = tmp_path / "input.eml"
    source.write_bytes(b"Content-Type: text/plain\nContent-Transfer-Encoding: quoted-printable\n\n" + payload)
    result = extract_parts(source, tmp_path, limits=MimeLimits(max_expanded_bytes=2 * CHUNK))
    assert result.parts[0].reference.path.read_bytes() == quopri.decodestring(payload)


@pytest.mark.parametrize("encoding,payload", [
    ("base64", b"!!!!"), ("base64", b"SGVsbG8"), ("base64", b"YQ==Yg=="),
    ("quoted-printable", b"bad=XY"), ("quoted-printable", b"unfinished="),
    ("unknown", b"uninterpretable"),
])
def test_invalid_attached_transfer_never_promoted(tmp_path: Path, encoding: str, payload: bytes) -> None:
    """PLUGINS preservation: invalid decoding cannot invent canonical child bytes."""
    raw = HEADER + f"Content-Type: message/rfc822\r\nContent-Transfer-Encoding: {encoding}\r\n\r\n".encode() + payload
    archive, source = ingest(tmp_path, raw, "--defer-content")
    failed = subprocess.run([sys.executable, "-m", "mailarchiver", "--archive", str(archive), "process"],
                            capture_output=True, text=True, check=False, timeout=30)
    assert failed.returncode != 0 and "attached-message transfer decoding failed" in failed.stderr
    with sqlite3.connect(archive / "archive.sqlite3") as database:
        assert database.execute("SELECT count(*) FROM messages").fetchone() == (1,)
    assert unfinished_work(archive).failed
    assert source.read_bytes() == raw and not verify_archive(archive)


@pytest.mark.parametrize("code,status", [
    ('from mailarchiver.processing.builtin import ClamAVProcessor\n'
     'policy = item.application.policy.model_copy(update={"scan_policy": "clamav", "scanner_executable": "/no-such-clamdscan", "scanner_configuration": item.content_ref.path})\n'
     'return ClamAVProcessor().process(item.model_copy(update={"application": item.application.model_copy(update={"policy": policy})}))', "scanner-error"),
    ('from mailarchiver.processing.contracts import ScanFailure, ScanEvidence\n'
     'raise ScanFailure(ScanEvidence(status="unscannable", detail="recorded scanner size-limit response", engine_version="1.4", signature_version="42"))', "unscannable"),
])
def test_scan_failure_evidence_blocks_filing(tmp_path: Path, code: str, status: str) -> None:
    """Typed failure publication retains evidence and raw input, never releasing filing."""
    from tests.test_processing import plugin
    plugin(tmp_path / "plugins", "test-scan", code, rank=1)
    source = tmp_path / "message.eml"
    raw = HEADER + b"\r\noriginal"
    source.write_bytes(raw)
    owners = tmp_path / "owners.txt"
    owners.write_text("owner@example.test\n")
    archive = tmp_path / "archive"
    result = subprocess.run([sys.executable, "-m", "mailarchiver", "--archive", str(archive), "ingest",
        "--no-scan", "--owner-names-file", str(owners), "--plugin-dir", str(tmp_path / "plugins"), str(source)],
        capture_output=True, text=True, check=False, timeout=30)
    assert result.returncode != 0
    with sqlite3.connect(archive / "processing.sqlite3") as database:
        assert database.execute("SELECT scan_status FROM message_state").fetchone() == (status,)
        assert database.execute("SELECT count(*) FROM invocations WHERE kind='file-message'").fetchone() == (0,)
        assert database.execute("SELECT json_extract(result_json,'$.scan.status') FROM invocations WHERE status='failed'").fetchone() == (status,)
        assert Path(database.execute("SELECT content_path FROM messages").fetchone()[0]).read_bytes() == raw
    assert source.read_bytes() == raw


def test_scan_outcome_classification() -> None:
    """Recorded scanner responses distinguish limits, errors and actual positive detection."""
    from mailarchiver.processing.builtin import scan_result
    from mailarchiver.processing.contracts import ScanEvidence
    versions = ScanEvidence(status="not-scanned", engine_version="1.4", signature_version="42")
    assert scan_result(0, b"file: OK", b"", versions).status == "clean"
    assert scan_result(2, b"", b"connection failed", versions).status == "scanner-error"
    assert scan_result(1, b"file: Heuristics.Limits.Exceeded.MaxFileSize FOUND\n", b"", versions).status == "unscannable"
    mixed = b"file: Heuristics.Encrypted.Zip FOUND\nfile: Win.TestVirus FOUND\n"
    assert scan_result(1, mixed, b"", versions).status == "infected"
