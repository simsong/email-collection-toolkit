# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Purpose: Verify working-object cleanup preserves recovery and canonical mail.
# How it works:
# Exercise real CLI operations and trusted fixture processors against disposable
# archives, checking retained bytes, retries and regeneration after cleanup.
# Fundamental dependents:
# - processing.objects: these regressions establish the deletion safety boundary.
# - processing.production: replay must work without source files or old objects.
"""Processing requirements: release completed working bytes, preserve live recovery."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from mailarchiver.layout import mbox_directory
from mailarchiver.mailsearch import read_message_bytes
from mailarchiver.processing.objects import collect_objects
from mailarchiver.processing.registry import fingerprint, load_processors
from mailarchiver.processing.runtime import run
from mailarchiver.processing.store import connect, submit
from mailarchiver.standalone_verify import verify_archive
from tests.test_cli_processing import HEADER, cli, ingest
from tests.test_processing import MESSAGE, plugin


def objects(archive: Path) -> set[str]:
    return {path.name for path in (archive / "objects").iterdir()}


def test_completed_local_ingest_reprocesses_from_canonical_bytes(tmp_path: Path) -> None:
    """Completed roots/parts are disposable; replay preserves mboxrd and malformed bytes."""
    raw = HEADER + b"Content-Type: text/plain; charset=utf-8\r\n\r\nFrom body\r\n>From quoted\r\n\xff\x00tail"
    archive, source = ingest(tmp_path, raw)
    assert not objects(archive)
    source.unlink()
    for _ in range(2):
        cli(archive, "process", "--reprocess")
        assert not objects(archive)
        assert read_message_bytes(archive, 1) == raw
        assert not verify_archive(archive)
    with sqlite3.connect(archive / "processing.sqlite3") as database:
        assert database.execute("SELECT count(*) FROM invocations WHERE kind='clamav'").fetchone() == (1,)


def test_deferred_and_partial_content_keep_live_inputs(tmp_path: Path) -> None:
    """Deferred work regenerates roots from canonical MBOX and retains live derived parts."""
    raw = HEADER + b"Content-Type: text/plain\r\n\r\nrecoverabletoken\r\n"
    archive, source = ingest(tmp_path, raw, "--defer-content")
    assert not objects(archive)
    source.unlink()
    cli(archive, "process", "--max-jobs", "1")
    assert objects(archive)
    assert json.loads(cli(archive, "processing-status").stdout)["pending"] > 0
    cli(archive, "process")
    assert not objects(archive)
    with sqlite3.connect(archive / "search.sqlite3") as database:
        assert 'recoverabletoken' in database.execute("SELECT content FROM message_fts").fetchone()[0]


def test_failed_rank_keeps_checkpoint_outputs_and_harness_roots(tmp_path: Path) -> None:
    """Retry reuses successful output bytes; terminal harness output is then reclaimed."""
    plugin(tmp_path, "emit", 'return ProcessingResult(emissions=(Emission(content_ref=item.create_content(b"derived"), content_type="text/plain"),))',
           emits='"text/plain"')
    plugin(tmp_path, "gate", 'if not (item.archive.path / "ready").exists():\n    raise ValueError("not ready")\nreturn ProcessingResult()', rank=1)
    plugins = load_processors((tmp_path,))
    archive = tmp_path / "archive"
    with connect(archive, create=True) as database:
        submit(database, archive, MESSAGE, fingerprint(plugins))
        assert run(database, plugins).failed == 1
        derived = archive / "objects" / hashlib.sha256(b"derived").hexdigest()
        assert derived.read_bytes() == b"derived"
        database.execute("DROP TABLE object_references")
        database.commit()
    with connect(archive) as database:
        (archive / "ready").touch()
        assert run(database, plugins, retry=True).failed == 0
        assert not derived.exists()
        root = Path(database.execute("SELECT content_path FROM messages").fetchone()[0])
        assert root.read_bytes() == MESSAGE.read_bytes()
        assert database.execute("SELECT count(*) FROM invocations WHERE kind='emit'").fetchone() == (1,)


def test_collection_preserves_unknown_files_and_symlink_targets(tmp_path: Path) -> None:
    """Only regular owned objects may be deleted; no traversal through directory links."""
    archive = tmp_path / "archive"
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / ("a" * 64)
    target.write_bytes(b"protected")
    with connect(archive, create=True) as database:
        directory = archive / "objects"
        directory.mkdir()
        unknown = directory / "notes.txt"
        unknown.write_text("keep")
        try:
            (directory / ("b" * 64)).symlink_to(target)
        except OSError:
            pytest.skip("platform cannot create fixture symlinks")
        (directory / ("c" * 64)).write_bytes(b"orphan")
        (directory / '.object-interrupted').write_bytes(b"incomplete")
        assert collect_objects(database, archive) == 2
        assert unknown.read_text() == "keep"
        assert target.read_bytes() == b"protected"
        for path in directory.iterdir():
            path.unlink()
        directory.rmdir()
        directory.symlink_to(outside, target_is_directory=True)
        with pytest.raises(ValueError, match="symlink"):
            collect_objects(database, archive)
        assert target.read_bytes() == b"protected"


def test_cleanup_retains_original_if_canonical_copy_is_damaged(tmp_path: Path) -> None:
    """Cleanup must not discard the surviving original when MBOX hash validation fails."""
    raw = HEADER + b"Content-Type: text/plain\r\n\r\noriginaltoken\r\n"
    archive, _ = ingest(tmp_path, raw, "--defer-content")
    path = mbox_directory(archive) / "2024-Archive1.mbox"
    path.write_bytes(path.read_bytes().replace(b"originaltoken", b"damaged-token"))
    (archive / "objects" / hashlib.sha256(raw).hexdigest()).write_bytes(raw)
    result = subprocess.run([sys.executable, "-m", "mailarchiver", "--archive", str(archive), "process"],
                            capture_output=True, text=True, check=False, timeout=60)
    assert result.returncode != 0 and 'hash mismatch' in result.stderr
    assert (archive / "objects" / hashlib.sha256(raw).hexdigest()).read_bytes() == raw


def test_reprocessing_missing_objects_rejects_corrupt_canonical_bytes(tmp_path: Path) -> None:
    """Never synthesize new working bytes from a damaged canonical record."""
    raw = HEADER + b"\r\noriginaltoken\r\n"
    archive, source = ingest(tmp_path, raw)
    source.unlink()
    path = mbox_directory(archive) / "2024-Archive1.mbox"
    original_mbox = path.read_bytes()
    path.write_bytes(original_mbox.replace(b"originaltoken", b"damaged-token"))
    result = subprocess.run([sys.executable, "-m", "mailarchiver", "--archive", str(archive), "process", "--reprocess"],
                            capture_output=True, text=True, check=False, timeout=60)
    assert result.returncode != 0
    assert 'hash mismatch' in result.stderr
    assert not objects(archive)
    with sqlite3.connect(archive / "processing.sqlite3") as database:
        failed = database.execute("SELECT status,detail FROM jobs ORDER BY job_id DESC LIMIT 1").fetchone()
        assert failed[0] == "failed" and "hash mismatch" in failed[1]
    path.write_bytes(original_mbox)
    cli(archive, "process")
    assert not objects(archive)
    assert read_message_bytes(archive, 1) == raw


def test_shared_derived_input_survives_other_message_completion(tmp_path: Path) -> None:
    """A shared output stays live until every message's queued consumer commits."""
    plugin(tmp_path, "emit", 'return ProcessingResult(emissions=(Emission(content_ref=item.create_content(b"shared"), content_type="text/plain"),))',
           emits='"text/plain"')
    plugin(tmp_path, "consume", 'assert item.content_ref.path.read_bytes() == b"shared"\nreturn ProcessingResult()',
           subscribes="text/plain")
    plugins = load_processors((tmp_path,))
    archive = tmp_path / "archive"
    second = tmp_path / "second.eml"
    second.write_bytes(MESSAGE.read_bytes() + b"different")
    with connect(archive, create=True) as database:
        submit(database, archive, MESSAGE, fingerprint(plugins))
        submit(database, archive, second, fingerprint(plugins))
        assert run(database, plugins, max_jobs=2).pending == 2
        shared = archive / "objects" / hashlib.sha256(b"shared").hexdigest()
        assert run(database, plugins, max_jobs=1).pending == 1
        assert shared.read_bytes() == b"shared"
        assert run(database, plugins).pending == 0
        assert not shared.exists()


def test_collection_rejects_uncommitted_processing_records(tmp_path: Path) -> None:
    """No file is removed while processing references are still uncommitted."""
    archive = tmp_path / "archive"
    with connect(archive, create=True) as database:
        directory = archive / "objects"
        directory.mkdir()
        candidate = directory / ("a" * 64)
        candidate.write_bytes(b"orphan")
        database.execute("BEGIN")
        with pytest.raises(ValueError, match="committed processing"):
            collect_objects(database, archive)
        assert candidate.exists()
        database.commit()
        assert collect_objects(database, archive) == 1
