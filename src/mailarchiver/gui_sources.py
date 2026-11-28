# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""List, enable and rescan the sources an archive was imported from (the Sources tab).

Purpose: give the search window's Sources tab one list of every imported top-level
directory or file and every IMAP account, each with a checkbox saying whether
Rescan Imports… should include it, and let IMAP accounts be reconfigured.

How it works: local sources are kept in the archive's config.yaml (`local_sources`).
Each import records its local roots there; roots of imports made before this list
existed are recovered from the Activity history. IMAP accounts already live in
config.yaml with an `enabled` flag. A rescan imports every enabled source again
through the ordinary import service, so unchanged files are skipped by their
recorded size and modification time, servers are rechecked, and duplicates are
recognized as always. Credentials never pass through this module except to the
native keyring.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from .archive_config import LocalSource, load_archive_config, save_archive_config
from .imap_models import ImapAccount
from .imap_source import imap_locator
from .ingest_status import read_ingest_history

SourceKind = Literal["directory", "file", "imap"]


class SourceEntry(BaseModel):
    """One row of the Sources tab."""

    id: str
    kind: SourceKind
    label: str
    detail: str
    enabled: bool
    available: bool
    server: str | None = None
    port: int | None = None
    username: str | None = None
    tls: Literal["implicit", "starttls"] | None = None


def local_source_id(path: str) -> str:
    return "local-" + hashlib.sha256(os.path.normcase(os.path.abspath(path)).encode()).hexdigest()[:16]


def _history_roots(archive: Path) -> list[str]:
    roots: list[str] = []
    for status in reversed(read_ingest_history(archive).statuses):  # Oldest first.
        roots.extend(root for root in status.source_roots if "://" not in root)
    return roots


def _local_sources(archive: Path) -> list[LocalSource]:
    """Saved local sources, plus roots of earlier imports not yet saved (enabled)."""
    saved = list(load_archive_config(archive).local_sources)
    known = {source.id for source in saved}
    for root in _history_roots(archive):
        source_id = local_source_id(root)
        if source_id not in known:
            known.add(source_id)
            saved.append(LocalSource(id=source_id, path=os.path.abspath(root)))
    return saved


def archive_sources(archive: Path) -> list[SourceEntry]:
    entries = []
    for source in _local_sources(archive):
        path = Path(source.path)
        kind: SourceKind = "file" if path.is_file() else "directory"
        entries.append(SourceEntry(id=source.id, kind=kind, label=path.name or str(path), detail=str(path),
                                   enabled=source.enabled, available=path.exists()))
    for account in load_archive_config(archive).imap_sources:
        entries.append(SourceEntry(
            id=account.id, kind="imap", label=account.username, detail=f"{account.server}:{account.port}",
            enabled=account.enabled, available=True, server=account.server, port=account.port,
            username=account.username, tls=account.tls))
    return entries


def record_local_sources(archive: Path, roots: list[str]) -> None:
    """Remember the local roots of an import (idempotent; keeps each source's checkbox)."""
    local = [os.path.abspath(root) for root in roots if "://" not in root]
    if not local:
        return
    config = load_archive_config(archive)
    known = {source.id for source in config.local_sources}
    added = [LocalSource(id=local_source_id(root), path=root) for root in local if local_source_id(root) not in known]
    if added:
        config.local_sources = [*config.local_sources, *added]
        save_archive_config(archive, config)


def set_source_enabled(archive: Path, source_id: str, enabled: bool) -> None:
    config = load_archive_config(archive)
    if any(account.id == source_id for account in config.imap_sources):
        config.imap_sources = [account.model_copy(update={"enabled": enabled}) if account.id == source_id else account
                               for account in config.imap_sources]
    else:
        local = {source.id: source for source in _local_sources(archive)}
        if source_id not in local:
            raise ValueError("unknown source")
        saved = {source.id for source in config.local_sources}
        updated = [source.model_copy(update={"enabled": enabled}) if source.id == source_id else source
                   for source in config.local_sources]
        if source_id not in saved:
            updated.append(local[source_id].model_copy(update={"enabled": enabled}))
        config.local_sources = updated
    save_archive_config(archive, config)


def update_imap_account(archive: Path, source_id: str, server: str, port: int, username: str,
                        tls: Literal["implicit", "starttls"]) -> tuple[ImapAccount, ImapAccount]:
    """Replace an IMAP account's connection settings, keeping its identity and checkbox."""
    config = load_archive_config(archive)
    previous = next((account for account in config.imap_sources if account.id == source_id), None)
    if previous is None:
        raise ValueError("unknown IMAP source")
    updated = ImapAccount(id=previous.id, server=server.strip(), port=port, username=username.strip(), tls=tls,
                          enabled=previous.enabled)
    config.imap_sources = [updated if account.id == source_id else account for account in config.imap_sources]
    save_archive_config(archive, config)
    return previous, updated


def rescan_roots(archive: Path, workers: int) -> list[str]:
    """Import roots for Rescan Imports…: every enabled source that is reachable."""
    roots = [source.path for source in _local_sources(archive) if source.enabled and Path(source.path).exists()]
    roots.extend(imap_locator(archive, account, workers) for account in load_archive_config(archive).imap_sources
                 if account.enabled)
    return roots
