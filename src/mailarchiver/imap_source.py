# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Read-only generic IMAP and Gmail All Mail source adapter.

Purpose: Adapt generic IMAP and Gmail All Mail to the shared source contract.

How it works:
Freeze UID ranges, prioritize missing checkpoints and emit LF-normalized message bytes with
wire hashes, then refresh saved-message metadata without another body transfer.
Replace interrupted sessions with bounded backoff while retaining frozen UID plans.

Fundamental dependents:

- ``__main__``: source workers consume this provider through the plugin contract.
- ``imap_cli``: synchronization depends on its account locator and discovery semantics.
"""
from __future__ import annotations

from collections.abc import Callable, Generator, Iterator
from concurrent.futures import Future
from hashlib import sha256
from contextlib import ExitStack, closing
from math import ceil
from pathlib import Path
import sqlite3
import ssl
from urllib.parse import parse_qs, quote, urlencode, urlparse

from pydantic import BaseModel, Field

from .archive_config import load_archive_config
from .change_signal import ActivityCallback, ChangeEvent, await_activity
from .imap_credentials import password_for
from .imap_models import ImapAccount, ImapDownload, ImapMailbox, ImapMessageMetadata
from .imap_preferences import DEFAULT_IMAP_CONNECTIONS
from .imap_protocol import ImapPaused, ImapServerBusy, ImapSession, UID_BATCH_SIZE
from .imap_state import known_observation
from .plugin_api import (
    IntegrityDecision, IntegrityEvidence, MailContainer, MailObject, PluginCapabilities,
    PluginContext, ProgressEvent, SkippedInput, SourceIntegrityControls, SourceObservation, SourcePlugin,
    SourceReference, SourceSpec,
)
from .sqlite_paths import sqlite_uri

UID_SEARCH_WINDOW = 65536
IMAP_RECOVERY_ATTEMPTS = 3
IMAP_RECOVERY_PHASE = "IMAP connection interrupted;"
IMAP_RETRY_INITIAL = 1.0
IMAP_RETRY_MAXIMUM = 60.0
IMAP_BUSY_PHASE = "IMAP server busy;"


def _backoff_cancelled(cancellation: ChangeEvent, delay: float) -> bool:
    """Wake for native console signals as well as worker/GUI cancellation, without polling."""
    result: Future[bool] = Future()

    def check() -> None:
        if cancellation.is_set() and not result.done():
            result.set_result(True)

    try:
        return await_activity(result, ActivityCallback(check, (cancellation.changed,)), delay)
    except TimeoutError:
        return False


def normalize_download(raw: bytes, metadata: ImapMessageMetadata) -> tuple[bytes, ImapMessageMetadata]:
    """Record verified wire evidence, then apply only the IMAP CRLF-to-LF policy."""
    evidence = ImapDownload(source_sha256=sha256(raw).hexdigest(), source_byte_length=len(raw),
                            crlf_pairs=raw.count(b"\r\n"))
    return raw.replace(b"\r\n", b"\n"), metadata.model_copy(update={"download": evidence})


class ImapWork(BaseModel):
    archive: Path
    account: ImapAccount
    mailbox: ImapMailbox
    gmail: bool
    utf8: bool | None = None
    first_uid: int = Field(ge=1)
    last_uid: int = Field(ge=0)


def imap_locator(archive: Path, account: ImapAccount, workers: int) -> str:
    return f"imap://{account.id}?" + urlencode({"archive": str(archive.resolve()), "workers": workers})


def _uid_batches(database: sqlite3.Connection, session: ImapSession, work: ImapWork,
                 refresh: bool) -> Iterator[list[int]]:
    """Find missing exact checkpoints first; a maximum UID would skip interrupted holes."""
    for start in range(work.first_uid, work.last_uid + 1, UID_SEARCH_WINDOW):
        end = min(start + UID_SEARCH_WINDOW - 1, work.last_uid)
        prior = {row[0] for row in database.execute("SELECT uid FROM prior_imap_uids WHERE uid BETWEEN ? AND ?", (start, end))}
        delivered = {row[0] for row in database.execute("SELECT uid FROM delivered_imap_uids WHERE uid BETWEEN ? AND ?", (start, end))}
        uids = [uid for uid in session.uids(start, end) if (uid in prior) == refresh and uid not in delivered]
        for offset in range(0, len(uids), UID_BATCH_SIZE):
            yield uids[offset:offset + UID_BATCH_SIZE]


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
    # Limit acquisition connections independently of the shared processing worker pool.
    capabilities = PluginCapabilities(resumable=False, stable_inventory=False, network_access=True,
                                      max_concurrency=DEFAULT_IMAP_CONNECTIONS)
    integrity_controls = ImapIntegrityControls()

    def __init__(self, services: PluginContext | None = None, *, credentials: Callable[[ImapAccount], str] = password_for,
                 context: ssl.SSLContext | None = None) -> None:
        self.credentials = credentials
        self.context = context
        if services is not None:
            self.capabilities = self.capabilities.model_copy(update={"max_concurrency": services.imap_connections})
        self.cancellation = services.cancellation if services and services.cancellation is not None else ChangeEvent()

    def _connect(self, account: ImapAccount, connections: ExitStack, work_id: str
                 ) -> Generator[ProgressEvent, None, ImapSession | None]:
        """Retry only unopened sessions; never replay a body or discard committed progress."""
        delay = IMAP_RETRY_INITIAL
        while not self.cancellation.is_set():
            try:
                return connections.enter_context(ImapSession(account, self.credentials(account), self.context))
            except ImapServerBusy as error:
                detail = error.detail
            yield ProgressEvent(work_id=work_id,
                                phase=f"{IMAP_BUSY_PHASE} retrying in {delay:g}s: {detail}")
            # This user-requested retry deadline also wakes immediately for Suspend/Quit.
            if _backoff_cancelled(self.cancellation, delay):
                return None
            delay = min(delay * 2, IMAP_RETRY_MAXIMUM)
        return None

    def _recover(self, work_id: str, attempt: int, error: ImapPaused) -> Generator[ProgressEvent, None, bool]:
        if attempt > IMAP_RECOVERY_ATTEMPTS:
            raise error
        delay = min(IMAP_RETRY_INITIAL * 2 ** (attempt - 1), IMAP_RETRY_MAXIMUM)
        yield ProgressEvent(work_id=work_id,
            phase=f"{IMAP_RECOVERY_PHASE} reconnecting in {delay:g}s ({attempt}/{IMAP_RECOVERY_ATTEMPTS})")
        return not _backoff_cancelled(self.cancellation, delay)

    def recognizes(self, source: SourceSpec) -> bool:
        return source.kind == "imap" or source.locator.startswith("imap://")

    def discover(self, source: SourceSpec) -> Generator[MailContainer | ProgressEvent | SkippedInput, None, None]:
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
        frozen: list[ImapMailbox] = []
        emitted: set[str] = set()
        mode: tuple[bool, bool] | None = None
        failures = 0
        while not self.cancellation.is_set():
            try:
                with ExitStack() as connections:
                    session = yield from self._connect(account, connections, account.id)
                    if session is None:
                        return
                    if mode is not None and mode != (session.gmail, session.utf8):
                        raise RuntimeError("IMAP capabilities changed during discovery; restart synchronization")
                    mode = (session.gmail, session.utf8)
                    for listed in session.mailboxes():
                        mailbox = session.select(listed)
                        previous = next((item for item in frozen if item.name == mailbox.name), None)
                        if previous is not None:
                            if mailbox.uid_validity != previous.uid_validity:
                                raise RuntimeError("IMAP UIDVALIDITY changed during discovery; restart synchronization")
                            mailbox = previous
                        else:
                            frozen.append(mailbox)
                        # Escaped display components satisfy the provider-neutral hierarchy contract.
                        hierarchy = tuple(quote(part, safe="").replace(".", "%2E")
                                          for part in mailbox.name.split(mailbox.delimiter or "\x00") if part)
                        source_ref = SourceReference(plugin_kind="imap", source_id=account.id,
                            native_id=mailbox.name, display_name=f"{account.username}: {mailbox.name}",
                            hierarchy=hierarchy, provenance_json=account.model_dump_json())
                        length = max(1, ceil(max(mailbox.uid_next - 1, 1) / workers))
                        for partition, first in enumerate(range(1, max(mailbox.uid_next, 2), length)):
                            last = min(first + length - 1, mailbox.uid_next - 1)
                            work = ImapWork(archive=archive, account=account, mailbox=mailbox, gmail=session.gmail, utf8=session.utf8,
                                            first_uid=first, last_uid=last)
                            native_id = f"{quote(mailbox.name, safe='')}?partition={partition}"
                            container = MailContainer(work_id=f"{account.id}:{native_id}",
                                source=source_ref.model_copy(update={"native_id": native_id}),
                                concurrency_key=account.id, plugin_data_json=work.model_dump_json())
                            if container.work_id not in emitted:
                                emitted.add(container.work_id)
                                yield container
                return
            except ImapPaused as error:
                if self.cancellation.is_set():
                    return
                failures += 1
                if not (yield from self._recover(account.id, failures, error)):
                    return

    def messages(self, container: MailContainer, resume_cursor: str | None
                 ) -> Generator[MailObject | SourceObservation | ProgressEvent, None, None]:
        del resume_cursor
        work = ImapWork.model_validate_json(container.plugin_data_json)
        with closing(sqlite3.connect(sqlite_uri(work.archive / "archive.sqlite3"), uri=True)) as database:
            # Freeze prior membership once. Track delivered objects separately so a
            # fresh transport never replays bodies already handed to publication.
            database.execute("CREATE TEMP TABLE prior_imap_uids(uid INTEGER PRIMARY KEY)")
            database.execute("CREATE TEMP TABLE delivered_imap_uids(uid INTEGER PRIMARY KEY)")
            prior_count = database.execute("INSERT INTO prior_imap_uids SELECT uid FROM imap_seen "
                "WHERE source_id=? AND mailbox=? AND uid_validity=? AND uid BETWEEN ? AND ?",
                (work.account.id, work.mailbox.name, work.mailbox.uid_validity, work.first_uid, work.last_uid)).rowcount
            database.commit()
            failures = 0
            while not self.cancellation.is_set():
                try:
                    with ExitStack() as connections:
                        session = yield from self._connect(work.account, connections, container.work_id)
                        if session is None:
                            return
                        selected = session.select(work.mailbox)
                        if (selected.uid_validity != work.mailbox.uid_validity or session.gmail != work.gmail
                                or work.utf8 is not None and session.utf8 != work.utf8):
                            raise RuntimeError("IMAP mailbox epoch or capabilities changed; retry synchronization")
                        for refresh in ((False, True) if prior_count else (False,)):
                            phase = "refreshing saved IMAP metadata" if refresh else "finding unarchived IMAP messages"
                            yield ProgressEvent(work_id=container.work_id, phase=phase)
                            completed = 0
                            for batch in _uid_batches(database, session, work, refresh):
                                for metadata in session.metadata(selected, batch):
                                    cursor = f"{metadata.uid_validity}:{metadata.uid}"
                                    payload = metadata.model_dump_json()
                                    item: MailObject | SourceObservation
                                    if known_observation(database, container.source, metadata) is not None:
                                        item = SourceObservation(work_id=container.work_id, source=container.source,
                                            cursor=cursor, provider_metadata_json=payload)
                                    else:
                                        normalized, metadata = normalize_download(session.body(metadata), metadata)
                                        item = MailObject(work_id=container.work_id, source=container.source, cursor=cursor,
                                            raw=normalized, provider_metadata_json=metadata.model_dump_json(),
                                            source_date_utc=metadata.internal_date)
                                    database.execute("INSERT INTO delivered_imap_uids VALUES(?)", (metadata.uid,))
                                    database.commit()
                                    yield item
                                    failures = 0
                                if refresh:
                                    completed += len(batch)
                                    yield ProgressEvent(work_id=container.work_id, phase=phase,
                                                        completed=completed, unit="messages")
                    return
                except ImapPaused as error:
                    if self.cancellation.is_set():
                        return
                    failures += 1
                    if not (yield from self._recover(container.work_id, failures, error)):
                        return
