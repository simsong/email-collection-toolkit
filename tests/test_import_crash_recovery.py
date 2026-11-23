# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Requirement (#202): grouping processor checkpoints into one SQLite commit per
# message or job must keep imports crash-safe. Each case SIGKILLs a real
# ect-import subprocess partway through (during message ingest, or during the
# later body/attachment processing phase), restarts the import, and compares
# the result with an uninterrupted import of the same synthetic corpus: the
# archive must verify, hold exactly the same messages, and leave no unfinished jobs.

from __future__ import annotations

import os
import signal
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from mailarchiver.standalone_verify import verify_archive

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from benchmark_import import verify  # noqa: E402
from perf_corpus import CorpusManifest, generate  # noqa: E402

BIN = Path(sys.executable).parent  # the environment's installed ect-* entry points


def _count(database: Path, query: str) -> int:
    try:
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=0.2) as connection:
            return int(connection.execute(query).fetchone()[0])
    except sqlite3.Error:
        return -1


def _archived(archive: Path) -> int:
    return _count(archive / "archive.sqlite3", "SELECT count(*) FROM messages")


def _content_done(archive: Path) -> int:
    return _count(archive / "processing.sqlite3",
                  "SELECT count(*) FROM jobs WHERE status='completed' "
                  "AND json_extract(item_json,'$.pipeline')='content'")


def _create(archive: Path) -> None:
    subprocess.run([str(BIN / "ect-create"), str(archive), "--owner", "owner@example.test"],
                   check=True, capture_output=True, timeout=60)


def _import_command(archive: Path, corpus: Path) -> list[str]:
    return [str(BIN / "ect-import"), "--archive", str(archive), "--no-scan", "--workers", "2",
            str(corpus / "maildir"), str(corpus / "mbox")]


def _import(archive: Path, corpus: Path) -> None:
    result = subprocess.run(_import_command(archive, corpus), capture_output=True, text=True, timeout=600)
    assert result.returncode == 0, result.stderr[-2000:]


def _messages(archive: Path) -> list[tuple[str, str]]:
    with sqlite3.connect(archive / "archive.sqlite3") as connection:
        return sorted(connection.execute("SELECT message_id_normalized, sha256 FROM messages"))


def _unfinished_jobs(archive: Path) -> int:
    return _count(archive / "processing.sqlite3", "SELECT count(*) FROM jobs WHERE status IN ('pending','running')")


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("crash-corpus") / "corpus"
    # Large enough (~170 messages) that body processing spans more than one
    # 64-message commit batch, so the processing kill point is observable.
    generate(root, 1536 * 1024, seed=7)
    return root


@pytest.fixture(scope="module")
def reference(corpus: Path, tmp_path_factory: pytest.TempPathFactory) -> list[tuple[str, str]]:
    archive = tmp_path_factory.mktemp("crash-reference") / "reference.mailarchive"
    _create(archive)
    _import(archive, corpus)
    return _messages(archive)


@pytest.mark.parametrize(("phase", "reached"), [
    ("ingest", lambda archive: _archived(archive) >= 5),
    ("processing", lambda archive: _content_done(archive) >= 3),
])
def test_sigkill_mid_import_then_restart_matches_uninterrupted_import(
        tmp_path: Path, corpus: Path, reference: list[tuple[str, str]],
        phase: str, reached: Callable[[Path], bool]) -> None:
    archive = tmp_path / "crashed.mailarchive"
    _create(archive)
    with subprocess.Popen(_import_command(archive, corpus), stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL) as process:
        deadline = time.monotonic() + 300
        while not reached(archive) and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert process.poll() is None, f"import finished before reaching the {phase} kill point"
        os.kill(process.pid, signal.SIGKILL)
        process.wait(timeout=30)
    interrupted = _archived(archive)
    assert 0 < interrupted <= len(reference)
    _import(archive, corpus)
    assert not verify_archive(archive, quiet=True)
    assert _messages(archive) == reference
    assert _unfinished_jobs(archive) == 0
    assert verify(archive, _manifest(corpus)) == []


def _manifest(corpus: Path) -> CorpusManifest:
    return CorpusManifest.model_validate_json((corpus / "MANIFEST.json").read_text(encoding="utf-8"))


def test_uninterrupted_import_holds_exactly_the_corpus_and_damage_is_detected(tmp_path: Path, corpus: Path) -> None:
    """End to end: every generated message is cataloged byte-exact, searchable and processed."""
    archive = tmp_path / "complete.mailarchive"
    _create(archive)
    _import(archive, corpus)
    manifest = _manifest(corpus)
    assert verify(archive, manifest) == []
    with sqlite3.connect(archive / "search.sqlite3") as search:
        search.execute("DELETE FROM message_metadata WHERE rowid = (SELECT min(rowid) FROM message_metadata)")
    with sqlite3.connect(archive / "archive.sqlite3") as catalog:
        catalog.execute("PRAGMA foreign_keys=OFF")
        catalog.execute("UPDATE messages SET sha256 = ? WHERE message_pk = (SELECT max(message_pk) FROM messages)", ("0" * 64,))
    problems = verify(archive, manifest)
    assert any("not searchable" in problem for problem in problems), problems
    assert any("not in the catalog" in problem for problem in problems), problems
    assert any("not in the corpus" in problem for problem in problems), problems
