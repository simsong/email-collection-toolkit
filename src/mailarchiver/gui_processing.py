# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""Archive-backed desktop access to the same processor services as the CLI."""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from .__main__ import IngestRequest
from .ingest_status import read_ingest_history
from .plugin_api import PluginManifest
from .plugin_loader import builtin_plugin_directory, discover_manifests
from .processing.api import ProcessorManifest
from .processing.contracts import ProcessingPolicy
from .processing.identities import AddressRow, IdentityFilter, ManualDecision, addresses, edit, organizations
from .processing.registry import load_processors
from .processing.store import DATABASE
from .writer_lock import WriterLease


class ProcessingWork(BaseModel):
    ingest: int = 0
    content: int = 0
    failed: int = 0
    source_roots: list[str] = Field(default_factory=list)
    available: bool = False

    @property
    def incomplete(self) -> bool:
        return bool(self.ingest or self.content or self.source_roots)


@contextmanager
def connection(archive: Path) -> Iterator[sqlite3.Connection]:
    with closing(sqlite3.connect(f"{(archive / DATABASE).resolve().as_uri()}?mode=ro", uri=True)) as database:
        yield database


def unfinished_work(archive: Path) -> ProcessingWork:
    work = ProcessingWork(available=(archive / DATABASE).is_file())
    # Rootless content runs must not hide an interrupted source traversal.
    seen: set[str] = set()
    for status in read_ingest_history(archive).statuses:
        for root in status.source_roots:
            if root not in seen and status.state != "completed":
                work.source_roots.append(root)
            seen.add(root)
    if work.available:
        with connection(archive) as database:
            for pipeline, count, failed in database.execute("""SELECT json_extract(item_json,'$.pipeline'),
                count(*),sum(status='failed') FROM jobs WHERE status IN ('pending','running','failed')
                GROUP BY json_extract(item_json,'$.pipeline')"""):
                if pipeline == "ingest":
                    work.ingest += count
                else:
                    work.content += count
                work.failed += failed
    return work


def resume_request(archive: Path, ingest: bool, content: bool) -> IngestRequest:
    if not (ingest or content):
        raise ValueError("Select at least one processing phase.")
    if not (archive / DATABASE).is_file():
        raise ValueError("No saved import policy. Use File → Import to continue the source import.")
    with connection(archive) as database:
        row = database.execute("SELECT value FROM processing_settings WHERE name='request'").fetchone()
        policy_row = database.execute("SELECT value FROM processing_settings WHERE name='policy'").fetchone()
    if row:
        request = IngestRequest.model_validate_json(row[0])
    elif policy_row:
        policy = ProcessingPolicy.model_validate_json(policy_row[0])
        request = IngestRequest(archive=archive, owner_rules=policy.owners, earliest_year=policy.earliest_year,
                               index_attachments=policy.index_attachments, scan_policy=policy.scan_policy)
    else:
        raise ValueError("No saved import policy. Use File → Import to continue the source import.")
    return request.model_copy(update={"archive": archive, "roots": unfinished_work(archive).source_roots if ingest else [],
                                      "continue_ingest": ingest, "continue_content": content,
                                      "max_content_jobs": None, "reprocess": False})


def _plugin_roots(archive: Path | None) -> list[Path]:
    roots = [builtin_plugin_directory()]
    if archive is not None and (archive / DATABASE).is_file():
        with connection(archive) as database:
            row = database.execute("SELECT value FROM processing_settings WHERE name='request'").fetchone()
        if row:
            roots.extend(IngestRequest.model_validate_json(row[0]).plugin_dir)
    return roots


def registered_processors(archive: Path | None = None) -> list[ProcessorManifest]:
    return [plugin.manifest for plugin in load_processors(tuple(_plugin_roots(archive)))]


def registered_acquisition_plugins(archive: Path | None = None) -> list[PluginManifest]:
    return list(discover_manifests(_plugin_roots(archive)))


class PickerGroup(BaseModel):
    id: int
    label: str
    addresses: list[AddressRow]
    first_use: str | None = None
    last_use: str | None = None
    messages: int = 0
    signature_messages: int = 0


class PickerPage(BaseModel):
    groups: list[PickerGroup] = Field(default_factory=list)
    total_addresses: int = 0


def picker_page(archive: Path, kind: Literal["name", "institution"], filters: IdentityFilter) -> PickerPage:
    if not (archive / DATABASE).is_file():
        return PickerPage()
    with connection(archive) as database:
        page = PickerPage(total_addresses=database.execute("SELECT count(*) FROM addresses").fetchone()[0])
        if kind == "institution":
            page.groups = [PickerGroup(id=row.organization_id, label=row.name, addresses=row.addresses)
                           for row in organizations(database, filters)]
        else:
            by_id: dict[int, PickerGroup] = {}
            for row in addresses(database, filters):
                group = by_id.get(row.person_id)
                if group is None:
                    group = PickerGroup(id=row.person_id, label=row.canonical_name, addresses=[])
                    page.groups.append(group)
                    by_id[row.person_id] = group
                group.addresses.append(row)
        for group in page.groups:
            ids = [row.address_id for row in group.addresses]
            if not ids:
                continue
            start = filters.start.isoformat() if filters.start else None
            end = filters.end.isoformat() if filters.end else None
            group.first_use, group.last_use, group.messages, group.signature_messages = database.execute(f"""SELECT min(seen_at),max(seen_at),
                count(DISTINCT CASE WHEN kind='header' THEN message_id END),
                count(DISTINCT CASE WHEN kind='signature' THEN message_id END)
                FROM message_addresses WHERE address_id IN ({','.join('?' for _ in ids)})
                AND (? IS NULL OR date(seen_at)>=?) AND (? IS NULL OR date(seen_at)<=?)""", (*ids, start, start, end, end)).fetchone()
    return page


def save_identity(archive: Path, decision: ManualDecision) -> None:
    with WriterLease.acquire(archive, str(archive.resolve()), "GUI identity edit", uuid4().hex, "2"):
        with closing(sqlite3.connect(f"{(archive / DATABASE).resolve().as_uri()}?mode=rw", uri=True)) as database:
            database.execute("PRAGMA foreign_keys=ON")
            edit(database, decision)
