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
