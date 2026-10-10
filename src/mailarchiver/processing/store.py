# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""Fresh framework storage and atomic queue/checkpoint operations."""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import tempfile
from importlib.resources import files
from pathlib import Path

from .api import ArchiveContext, ContentReference, PluginStatistics, ProcessingObject, RAW_MESSAGE, RunReport
from ..catalog import PROCESSING_DATABASE as DATABASE
from ..catalog import require_processing_schema
from ..storage_sync import replace_file, sync_directory

SCHEMA_RESOURCE = "V2__processing.sql"


def connect(archive: Path, *, create: bool = False, production: bool = False) -> sqlite3.Connection:
    path = archive / DATABASE
    if create:
        archive.mkdir(parents=True, exist_ok=True)
        if path.exists() or (not production and (archive / "archive.sqlite3").exists()):
            raise ValueError("initialization requires a fresh framework archive")
    if not path.exists() and not create:
        raise ValueError("framework archive does not exist; use init")
    if create:
        # The production caller owns the archive writer lease. Publish only a
        # closed, complete schema. A crash before replacement leaves
        # an ignored staging file, not a partially initialized processing database.
        descriptor, name = tempfile.mkstemp(prefix=".processing-init-", dir=archive)
        os.close(descriptor)
        temporary = Path(name)
        try:
            staging = sqlite3.connect(temporary)
            try:
                staging.executescript("BEGIN IMMEDIATE;\n" + files("mailarchiver.processing").joinpath("sql", SCHEMA_RESOURCE).read_text() + "\nCOMMIT;")
                require_processing_schema(staging)
            finally:
                staging.close()
            if path.exists():
                raise ValueError("initialization requires a fresh framework archive")
            replace_file(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    database = sqlite3.connect(path, check_same_thread=not production)
    try:
        database.execute("PRAGMA foreign_keys=ON")
        require_processing_schema(database)
    except BaseException:
        database.close()
        raise
    return database


def content_reference(path: Path) -> ContentReference:
    with path.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    return ContentReference(path=path.resolve(), sha256=digest)


def snapshot(archive: Path, path: Path) -> ContentReference:
    """Copy once into content-addressed storage before committing a DB reference."""
    destination = archive / "objects"
    destination.mkdir(exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination, delete=False) as target:
        temporary = Path(target.name)
        try:
            with path.open("rb") as source:
                shutil.copyfileobj(source, target)
            target.flush()
            os.fsync(target.fileno())
        except BaseException:
            target.close()
            temporary.unlink(missing_ok=True)
            raise
    reference = content_reference(temporary)
    final = destination / reference.sha256
    replace_file(temporary, final)
    sync_directory(destination)
    return ContentReference(path=final.resolve(), sha256=reference.sha256)


def enqueue(database: sqlite3.Connection, item: ProcessingObject, registry_hash: str, parent_job_id: int | None = None) -> None:
    payload = item.model_copy(update={"job_id": None}).model_dump_json()
    identity = hashlib.sha256((registry_hash + payload + str(parent_job_id)).encode()).hexdigest()
    database.execute("INSERT OR IGNORE INTO jobs(identity,item_json,registry_hash,parent_job_id,status) VALUES(?,?,?,?,'pending')",
                     (identity, payload, registry_hash, parent_job_id))


def submit(database: sqlite3.Connection, archive: Path, source: Path, registry_hash: str) -> str:
    """Framework harness input: identity is raw digest; production admission follows in PR 2."""
    reference = snapshot(archive, source)
    item = ProcessingObject(archive=ArchiveContext(path=archive.resolve()), message_id=reference.sha256,
                            message_ref=reference, content_ref=reference, content_type=RAW_MESSAGE, pipeline="ingest")
    with database:
        database.execute("INSERT OR IGNORE INTO messages VALUES(?,?,?,?)",
                         (reference.sha256, reference.sha256, reference.sha256, str(reference.path)))
        database.execute("INSERT OR IGNORE INTO occurrences(message_id,source) VALUES(?,?)",
                         (reference.sha256, str(source.resolve())))
        enqueue(database, item, registry_hash)
    return item.message_id


def report(database: sqlite3.Connection, kinds: tuple[str, ...]) -> RunReport:
    stats: list[PluginStatistics] = []
    for kind in kinds:
        count, total, minimum, maximum, errors, timeouts = database.execute(
            "SELECT count(*),coalesce(sum(elapsed),0),min(elapsed),max(elapsed),"
            "coalesce(sum(status='failed' OR json_extract(result_json,'$.outcome')='fail-import'),0),"
            "coalesce(sum(json_extract(result_json,'$.timed_out')=1),0) "
            "FROM invocations WHERE kind=? AND status<>'running'", (kind,)).fetchone()
        stats.append(PluginStatistics(kind=kind, invocations=count, total=total, shortest=minimum,
                                      longest=maximum, average=total / count if count else None, errors=errors, timeouts=timeouts))
    def job_count(status: str) -> int:
        return database.execute("SELECT count(*) FROM jobs WHERE status=?", (status,)).fetchone()[0]
    return RunReport(completed=job_count("completed"), pending=job_count("pending") + job_count("running"),
                     failed=job_count("failed"), aborted=job_count("aborted"), statistics=tuple(stats))


def reprocess(database: sqlite3.Connection, archive: Path, registry_hash: str) -> None:
    """Explicitly supersede old pending work, retaining identity edits and audit history."""
    with database:
        database.execute("UPDATE jobs SET status='aborted',detail='superseded registry' "
                         "WHERE registry_hash<>? AND status IN ('pending','running','failed')", (registry_hash,))
        for message_id, digest, path in database.execute("SELECT message_id,sha256,content_path FROM messages").fetchall():
            reference = ContentReference(path=Path(path), sha256=digest)
            item = ProcessingObject(archive=ArchiveContext(path=archive), message_id=message_id,
                                    message_ref=reference, content_ref=reference,
                                    content_type=RAW_MESSAGE, pipeline="ingest")
            enqueue(database, item, registry_hash)
