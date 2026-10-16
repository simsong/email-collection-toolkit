# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

# Purpose: Adapt generic IMAP and Gmail All Mail to the shared source contract.
# How it works:
# Freeze mailbox UID ranges, give each worker a session and emit bytes or known-message metadata
# observations.
# Fundamental dependents:
# - __main__: source workers consume this provider through the plugin contract.
# - imap_cli: synchronization depends on its account locator and discovery semantics.

"""Read-only generic IMAP and Gmail All Mail source adapter."""
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import closing
from math import ceil
from pathlib import Path
import sqlite3
import ssl
from urllib.parse import parse_qs, quote, urlencode, urlparse

from pydantic import BaseModel, Field

from .archive_config import load_archive_config
from .imap_credentials import password_for
from .imap_models import ImapAccount, ImapMailbox
from .imap_protocol import ImapSession, UID_BATCH_SIZE
from .imap_state import known_observation
from .plugin_api import (
    IntegrityDecision, IntegrityEvidence, MailContainer, MailObject, PluginCapabilities,
    ProgressEvent, SkippedInput, SourceIntegrityControls, SourceObservation, SourcePlugin,
    SourceReference, SourceSpec,
)
from .sqlite_paths import sqlite_uri

UID_SEARCH_WINDOW = 65536


class ImapWork(BaseModel):
    archive: Path
    account: ImapAccount
    mailbox: ImapMailbox
    gmail: bool
    first_uid: int = Field(ge=1)
    last_uid: int = Field(ge=0)


def imap_locator(archive: Path, account: ImapAccount, workers: int) -> str:
    return f"imap://{account.id}?" + urlencode({"archive": str(archive.resolve()), "workers": workers})


class ImapIntegrityControls(SourceIntegrityControls):
    control_id = "imap-uid-epoch-v1"

    def plan(self, container: MailContainer, prior: tuple[IntegrityEvidence, ...]
             ) -> Iterator[IntegrityDecision | IntegrityEvidence | ProgressEvent]:
        work = ImapWork.model_validate_json(container.plugin_data_json)
        if any(item.value != str(work.mailbox.uid_validity) for item in prior if item.evidence_kind == "version-token"):
            yield ProgressEvent(phase="UIDVALIDITY changed; reconciling committed identities", work_id=container.work_id)
        yield IntegrityDecision(action="read", reason="enumerate UIDs and retain committed per-message progress")

    def complete(self, container: MailContainer, planned: tuple[IntegrityEvidence, ...]
                 ) -> Iterator[IntegrityEvidence | ProgressEvent]:
        del planned
        work = ImapWork.model_validate_json(container.plugin_data_json)
        yield IntegrityEvidence(control_id=self.control_id, subject_id=container.source.native_id,
                                evidence_kind="version-token", value=str(work.mailbox.uid_validity))


class ImapSourcePlugin(SourcePlugin):
    kind = "imap"
    capabilities = PluginCapabilities(resumable=False, stable_inventory=False, network_access=True)
    integrity_controls = ImapIntegrityControls()

    def __init__(self, *, credentials: Callable[[ImapAccount], str] = password_for,
                 context: ssl.SSLContext | None = None) -> None:
        self.credentials = credentials
        self.context = context

    def recognizes(self, source: SourceSpec) -> bool:
        return source.kind == "imap" or source.locator.startswith("imap://")

    def discover(self, source: SourceSpec) -> Iterator[MailContainer | ProgressEvent | SkippedInput]:
        parsed = urlparse(source.locator)
        query = parse_qs(parsed.query, strict_parsing=True)
        archive_values = query.get("archive", [])
        worker_values = query.get("workers", ["8"])
        if parsed.scheme != "imap" or len(archive_values) != 1 or len(worker_values) != 1:
            raise ValueError("IMAP source requires an archive-scoped locator")
        archive = Path(archive_values[0]).resolve()
        workers = int(worker_values[0])
        if workers < 1:
            raise ValueError("IMAP workers must be positive")
        matches = [account for account in load_archive_config(archive).imap_sources if account.id == parsed.netloc]
        if len(matches) != 1:
            raise ValueError("IMAP source ID is absent or ambiguous in config.yaml")
        account = matches[0]
        if not account.enabled:
            return
        with ImapSession(account, self.credentials(account), self.context) as session:
            for listed in session.mailboxes():
                mailbox = session.select(listed)
                # Escaped display components satisfy the provider-neutral hierarchy contract.
                hierarchy = tuple(quote(part, safe="").replace(".", "%2E")
                                  for part in mailbox.name.split(mailbox.delimiter or "\x00") if part)
                source_ref = SourceReference(plugin_kind="imap", source_id=account.id,
                    native_id=mailbox.name, display_name=f"{account.username}: {mailbox.name}",
                    hierarchy=hierarchy, provenance_json=account.model_dump_json())
                length = max(1, ceil(max(mailbox.uid_next - 1, 1) / workers))
                for partition, first in enumerate(range(1, max(mailbox.uid_next, 2), length)):
                    last = min(first + length - 1, mailbox.uid_next - 1)
                    work = ImapWork(archive=archive, account=account, mailbox=mailbox, gmail=session.gmail,
                                    first_uid=first, last_uid=last)
                    native_id = f"{quote(mailbox.name, safe='')}?partition={partition}"
                    yield MailContainer(work_id=f"{account.id}:{native_id}",
                        source=source_ref.model_copy(update={"native_id": native_id}),
                        concurrency_key=account.id, plugin_data_json=work.model_dump_json())

    def messages(self, container: MailContainer, resume_cursor: str | None
                 ) -> Iterator[MailObject | SourceObservation | ProgressEvent]:
        del resume_cursor
        work = ImapWork.model_validate_json(container.plugin_data_json)
        with ImapSession(work.account, self.credentials(work.account), self.context) as session:
            selected = session.select(work.mailbox)
            if selected.uid_validity != work.mailbox.uid_validity or session.gmail != work.gmail:
                raise RuntimeError("IMAP mailbox epoch or capabilities changed; retry synchronization")
            with closing(sqlite3.connect(sqlite_uri(work.archive / "archive.sqlite3"), uri=True)) as database:
                for start in range(work.first_uid, work.last_uid + 1, UID_SEARCH_WINDOW):
                    uids = session.uids(start, min(start + UID_SEARCH_WINDOW - 1, work.last_uid))
                    for offset in range(0, len(uids), UID_BATCH_SIZE):
                        for metadata in session.metadata(selected, uids[offset:offset + UID_BATCH_SIZE]):
                            cursor = f"{metadata.uid_validity}:{metadata.uid}"
                            payload = metadata.model_dump_json()
                            if known_observation(database, container.source, metadata) is not None:
                                yield SourceObservation(work_id=container.work_id, source=container.source,
                                    cursor=cursor, provider_metadata_json=payload)
                            else:
                                yield MailObject(work_id=container.work_id, source=container.source, cursor=cursor,
                                    raw=session.body(metadata), provider_metadata_json=payload,
                                    source_date_utc=metadata.internal_date)
