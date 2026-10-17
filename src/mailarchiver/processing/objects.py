# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Purpose: Reclaim processor working files without losing retry or replay inputs.
# How it works:
# Trace live jobs and checkpoint outputs, retain unpublished originals, and unlink
# only unreferenced owned objects after the caller has stopped processor workers.
# Fundamental dependents:
# - production: completed archive operations release reconstructable working data.
# - runtime: standalone harness runs release outputs while preserving source snapshots.
"""Writer-owned working-object collection for production and the processor harness."""
from __future__ import annotations

import hashlib
import re
import sqlite3
from collections.abc import Callable
from pathlib import Path

from .api import ProcessingObject, ProcessingResult
from ..storage_sync import sync_directory

OBJECT_NAME = re.compile(r"[0-9a-f]{64}")
LIVE_JOBS = "status IN ('pending','running','failed')"
CanonicalReader = Callable[[str], bytes]


def collect_objects(database: sqlite3.Connection, archive: Path,
                    canonical_reader: CanonicalReader | None = None) -> int:
    """Caller holds the writer lease and has stopped all dispatch/publication work."""
    directory = archive / "objects"
    if directory.is_symlink():
        raise ValueError("processor objects directory must not be a symlink")
    if not directory.exists():
        return 0
    if database.in_transaction:
        raise ValueError("object cleanup requires committed processing checkpoints")
    retained: set[str] = set()
    for (payload,) in database.execute(f"SELECT item_json FROM jobs WHERE {LIVE_JOBS}"):
        item = ProcessingObject.model_validate_json(payload)
        retained.update((item.message_ref.sha256, item.content_ref.sha256))
    # A successful invocation on an unfinished job may be replayed after a rank
    # failure, even if its emissions have not been published as child jobs yet.
    for (payload,) in database.execute(
            "SELECT result_json FROM invocations WHERE status='completed' AND result_json IS NOT NULL "
            f"AND job_id IN (SELECT job_id FROM jobs WHERE {LIVE_JOBS})"):
        result = ProcessingResult.model_validate_json(payload)
        retained.update(output.content_ref.sha256 for output in (*result.emissions, *result.promotions))
    for (digest,) in database.execute("SELECT sha256 FROM messages"):
        if digest in retained:
            continue
        path = directory / digest
        if not path.exists():
            continue
        if canonical_reader is None:
            retained.add(digest)
            continue
        try:
            raw = canonical_reader(digest)
            if hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError("canonical message digest mismatch")
        except (OSError, ValueError):
            # Unpublished or damaged canonical messages must keep their original
            # snapshots; cleanup must never remove the sole surviving raw copy.
            retained.add(digest)
    removed = 0
    for path in directory.iterdir():
        if path.is_symlink() or not path.is_file():
            continue
        if (OBJECT_NAME.fullmatch(path.name) and path.name not in retained) or path.name.startswith('.object-'):
            path.unlink()
            removed += 1
    if removed:
        sync_directory(directory)
    return removed
