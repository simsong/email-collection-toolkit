# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""Read processor-owned attachment provenance without creating a processing database."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path


from pydantic import BaseModel

from .sqlite_paths import sqlite_uri

from .processing.store import DATABASE

# Production processor keys are canonical hashes, permitting primary-key probes.
ATTACHED_MESSAGES_SQL = """SELECT m.message_pk FROM main.messages m
    CROSS JOIN identities.message_state s ON s.message_id=m.sha256 AND s.catalog_message_pk=m.message_pk
    CROSS JOIN identities.tags t ON t.name='attachment'
    CROSS JOIN identities.message_tags mt ON mt.message_id=s.message_id AND mt.tagid=t.tagid
    WHERE m.message_pk IN ({placeholders})"""


class AttachedOrigin(BaseModel):
    parent_message_pk: int | None
    parent_message_id: str
    part_path: tuple[int, ...]


def attached_messages(archive: Path, message_pks: list[int]) -> set[int]:
    if not message_pks or not (archive / DATABASE).is_file():
        return set()
    with closing(sqlite3.connect(sqlite_uri(archive / "archive.sqlite3"), uri=True)) as database:
        database.execute("ATTACH DATABASE ? AS identities", (sqlite_uri(archive / DATABASE),))
        result: set[int] = set()
        for offset in range(0, len(message_pks), 500):
            batch = message_pks[offset:offset + 500]
            result.update(row[0] for row in database.execute(
                ATTACHED_MESSAGES_SQL.format(placeholders=','.join('?' for _ in batch)), batch))
        return result


def attached_origins(archive: Path, message_pk: int) -> list[AttachedOrigin]:
    if not (archive / DATABASE).is_file():
        return []
    with closing(sqlite3.connect(sqlite_uri(archive / DATABASE), uri=True)) as database:
        return [AttachedOrigin(parent_message_pk=row[0], parent_message_id=row[1], part_path=json.loads(row[2]))
                for row in database.execute("""SELECT DISTINCT parent.catalog_message_pk,o.parent_message_id,o.part_path
                FROM occurrences o JOIN message_state child ON child.message_id=o.message_id
                LEFT JOIN message_state parent ON parent.message_id=o.parent_message_id
                WHERE child.catalog_message_pk=? AND o.parent_message_id IS NOT NULL
                ORDER BY o.parent_message_id,o.part_path""", (message_pk,))]
