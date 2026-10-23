# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Archive-backed desktop access to the same processor services as the CLI.

Purpose: Expose durable processing and identity workflows to desktop bridges.

How it works:
Query saved queues, reconstruct resumable policy and lease manual identity writes through
shared services.

Fundamental dependents:

- ``gui_app``: processing dialogs and identity pickers depend on this archive-backed bridge.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from datetime import datetime
from urllib.parse import urlsplit

from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from .sqlite_paths import sqlite_uri

from .__main__ import IngestRequest
from .archive_config import load_archive_config
from .ingest_status import read_ingest_history
from .plugin_api import PluginManifest
from .plugin_loader import builtin_plugin_directory, discover_manifests
from .processing.api import ProcessorManifest
from .processing.contracts import ProcessingPolicy
from .processing.identities import AddressRow, IdentityFilter, ManualDecision, addresses, edit, organizations
from .processing.registry import load_processors
from .processing.store import DATABASE, USER_CANCELLED
from .writer_lock import WriterLease


ABORTED_IMPORTS = "aborted-imports"


class AbortedImports(BaseModel):
    status_ids: set[str] = Field(default_factory=set)


class InterruptedImport(BaseModel):
    status_ids: list[str]
    source_roots: list[str]
    sources: list[str]
    started_at: datetime
    interrupted_at: datetime
    interruption_estimated: bool
    archived: int = 0
    duplicates: int = 0


class ProcessingWork(BaseModel):
    ingest: int = 0
    content: int = 0
    failed: int = 0
    source_roots: list[str] = Field(default_factory=list)
    available: bool = False
    imports: list[InterruptedImport] = Field(default_factory=list)

    @property
    def incomplete(self) -> bool:
        return bool(self.ingest or self.content or self.source_roots)


@contextmanager
def connection(archive: Path) -> Iterator[sqlite3.Connection]:
    with closing(sqlite3.connect(sqlite_uri(archive / DATABASE), uri=True)) as database:
        yield database


def _source_identity(root: str) -> str:
    # IMAP query parameters describe the invocation, not a different account.
    return f"imap://{urlsplit(root).netloc}" if root.startswith("imap://") else root


def source_label(archive: Path, root: str) -> str:
    """Describe a saved source without credentials, connections or locator options."""
    if not root.startswith("imap://"):
        return root
    try:
        accounts = load_archive_config(archive).imap_sources
    except ValueError:
        accounts = []
    account = next((account for account in accounts
                    if account.id == urlsplit(root).netloc), None)
    return f"IMAP: {account.username} on {account.server}:{account.port}" if account else "IMAP: account unavailable"


def unfinished_work(archive: Path) -> ProcessingWork:
    work = ProcessingWork(available=(archive / DATABASE).is_file())
    aborted = AbortedImports()
    if work.available:
        with connection(archive) as database:
            row = database.execute("SELECT value FROM processing_settings WHERE name=?", (ABORTED_IMPORTS,)).fetchone()
            if row:
                aborted = AbortedImports.model_validate_json(row[0])
            for pipeline, count, failed in database.execute("""SELECT json_extract(item_json,'$.pipeline'),
                count(*),sum(status='failed') FROM jobs WHERE status IN ('pending','running','failed')
                GROUP BY json_extract(item_json,'$.pipeline')"""):
                if pipeline == "ingest":
                    work.ingest += count
                else:
                    work.content += count
                work.failed += failed
    # Rootless content runs must not hide a source traversal. Completed or
    # explicitly abandoned runs close the preceding continuation for that root.
    closed: set[str] = set()
    for status in read_ingest_history(archive).statuses:
        if status.state == "completed" or status.status_id in aborted.status_ids:
            closed.update(_source_identity(root) for root in status.source_roots)
            continue
        roots = sorted(root for root in set(status.source_roots) if _source_identity(root) not in closed)
        if not roots:
            continue
        identities = {_source_identity(root) for root in roots}
        prior = next((item for item in work.imports
                      if {_source_identity(root) for root in item.source_roots} == identities), None)
        if prior is not None:
            prior.started_at = min(prior.started_at, status.started_at)
            prior.status_ids.append(status.status_id)
            prior.archived += status.counts.archived
            prior.duplicates += status.counts.duplicates
            continue
        work.imports.append(InterruptedImport(status_ids=[status.status_id], source_roots=roots,
            sources=[source_label(archive, root) for root in roots],
            started_at=status.started_at, interrupted_at=status.completed_at or status.updated_at,
            interruption_estimated=status.completed_at is None,
            archived=status.counts.archived, duplicates=status.counts.duplicates))
    work.source_roots = list(dict.fromkeys(root for item in work.imports for root in item.source_roots))
    return work


def abort_processing(archive: Path, status_ids: list[str]) -> None:
    """Abandon saved traversal/jobs under the writer lease, retaining mail and history."""
    with WriterLease.acquire(archive, str(archive.resolve()), "Abort saved import", uuid4().hex, "2"):
        work = unfinished_work(archive)
        current = {status_id for item in work.imports for status_id in item.status_ids}
        if current != set(status_ids):
            raise ValueError("Saved imports changed. Reopen Continue Processing to review them.")
        if not work.available:
            raise ValueError("No saved processing database is available.")
        with closing(sqlite3.connect(sqlite_uri(archive / DATABASE, mode="rw"), uri=True)) as database, database:
            row = database.execute("SELECT value FROM processing_settings WHERE name=?", (ABORTED_IMPORTS,)).fetchone()
            aborted = AbortedImports.model_validate_json(row[0]) if row else AbortedImports()
            aborted.status_ids.update(current)
            database.execute("INSERT OR REPLACE INTO processing_settings VALUES(?,?)",
                             (ABORTED_IMPORTS, aborted.model_dump_json()))
            database.execute("UPDATE jobs SET status='aborted',detail=? "
                             "WHERE status IN ('pending','running','failed')", (USER_CANCELLED,))


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
                                      "save_owner_defaults": False,
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
        with closing(sqlite3.connect(sqlite_uri(archive / DATABASE, mode="rw"), uri=True)) as database:
            database.execute("PRAGMA foreign_keys=ON")
            edit(database, decision)
