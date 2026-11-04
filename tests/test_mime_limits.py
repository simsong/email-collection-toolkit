# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""PLUGINS.md: bound the whole nested MIME tree before releasing child jobs."""
import base64
from pathlib import Path
import sqlite3
import subprocess
import sys
import tracemalloc
from email import policy
from email.parser import BytesParser

import pytest

from mailarchiver.gui_processing import unfinished_work
from mailarchiver.processing.mime import CHUNK, MimeLimits, extract_parts
from mailarchiver.mime_tree import MimeReaderBudget, MimeReaderLimitError, OriginalMimeParts, walk_mime
from mailarchiver.standalone_verify import verify_archive
from tests.test_cli_processing import HEADER, cli, configure, ingest
from tests.test_message import encoded_nested_message


@pytest.mark.parametrize("limits", [MimeLimits(max_depth=4), MimeLimits(max_parts=4),
    MimeLimits(max_child_messages=3), MimeLimits(max_expanded_bytes=1000)])
def test_encoded_reader_limits_abort_instead_of_returning_partial_ids(limits: MimeLimits) -> None:
    """DESKTOP/SEARCH: shallow individual parses cannot bypass cumulative reader budgets."""
    raw = encoded_nested_message(12, b"unchanged leaf\n")
    original = OriginalMimeParts(entity=raw, budget=MimeReaderBudget(limits=limits))
    with pytest.raises(MimeReaderLimitError, match="limit"):
        list(walk_mime(BytesParser(policy=policy.compat32).parsebytes(raw), original))
    assert original.budget.decoded_bytes <= limits.max_expanded_bytes
    assert original.budget.cached_parts <= limits.max_parts


def test_reader_sibling_decodes_share_one_budget_and_cache() -> None:
    """DESKTOP: repeated requests reuse a decode, while sibling payloads cannot each reset the byte allowance."""
    payload = b"x" * 512
    part = b"--x\nContent-Type: application/octet-stream\nContent-Transfer-Encoding: base64\n\n" + base64.b64encode(payload) + b"\n"
    raw = b"Content-Type: multipart/mixed; boundary=x\n\n" + part * 2 + b"--x--\n"
    original = OriginalMimeParts(entity=raw, budget=MimeReaderBudget(limits=MimeLimits(max_expanded_bytes=1000)))
    assert original.size((1,)) == len(payload) and original.payload((1,)) == payload
    assert original.budget.decoded_bytes == len(payload)
    with pytest.raises(MimeReaderLimitError, match="byte limit"):
        original.size((2,))
    assert original.budget.decoded_bytes == len(payload)


def test_raw_mime_spans_do_not_retain_nested_payload_copies() -> None:
    """DESKTOP: nested unencoded containers share source bytes rather than retaining depth times the message."""
    payload = b"x" * (512 * 1024)
    raw = b"Content-Type: application/pdf\n\n" + payload
    for level in reversed(range(32)):
        raw = f"Content-Type: multipart/mixed; boundary=b{level}\n\n--b{level}\n".encode() + raw + f"\n--b{level}--\n".encode()
    owned_trace = not tracemalloc.is_tracing()
    if owned_trace:
        tracemalloc.start()
    baseline = tracemalloc.get_traced_memory()[0]
    try:
        original = OriginalMimeParts(entity=raw)
        assert original.payload((1,) * 32) == payload
        assert original.size((1,) * 32) == len(payload)
        assert tracemalloc.get_traced_memory()[0] - baseline < 4 * len(raw)
    finally:
        if owned_trace:
            tracemalloc.stop()


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


@pytest.mark.parametrize("code,status,failed", [
    ('from mailarchiver.processing.builtin import ClamAVProcessor\n'
     'policy = item.application.policy.model_copy(update={"scan_policy": "clamav", "scanner_executable": "/no-such-clamdscan", "scanner_configuration": item.content_ref.path})\n'
     'return ClamAVProcessor().process(item.model_copy(update={"application": item.application.model_copy(update={"policy": policy})}))', "scanner-error", False),
    ('from mailarchiver.processing.contracts import ScanFailure, ScanEvidence\n'
     'raise ScanFailure(ScanEvidence(status="unscannable", detail="recorded scanner size-limit response", engine_version="1.4", signature_version="42"))', "unscannable", True),
])
def test_scan_evidence_continues_but_unhandled_custom_plugin_errors_remain_failed(
    tmp_path: Path, code: str, status: str, failed: bool,
) -> None:
    """PLUGINS: accepted scanner evidence files mail; unhandled extension errors retain retry inputs."""
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
    assert (result.returncode != 0) == failed, result.stdout + result.stderr
    with sqlite3.connect(archive / "processing.sqlite3") as database:
        assert database.execute("SELECT scan_status FROM message_state").fetchone() == (status,)
        assert database.execute("SELECT count(*) FROM invocations WHERE kind='file-message'").fetchone() == (0 if failed else 1,)
        if failed:
            assert database.execute("SELECT json_extract(result_json,'$.result.scan.status') FROM invocations WHERE status='failed'").fetchone() == (status,)
            assert Path(database.execute("SELECT content_path FROM messages").fetchone()[0]).read_bytes() == raw
    if not failed:
        assert not verify_archive(archive)
    assert source.read_bytes() == raw
