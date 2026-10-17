# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

# Purpose: Maintain transactional remote checkpoints and hourly acquisition accounting.
# How it works:
# Commit UID/Gmail observations, label hierarchy and distinct new/duplicate totals with catalog
# publication.
# Fundamental dependents:
# - imap_source: successful checkpoints prevent repeated body transfers.
# - imap_cli: status reporting depends on committed account totals.

"""Transactional IMAP progress, label hierarchy and hourly download statistics."""
from __future__ import annotations

from datetime import UTC, datetime
from contextlib import closing
from pathlib import Path
import sqlite3

from .imap_models import ImapMessageMetadata, ImapTotals
from .plugin_api import MailObject, SourceObservation, SourceReference
from .sqlite_paths import sqlite_uri


def initialize_state(database: sqlite3.Connection) -> None:
    # Individual statements preserve any surrounding archive transaction.
    statements = (
        "CREATE TABLE IF NOT EXISTS imap_seen (source_id TEXT NOT NULL,mailbox TEXT NOT NULL,"
        "uid_validity INTEGER NOT NULL,uid INTEGER NOT NULL,gmail_id TEXT,labels_json TEXT NOT NULL,"
        "observation_pk INTEGER NOT NULL REFERENCES observations(observation_pk),"
        "PRIMARY KEY(source_id,mailbox,uid_validity,uid))",
        "CREATE INDEX IF NOT EXISTS imap_seen_gmail ON imap_seen(source_id,gmail_id) WHERE gmail_id IS NOT NULL",
        "CREATE TABLE IF NOT EXISTS imap_hourly (source_id TEXT NOT NULL,hour_utc TEXT NOT NULL,"
        "archived INTEGER NOT NULL DEFAULT 0,duplicates INTEGER NOT NULL DEFAULT 0,"
        "excluded INTEGER NOT NULL DEFAULT 0,errors INTEGER NOT NULL DEFAULT 0,"
        "PRIMARY KEY(source_id,hour_utc))",
        "CREATE TABLE IF NOT EXISTS imap_mailboxes (source_id TEXT NOT NULL,name TEXT NOT NULL,"
        "parent TEXT,PRIMARY KEY(source_id,name))",
    )
    for statement in statements:
        database.execute(statement)


def known_observation(database: sqlite3.Connection, source: SourceReference,
                      metadata: ImapMessageMetadata) -> int | None:
    if metadata.gmail_id is not None:
        row = database.execute("SELECT observation_pk FROM imap_seen WHERE source_id=? AND gmail_id=? LIMIT 1",
                               (source.source_id, metadata.gmail_id)).fetchone()
    else:
        row = database.execute("SELECT observation_pk FROM imap_seen WHERE source_id=? AND mailbox=? "
                               "AND uid_validity=? AND uid=?",
                               (source.source_id, metadata.mailbox or source.native_id, metadata.uid_validity, metadata.uid)).fetchone()
    return None if row is None else int(row[0])


def remember(database: sqlite3.Connection, source: SourceReference,
             metadata: ImapMessageMetadata, observation_pk: int) -> None:
    if metadata.download is None:
        # Metadata-only refreshes, including Gmail UID epoch changes, must keep
        # the original download evidence attached to this committed observation.
        previous = database.execute("SELECT labels_json FROM imap_seen WHERE source_id=? AND observation_pk=? "
                                    "AND json_type(labels_json,'$.download')='object' LIMIT 1",
                                    (source.source_id, observation_pk)).fetchone()
        if previous:
            prior = ImapMessageMetadata.model_validate_json(previous[0])
            metadata = metadata.model_copy(update={"download": prior.download})
    database.execute("INSERT INTO imap_seen VALUES (?,?,?,?,?,?,?) "
                     "ON CONFLICT(source_id,mailbox,uid_validity,uid) DO UPDATE SET "
                     "gmail_id=excluded.gmail_id,labels_json=excluded.labels_json,observation_pk=excluded.observation_pk",
                     (source.source_id, metadata.mailbox or source.native_id, metadata.uid_validity, metadata.uid,
                      metadata.gmail_id, metadata.model_dump_json(), observation_pk))
    for name in (metadata.mailbox or source.native_id, *metadata.labels):
        parts = name.split("/")
        for index in range(1, len(parts) + 1):
            path = "/".join(parts[:index])
            parent = "/".join(parts[:index - 1]) if index > 1 else None
            database.execute("INSERT OR IGNORE INTO imap_mailboxes VALUES (?,?,?)", (source.source_id, path, parent))


def record_download(database: sqlite3.Connection, source: MailObject, observation_pk: int,
                    disposition: str, *, now: datetime | None = None) -> None:
    if source.source.plugin_kind != "imap" or source.provider_metadata_json is None:
        return
    timestamp = now or datetime.now(UTC)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("IMAP download counters require an aware timestamp")
    initialize_state(database)
    metadata = ImapMessageMetadata.model_validate_json(source.provider_metadata_json)
    if disposition in {"archived", "duplicate", "autosave-excluded", "source-metadata-excluded"}:
        remember(database, source.source, metadata, observation_pk)
    category = "archived" if disposition == "archived" else "duplicates" if disposition == "duplicate" else (
        "excluded" if disposition.endswith("excluded") else "errors")
    hour = timestamp.astimezone(UTC).replace(minute=0, second=0, microsecond=0).isoformat()
    database.execute("INSERT INTO imap_hourly(source_id,hour_utc) VALUES (?,?) "
                     "ON CONFLICT(source_id,hour_utc) DO NOTHING", (source.source.source_id, hour))
    # The column comes exclusively from the closed disposition mapping above.
    database.execute(f"UPDATE imap_hourly SET {category}={category}+1 WHERE source_id=? AND hour_utc=?",
                     (source.source.source_id, hour))


def refresh_observation(database: sqlite3.Connection, event: SourceObservation) -> None:
    if event.source.plugin_kind != "imap":
        raise ValueError("Unsupported metadata-only source observation")
    metadata = ImapMessageMetadata.model_validate_json(event.provider_metadata_json)
    observation = known_observation(database, event.source, metadata)
    if observation is None:
        raise RuntimeError("Remote metadata has no committed archive observation; retry synchronization")
    remember(database, event.source, metadata, observation)


def totals(archive: Path) -> ImapTotals:
    path = archive / "archive.sqlite3"
    if not path.exists():
        return ImapTotals()
    with closing(sqlite3.connect(sqlite_uri(path), uri=True)) as database:
        if database.execute("SELECT 1 FROM sqlite_master WHERE name='imap_hourly'").fetchone() is None:
            return ImapTotals()
        row = database.execute("SELECT coalesce(sum(archived),0),coalesce(sum(duplicates),0),"
                               "coalesce(sum(excluded),0),coalesce(sum(errors),0) FROM imap_hourly").fetchone()
        return ImapTotals(archived=row[0], duplicates=row[1], excluded=row[2], errors=row[3])
