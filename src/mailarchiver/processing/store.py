# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Fresh framework storage and atomic queue/checkpoint operations.

Purpose: Persist processor objects, queues, invocations and replay generations.

How it works:
Snapshot verified content and atomically track job completion, handoffs and reprocessing
without losing manual state.

Fundamental dependents:

- ``processing.runtime``: interruption-safe dispatch relies on these durable checkpoints.
- ``processing.production``: deferred content work and replay need the same saved objects.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
import tempfile
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path
from uuid import uuid4

from .objects import OBJECT_NAME, ensure_object_index, index_inputs
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
    database = sqlite3.connect(path)
    try:
        database.execute("PRAGMA foreign_keys=ON")
        require_processing_schema(database)
        ensure_object_index(database)
        with database:
            database.execute("CREATE INDEX IF NOT EXISTS jobs_status_order ON jobs(status,job_id)")
            database.execute("CREATE INDEX IF NOT EXISTS jobs_parent ON jobs(parent_job_id)")
            database.execute("CREATE TABLE IF NOT EXISTS accepted_invocations ("
                             "invocation_id INTEGER PRIMARY KEY REFERENCES invocations, result_json TEXT NOT NULL)")
    except BaseException:
        database.close()
        raise
    return database


def content_reference(path: Path) -> ContentReference:
    with path.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    return ContentReference(path=path.resolve(), sha256=digest)


def _store_object(destination: Path, chunks: Iterable[bytes]) -> ContentReference:
    """Write, hash and fsync in one pass, then publish under the SHA-256 name."""
    destination.mkdir(exist_ok=True)
    digest = hashlib.sha256()
    with tempfile.NamedTemporaryFile(dir=destination, prefix=".object-", delete=False) as target:
        temporary = Path(target.name)
        try:
            for chunk in chunks:
                digest.update(chunk)
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
        except BaseException:
            target.close()
            temporary.unlink(missing_ok=True)
            raise
    final = destination / digest.hexdigest()
    if _GROUPED and os.name != "nt":
        # The file itself is already fsynced. Its directory entry only has to be
        # durable before a commit can refer to it, so one directory sync per
        # group commit (see ``_commit``) replaces one per object.
        temporary.replace(final)
        with _DIRECTORY_SYNC:
            _UNSYNCED_DIRECTORIES.add(destination)
    else:
        replace_file(temporary, final)  # also fsyncs the objects directory on POSIX
    return ContentReference(path=final.resolve(), sha256=digest.hexdigest())


def _file_chunks(path: Path) -> Iterator[bytes]:
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            yield chunk


def snapshot(archive: Path, path: Path) -> ContentReference:
    """Copy once into content-addressed storage before committing a DB reference."""
    destination = archive / "objects"
    resolved = path.resolve()
    if (resolved.parent == destination.resolve() and OBJECT_NAME.fullmatch(resolved.name)
            and not path.is_symlink()):
        # Already this archive's object (a checkpoint output being re-queued).
        # Verify it instead of copying it onto itself with three more fsyncs.
        reference = content_reference(resolved)
        if reference.sha256 == resolved.name:
            return reference
    return _store_object(destination, _file_chunks(path))


def snapshot_bytes(archive: Path, data: bytes) -> ContentReference:
    """``snapshot`` for bytes already in memory, without a staging file."""
    return _store_object(archive / "objects", (data,))


USER_CANCELLED = "Cancelled by user"

# Connections whose checkpoint units currently share one SQLite transaction.
# Each unit is still all-or-nothing (a savepoint); only durability is grouped.
_GROUPED: set[int] = set()
_COMMIT_FIRST: dict[int, list[sqlite3.Connection]] = {}
# Object directories renamed into while a group was open, synced before any commit.
# Shared by every owner thread (the GUI can import into several archives), so
# the lock makes a commit wait for a sync another owner already started.
_UNSYNCED_DIRECTORIES: set[Path] = set()
_DIRECTORY_SYNC = threading.Lock()


@contextmanager
def grouped(database: sqlite3.Connection) -> Iterator[None]:
    """Make nested ``unit`` blocks share one commit, issued when the group ends.

    Rollback-journal commits cost several fsyncs each, and one message used to
    commit dozens of times. A crash now loses only the uncommitted group, which
    replays from the last commit exactly like a crash between two old commits.
    The group commits on exceptions too: every closed unit was already
    complete, matching the old per-unit commit semantics. Nested groups join
    the outer one.
    """
    key = id(database)
    if key in _GROUPED:
        yield
        return
    _GROUPED.add(key)
    try:
        yield
    finally:
        _GROUPED.discard(key)
        _commit(database)


def flush_group(database: sqlite3.Connection) -> None:
    """Commit the open group early, e.g. before object cleanup requires it."""
    _commit(database)


def _sync_object_directories() -> None:
    """Sync every pending directory; each stays pending until its sync succeeds."""
    with _DIRECTORY_SYNC:
        for directory in sorted(_UNSYNCED_DIRECTORIES):
            sync_directory(directory)
            _UNSYNCED_DIRECTORIES.discard(directory)


def _commit(database: sqlite3.Connection) -> None:
    """Sync objects, commit dependents, then commit ``database``.

    If a barrier before the final commit fails, ``database`` is rolled back so
    no later commit (such as saving the policy at close) can mark jobs
    completed whose objects or derived rows are not durable. Those jobs rerun.
    """
    try:
        _sync_object_directories()
        for dependent in _COMMIT_FIRST.get(id(database), ()):
            if dependent.in_transaction:
                dependent.commit()
    except BaseException:
        if database.in_transaction:
            database.rollback()
        raise
    if database.in_transaction:
        database.commit()


@contextmanager
def commit_first(database: sqlite3.Connection, dependent: sqlite3.Connection) -> Iterator[None]:
    """Commit ``dependent`` before every grouped commit of ``database``.

    Derived databases (search) written inside a processing group must become
    durable before the processing commit marks those jobs completed; a crash
    between the two commits then only repeats idempotent derived writes.
    """
    hooks = _COMMIT_FIRST.setdefault(id(database), [])
    hooks.append(dependent)
    try:
        yield
    finally:
        hooks.remove(dependent)
        if not hooks:
            _COMMIT_FIRST.pop(id(database), None)


@contextmanager
def unit(database: sqlite3.Connection) -> Iterator[None]:
    """An atomic checkpoint: commits alone, or as a savepoint inside ``grouped``."""
    if id(database) not in _GROUPED:
        with database:
            yield
            _sync_object_directories()
        return
    if not database.in_transaction:
        database.execute("BEGIN")
    database.execute("SAVEPOINT checkpoint_unit")
    try:
        yield
    except BaseException:
        database.execute("ROLLBACK TO checkpoint_unit")
        database.execute("RELEASE checkpoint_unit")
        raise
    database.execute("RELEASE checkpoint_unit")


def enqueue(database: sqlite3.Connection, item: ProcessingObject, registry_hash: str, parent_job_id: int | None = None) -> None:
    payload = item.model_copy(update={"job_id": None}).model_dump_json()
    # Reuse legacy child rows too: runtime policy is not a new processing result.
    if parent_job_id is not None and database.execute(
            "SELECT 1 FROM jobs WHERE parent_job_id=? AND registry_hash=? AND "
            "json_remove(item_json,'$.application.policy')=json_remove(?,'$.application.policy') LIMIT 1",
            (parent_job_id, registry_hash, payload)).fetchone():
        return
    identity = hashlib.sha256((registry_hash + payload + str(parent_job_id)).encode()).hexdigest()
    row = database.execute("INSERT OR IGNORE INTO jobs(identity,item_json,registry_hash,parent_job_id,status) "
                           "VALUES(?,?,?,?,'pending') RETURNING job_id", (identity, payload, registry_hash, parent_job_id)).fetchone()
    if row is not None:
        index_inputs(database, int(row[0]), item)


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
    generation = str(uuid4())
    with database:
        database.execute("UPDATE jobs SET status='aborted',detail='explicit reprocessing' "
                         "WHERE status IN ('pending','running','failed')")
        for message_id, digest, path in database.execute("SELECT message_id,sha256,content_path FROM messages").fetchall():
            reference = ContentReference(path=Path(path), sha256=digest)
            item = ProcessingObject(archive=ArchiveContext(path=archive), message_id=message_id,
                                    message_ref=reference, content_ref=reference,
                                    content_type=RAW_MESSAGE, pipeline="ingest", generation=generation)
            enqueue(database, item, registry_hash)
