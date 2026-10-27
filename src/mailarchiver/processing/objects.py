# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Writer-owned working-object collection for production and the processor harness.

Purpose: Release working bytes at committed message/job boundaries without losing retries.

How it works:
Index job inputs and checkpoint outputs, protect live derived references, and
release raw copies only when canonical MBOX bytes can reconstruct them.

Fundamental dependents:

- ``production``: incremental collection follows durable publication and SQL commits.
- ``runtime``: job completion releases outputs while preserving unfinished consumers.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path

from .api import ProcessingObject, ProcessingResult
from ..storage_sync import sync_directory

OBJECT_NAME = re.compile(r"[0-9a-f]{64}")
LIVE_JOBS = "status IN ('pending','running','failed')"
CanonicalReader = Callable[[str], bytes]


def index_inputs(database: sqlite3.Connection, job_id: int, item: ProcessingObject) -> None:
    database.execute("INSERT OR IGNORE INTO object_references VALUES(?,?,'raw')", (job_id, item.message_ref.sha256))
    if item.content_ref.sha256 != item.message_ref.sha256:
        database.execute("INSERT OR IGNORE INTO object_references VALUES(?,?,'content')", (job_id, item.content_ref.sha256))


def index_outputs(database: sqlite3.Connection, job_id: int, raw_digest: str, result: ProcessingResult) -> None:
    database.executemany("INSERT OR IGNORE INTO object_references VALUES(?,?,'output')",
        ((job_id, output.content_ref.sha256) for output in (*result.emissions, *result.promotions)
         if output.content_ref.sha256 != raw_digest))


def ensure_object_index(database: sqlite3.Connection) -> None:
    """Build an additive, rebuildable reference index once for existing V2 archives."""
    if database.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='object_references'").fetchone():
        return
    with database:
        database.execute("BEGIN IMMEDIATE")
        database.execute("CREATE TABLE object_references(job_id INTEGER NOT NULL REFERENCES jobs,sha256 TEXT NOT NULL,"
                         "role TEXT NOT NULL CHECK(role IN ('raw','content','output')),PRIMARY KEY(job_id,sha256,role))")
        database.execute("CREATE INDEX object_references_digest ON object_references(sha256,role,job_id)")
        database.execute("CREATE INDEX IF NOT EXISTS jobs_message_identity ON jobs(json_extract(item_json,'$.message_id'))")
        database.execute("CREATE INDEX IF NOT EXISTS processing_message_digest ON messages(sha256)")
        for job_id, payload in database.execute("SELECT job_id,item_json FROM jobs"):
            index_inputs(database, job_id, ProcessingObject.model_validate_json(payload))
        for job_id, digest, payload in database.execute(
                "SELECT i.job_id,json_extract(j.item_json,'$.message_ref.sha256'),i.result_json "
                "FROM invocations i JOIN jobs j USING(job_id) WHERE i.status='completed' AND i.result_json IS NOT NULL"):
            index_outputs(database, job_id, digest, ProcessingResult.model_validate_json(payload))


def _candidate_paths(database: sqlite3.Connection, directory: Path, message_id: str | None) -> Iterator[Path]:
    if message_id is None:
        yield from directory.iterdir()
    else:
        for (digest,) in database.execute(
                "SELECT DISTINCT r.sha256 FROM jobs j JOIN object_references r USING(job_id) "
                "WHERE json_extract(j.item_json,'$.message_id')=?", (message_id,)):
            yield directory / digest


def collect_objects(database: sqlite3.Connection, archive: Path,
                    canonical_reader: CanonicalReader | None = None, *, message_id: str | None = None) -> int:
    """Caller holds the writer/publication locks and has committed both databases."""
    directory = archive / "objects"
    if directory.is_symlink():
        raise ValueError("processor objects directory must not be a symlink")
    if not directory.exists():
        return 0
    if database.in_transaction:
        raise ValueError("object cleanup requires committed processing checkpoints")
    removed = 0
    for path in _candidate_paths(database, directory, message_id):
        if path.is_symlink() or not path.is_file():
            continue
        if not OBJECT_NAME.fullmatch(path.name):
            if message_id is None and path.name.startswith('.object-'):
                path.unlink()
                removed += 1
            continue
        roles = {row[0] for row in database.execute(
            "SELECT DISTINCT CASE WHEN j.status='running' THEN 'active' ELSE r.role END "
            "FROM object_references r JOIN jobs j USING(job_id) "
            f"WHERE r.sha256=? AND j.{LIVE_JOBS}", (path.name,))}
        if roles - {'raw'}:
            continue
        original = bool(roles) or database.execute("SELECT 1 FROM messages WHERE sha256=? LIMIT 1", (path.name,)).fetchone()
        if original:
            if canonical_reader is None:
                continue
            try:
                if hashlib.sha256(canonical_reader(path.name)).hexdigest() != path.name:
                    raise ValueError("canonical message digest mismatch")
            except (OSError, ValueError):
                # Unpublished/damaged originals remain even if their jobs ended.
                continue
        path.unlink()
        removed += 1
    if removed:
        sync_directory(directory)
    return removed
