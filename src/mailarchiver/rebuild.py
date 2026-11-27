# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Rebuild databases: rebuild the catalog's derived message fields and the search index.

Purpose: `ect rebuild` and File → Rebuild Databases… re-read every stored message
from the canonical MBOX files and rebuild everything that is derived from message bytes,
without reimporting and without changing the mailboxes.

How it works: the live catalog is copied (SQLite backup) to `archive.sqlite3.new.<id>`;
each message's Message-ID, sender, recipients, subject, date and parse defects are
re-derived in that copy, keeping message numbers, locations and all provenance (sources,
observations, import runs, antivirus results), which the mailboxes do not contain. A
fresh `search.sqlite3.new.<id>` is built in the same pass. Only when both are complete
are the live files renamed to `.old` and the new files moved into place. Stopping or
failing earlier deletes the `.new` files and leaves the live databases untouched.
Progress is measured in bytes known before reading.
"""

from __future__ import annotations

import logging
import math
import re
import multiprocessing
import os
import shutil
import signal
import sqlite3
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from concurrent.futures import Future, ProcessPoolExecutor
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from uuid import uuid4

from pydantic import BaseModel, Field

from .bagit import BAG_INFO, CHECKPOINT_STAMP_MARGIN_NS
from .catalog import address_pk, create_catalog, create_search
from .ingest_status import (IngestCounts, IngestState, IngestStatus, IngestStatusFile, IngestWorkerStatus, YearProgress,
                            new_status_id)
from .layout import mbox_directory, mbox_path
from .mbox import MboxLocation, read_verified_location
from .message import MetadataDefect, ParsedMessage, display_entity, parse_message
from .search import QUARANTINE_MAILBOX, SEARCH_CATEGORIES, PreparedSearchMessage, prepare_search_message
from .search import write_prepared_search_message
from .standalone_verify import PreservingMbox
from .sqlite_paths import sqlite_uri
from .storage_sync import sync_directory
from .worker_preferences import half_cpus
from .writer_lock import WriterLease

CATALOG = "archive.sqlite3"
SEARCH = "search.sqlite3"
PROCESSING = "processing.sqlite3"
OLD_SUFFIX = ".old"
NEW_INFIX = ".new."
REBUILD_DEFAULT_WORKERS = half_cpus()
# Per-worker, per-year and name counts refresh this often; the progress bar keeps its
# one-second cadence. Counting names scans the new address table, so keep it rare.
DETAIL_REFRESH_SECONDS = 10.0
MAX_IN_FLIGHT_BYTES = 256 * 1024 * 1024
FREE_SPACE_MARGIN = 1.2
# Defect fields that parse_message can emit; others (antivirus, scanning, search) are kept.
PARSE_DEFECT_FIELDS = frozenset({
    "Message-ID", "From", "Sender", "to", "cc", "bcc", "Date", "Received", "Subject",
    "X-Apple-Auto-Saved", "body date", "rebuild", "reprocess",
})


def formatted_bytes(value: int) -> str:
    amount = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if amount < 1024 or unit == "TiB":
            return f"{int(amount)} B" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024
    raise AssertionError("unreachable")


def formatted_duration(seconds: float) -> str:
    remaining = max(0, math.ceil(seconds))
    hours, remaining = divmod(remaining, 3600)
    minutes, seconds = divmod(remaining, 60)
    fields = ([f"{hours}h"] if hours else []) + ([f"{minutes}m"] if hours or minutes else []) + [f"{seconds}s"]
    return " ".join(fields)


class RebuildProgress(BaseModel):
    """One observable, byte-measured phase of rebuilding or index rebuilding.

    Totals come from catalog record lengths and file sizes, which are known before
    any message is read, so percent and ETA track bytes processed.
    """

    phase: str
    total_bytes: int
    started_monotonic: float = Field(default_factory=time.monotonic)
    completed_bytes: int = 0

    @property
    def percent(self) -> float:
        return 100.0 if self.total_bytes == 0 else 100 * min(self.completed_bytes, self.total_bytes) / self.total_bytes

    def eta(self, now: float) -> str:
        completed = min(self.completed_bytes, self.total_bytes)
        if completed >= self.total_bytes:
            return "0s"
        if completed == 0:
            return "calculating"
        elapsed = max(now - self.started_monotonic, 0.001)
        return formatted_duration((self.total_bytes - completed) * elapsed / completed)


def progress_line(progress: RebuildProgress, now: float, width: int = 24) -> str:
    """Render bounded byte progress without relying on terminal controls."""
    completed = min(progress.completed_bytes, progress.total_bytes)
    elapsed = max(now - progress.started_monotonic, 0.001)
    filled = round(width * progress.percent / 100)
    bar = "#" * filled + "-" * (width - filled)
    return (
        f"{progress.phase}: [{bar}] {progress.percent:5.1f}% {formatted_bytes(completed)} of "
        f"{formatted_bytes(progress.total_bytes)}  {formatted_bytes(int(completed / elapsed))}/s  ETA {progress.eta(now)}"
    )


class ByteProgressReporter:
    """Report byte progress to a terminal, or to a callback such as the GUI."""

    def __init__(self, phase: str, total_bytes: int,
                 observer: Callable[[RebuildProgress], None] | None = None,
                 listener: Callable[[RebuildProgress], None] | None = None) -> None:
        self.progress = RebuildProgress(phase=phase, total_bytes=total_bytes)
        self.observer = observer
        self.listener = listener  # Always notified (the Activity status file), besides observer or terminal.
        self.tty = sys.stderr.isatty()
        self.last_tenth: int | None = None

    def display(self, *, force: bool = False) -> None:
        tenth = int(self.progress.percent * 10)
        if not force and self.last_tenth == tenth:
            return
        self.last_tenth = tenth
        if self.listener is not None:
            self.listener(self.progress.model_copy())
        if self.observer is not None:
            self.observer(self.progress.model_copy())
            return
        if not force and not self.tty and tenth % 10:
            return  # Logs get one line per whole percent; terminals redraw per tenth.
        line = progress_line(self.progress, time.monotonic())
        if self.tty:
            sys.stderr.write(f"\r\x1b[2K{line}")
        else:
            print(line, file=sys.stderr)
        sys.stderr.flush()

    def advance(self, byte_count: int) -> None:
        self.progress.completed_bytes += byte_count
        self.display()

    def finish(self) -> None:
        self.progress.completed_bytes = self.progress.total_bytes
        self.display(force=True)
        if self.tty and self.observer is None:
            sys.stderr.write("\n")
            sys.stderr.flush()


class RebuildInterrupted(KeyboardInterrupt):
    """Report whether a stop arrived before or after the new databases were published."""

    def __init__(self, published: bool) -> None:
        super().__init__()
        self.published = published


class UninterruptiblePublication:
    """Ignore Ctrl-C while live databases are being renamed."""

    def __enter__(self) -> None:
        self.previous_handler = (
            signal.signal(signal.SIGINT, signal.SIG_IGN)
            if threading.current_thread() is threading.main_thread()
            else None
        )

    def __exit__(self, exception_type: type[BaseException] | None, _value: BaseException | None,
                 _traceback: TracebackType | None) -> None:
        if self.previous_handler is not None:
            signal.signal(signal.SIGINT, self.previous_handler)


MAILBOX_YEAR = re.compile(r"^(\d{4})-")


def mailbox_year(filename: str) -> int | None:
    """The year of a canonical MBOX named ``YYYY-Mailbox1.mbox``; None for others (e.g. INFECTED1)."""
    match = MAILBOX_YEAR.match(filename)
    return int(match.group(1)) if match else None


class WorkerTally(BaseModel):
    """What one worker process has rebuilt, seen from the results the parent applied."""

    worker: int
    messages: int = 0
    bytes_done: int = 0
    last_mailbox: str | None = None


class RebuildDetail:
    """Per-worker, per-year and current-mailbox counts for the Activity window.

    Work is handed out in canonical MBOX order (``YYYY-Mailbox1.mbox`` sorts by year), and
    results are applied in that order, so the last applied mailbox is the rebuild's
    position. Workers are numbered by first result; a result names its worker's process.
    """

    def __init__(self) -> None:
        self.workers: dict[int, WorkerTally] = {}
        self.years: dict[int, int] = {}
        self.mailbox: str | None = None
        self.year: int | None = None
        self.names_learned: int | None = None
        self.addresses_known: int | None = None

    def record(self, worker_pid: int, filename: str, byte_length: int) -> None:
        tally = self.workers.get(worker_pid)
        if tally is None:
            tally = self.workers[worker_pid] = WorkerTally(worker=len(self.workers) + 1)
        tally.messages += 1
        tally.bytes_done += byte_length
        tally.last_mailbox = filename
        self.mailbox = filename
        year = mailbox_year(filename)
        if year is not None:
            self.year = year
            self.years[year] = self.years.get(year, 0) + 1

    def count_names(self, search: sqlite3.Connection) -> None:
        """Count addresses, and those with a display name, in the index being built."""
        row = search.execute("SELECT count(*), count(*) FILTER (WHERE display_name != '') FROM address_suggestions").fetchone()
        assert row is not None
        self.addresses_known, self.names_learned = int(row[0]), int(row[1])


class RebuildActivity:
    """Show one rebuild run in the Activity window, as an ingest-status file of kind "rebuild".

    Progress is written at most once a second and on completion; per-worker, per-year
    and name counts are refreshed every DETAIL_REFRESH_SECONDS. A status that cannot be
    written never stops the rebuild itself.
    """

    def __init__(self, archive: Path, workers: int, application_version: str) -> None:
        self.archive, self.workers, self.version = archive, workers, application_version or None
        self.started_at = datetime.now(UTC)
        self.started = self.phase_started = time.monotonic()
        self.messages = 0
        self.progress = RebuildProgress(phase="Preparing to rebuild databases", total_bytes=0)
        self.last_write = 0.0
        self.detail = RebuildDetail()
        self.search: sqlite3.Connection | None = None
        self.last_detail = -math.inf
        self.workers_status: list[IngestWorkerStatus] = []
        self.years_status: list[YearProgress] = []
        self.status_id = new_status_id(self.started_at, 0, os.getpid())
        self.file: IngestStatusFile | None = None
        try:
            self.file = IngestStatusFile(archive, self.status_id)
        except OSError:
            logging.getLogger(__name__).warning("Could not create the rebuild activity status", exc_info=True)
        self._write("running", force=True)

    def update(self, progress: RebuildProgress) -> None:
        new_phase = progress.phase != self.progress.phase
        if new_phase:
            self.phase_started = time.monotonic()
        self.progress = progress
        self._write("running", force=new_phase or progress.completed_bytes >= progress.total_bytes)

    def finish(self, state: IngestState, phase: str, detail: str | None = None) -> None:
        self.progress = self.progress.model_copy(update={"phase": phase})
        self.last_detail = -math.inf  # Final counts, not the last periodic sample.
        self._write(state, failure=detail, force=True)
        self.search = None

    def _refresh_detail(self, now: float, running: bool) -> None:
        if now - self.last_detail < DETAIL_REFRESH_SECONDS:
            return
        self.last_detail = now
        if self.search is not None:
            try:
                self.detail.count_names(self.search)
            except sqlite3.Error:
                logging.getLogger(__name__).warning("Could not count names for the rebuild activity", exc_info=True)
        detail = self.detail
        self.workers_status = [
            IngestWorkerStatus(worker=tally.worker, phase="rebuilding" if running else "idle", path=None,
                               last_path=tally.last_mailbox, bytes_done=tally.bytes_done,
                               messages_processed=tally.messages)
            for tally in sorted(detail.workers.values(), key=lambda tally: tally.worker)]
        self.years_status = [YearProgress(year=year, messages=count) for year, count in sorted(detail.years.items())]

    def _write(self, state: IngestState, *, failure: str | None = None, force: bool = False) -> None:
        now = time.monotonic()
        if self.file is None or (not force and now - self.last_write < 1.0):
            return
        self.last_write = now
        elapsed = max(now - self.started, 0.0)
        running = state == "running"
        progress = self.progress
        self._refresh_detail(now, running)
        detail = self.detail
        try:
            self.file.write(IngestStatus(
                status_id=self.status_id, archive=str(self.archive), run_pk=0, process_id=os.getpid(),
                application_version=self.version, source_roots=[], started_at=self.started_at,
                updated_at=datetime.now(UTC), completed_at=None if running else datetime.now(UTC), state=state,
                phase=progress.phase, elapsed_seconds=elapsed, phase_elapsed_seconds=max(now - self.phase_started, 0.0),
                processed_messages=self.messages, message_rate=self.messages / max(elapsed, 0.001),
                files_processed=0, files_total=0, bytes_processed=progress.completed_bytes,
                bytes_total=progress.total_bytes,
                percent=100.0 if state == "completed" else progress.percent,
                eta=progress.eta(now) if running else "0s", active_workers=self.workers if running else 0,
                peak_workers=self.workers, configured_workers=self.workers,
                workers=self.workers_status, counts=IngestCounts(), years=self.years_status,
                current_year=detail.year, current_year_messages=detail.years.get(detail.year, 0) if detail.year else 0,
                current_mailbox=detail.mailbox, names_learned=detail.names_learned,
                addresses_known=detail.addresses_known, failure_detail=failure, activity_kind="rebuild"))
        except (OSError, ValueError):
            logging.getLogger(__name__).warning("Could not update the rebuild activity status", exc_info=True)


class RebuildWork(BaseModel):
    """One verified-MBOX read assigned to a worker, with the catalog values it may keep."""

    message_pk: int
    sha256: str
    filename: str
    location: MboxLocation
    date_utc: str
    date_source: str
    searchable: bool


class RebuiltMessage(BaseModel):
    """A worker result consumed, in catalog order, by the sole SQLite writer."""

    message_pk: int
    byte_length: int
    date_utc: str
    parsed: ParsedMessage | None
    error: str | None = None
    indexed: PreparedSearchMessage | None = None
    worker_pid: int = 0


class RebuildResult(BaseModel):
    messages: int
    changed: int
    failures: int
    processing_database: bool


def rebuild_message(archive: Path, index_attachments: bool, work: RebuildWork) -> RebuiltMessage:
    """Verify one stored message and re-derive its catalog fields and search content."""
    raw = read_verified_location(mbox_path(archive, work.filename), work.location, work.sha256)
    entity = display_entity(raw)
    parsed: ParsedMessage | None = None
    error = None
    try:
        parsed = parse_message(entity, None, datetime.fromisoformat(work.date_utc))
    except (ValueError, RecursionError, LookupError) as failure:
        error = f"{type(failure).__name__}: {failure}"
    if parsed is not None:
        update: dict[str, object] = {"sha256": work.sha256}
        if parsed.message_id == parsed.sha256:  # No Message-ID: identity is the stored bytes' hash.
            update["message_id"] = work.sha256
        if parsed.date_source == "previous-message":
            # The bytes carry no date; keep the date recorded at import (from context).
            update["date_utc"], update["date_source"] = work.date_utc, work.date_source
        parsed = parsed.model_copy(update=update)
    indexed = prepare_search_message(raw, index_attachments, sha256=work.sha256) if work.searchable else None
    return RebuiltMessage(message_pk=work.message_pk, byte_length=work.location.byte_length,
                              date_utc=parsed.date_utc if parsed is not None else work.date_utc,
                              parsed=parsed, error=error, indexed=indexed, worker_pid=os.getpid())


def _ignore_interrupts() -> None:
    """Worker initializer: Ctrl-C reaches the whole terminal process group, but only the
    parent decides how to stop (it cancels queued work and shuts the pool down)."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)


def worker_pool(workers: int) -> ProcessPoolExecutor:
    """Spawned rebuild workers that ignore Ctrl-C and leave stopping to the parent."""
    return ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn"),
                               initializer=_ignore_interrupts)


def _work_items(source: sqlite3.Connection) -> Iterator[RebuildWork]:
    """Stream work from the unchanged live catalog, never from the copy being rewritten."""
    rows = source.execute(
        "SELECT m.message_pk, m.sha256, g.filename, l.byte_offset, l.byte_length, m.date_utc, m.date_source, "
        "m.category FROM messages m JOIN locations l USING (message_pk) JOIN mbox_generations g USING (generation_pk) "
        "ORDER BY g.filename, l.byte_offset"
    )
    for message_pk, digest, filename, offset, length, date_utc, date_source, category in rows:
        searchable = category in SEARCH_CATEGORIES and not QUARANTINE_MAILBOX.fullmatch(filename)
        if category in SEARCH_CATEGORIES and QUARANTINE_MAILBOX.fullmatch(filename):
            raise RuntimeError(f"searchable catalog message is stored in quarantine MBOX: {filename}")
        yield RebuildWork(message_pk=message_pk, sha256=digest, filename=filename,
                            location=MboxLocation(byte_offset=offset, byte_length=length),
                            date_utc=date_utc, date_source=date_source, searchable=searchable)


def _apply(catalog: sqlite3.Connection, result: RebuiltMessage) -> bool:
    """Write one message's re-derived fields; return whether any catalog field changed."""
    message_pk, parsed = result.message_pk, result.parsed
    if parsed is None:
        catalog.execute("INSERT OR IGNORE INTO metadata_defects(message_pk, field, detail) VALUES (?, 'rebuild', ?)",
                        (message_pk, f"could not re-derive metadata; kept previous values: {result.error}"))
        return False
    before = catalog.execute(
        "SELECT message_id_normalized, sender_address_pk, subject, date_utc, date_source FROM messages WHERE message_pk = ?",
        (message_pk,)).fetchone()
    old_recipients = catalog.execute(
        "SELECT address_pk, role FROM recipients WHERE message_pk = ? ORDER BY address_pk, role", (message_pk,)).fetchall()
    sender_pk = address_pk(catalog, parsed.sender)
    defects = list(parsed.defects)
    try:
        catalog.execute("UPDATE messages SET message_id_normalized = ? WHERE message_pk = ?", (parsed.message_id, message_pk))
    except sqlite3.IntegrityError:
        defects.append(MetadataDefect(field="rebuild", detail=(
            f"parsed Message-ID {parsed.message_id} is already catalogued for identical bytes; kept the previous ID")))
    catalog.execute(
        "UPDATE messages SET sender_address_pk = ?, subject = ?, date_utc = ?, date_source = ? WHERE message_pk = ?",
        (sender_pk, parsed.subject, parsed.date_utc, parsed.date_source, message_pk))
    catalog.execute("DELETE FROM recipients WHERE message_pk = ?", (message_pk,))
    catalog.executemany("INSERT OR IGNORE INTO recipients(message_pk, address_pk, role) VALUES (?, ?, ?)",
                        ((message_pk, address_pk(catalog, recipient.address), recipient.role.value)
                         for recipient in parsed.recipients))
    placeholders = ", ".join("?" for _ in PARSE_DEFECT_FIELDS)
    catalog.execute(f"DELETE FROM metadata_defects WHERE message_pk = ? AND field IN ({placeholders})",
                    (message_pk, *sorted(PARSE_DEFECT_FIELDS)))
    catalog.executemany("INSERT OR IGNORE INTO metadata_defects(message_pk, field, detail) VALUES (?, ?, ?)",
                        ((message_pk, defect.field, defect.detail) for defect in defects))
    after = catalog.execute(
        "SELECT message_id_normalized, sender_address_pk, subject, date_utc, date_source FROM messages WHERE message_pk = ?",
        (message_pk,)).fetchone()
    new_recipients = catalog.execute(
        "SELECT address_pk, role FROM recipients WHERE message_pk = ? ORDER BY address_pk, role", (message_pk,)).fetchall()
    return before != after or old_recipients != new_recipients


def checkpoint_stamp_ns(archive: Path) -> int | None:
    """Return when the last completed checkpoint began (bag-info.txt), if recorded."""
    try:
        for line in (archive / BAG_INFO).read_text(encoding="utf-8").splitlines():
            if line.startswith("Bagging-Timestamp: "):
                stamped = datetime.fromisoformat(line.removeprefix("Bagging-Timestamp: ").strip())
                return int(stamped.timestamp() * 1_000_000_000) if stamped.tzinfo is not None else None
    except (OSError, ValueError):
        return None
    return None


def _check_mailboxes(archive: Path, catalog: sqlite3.Connection, observer: Callable[[RebuildProgress], None] | None,
                     should_stop: Callable[[], bool] | None, listener: Callable[[RebuildProgress], None] | None = None,
                     ) -> None:
    """Refuse to rebuild when a mailbox changed since the last checkpoint and its record count disagrees.

    A mailbox last modified before the last completed checkpoint, whose size and message
    count match what that checkpoint recorded, is taken as unchanged and is not reread;
    File > Revalidate Archive is the full recheck. Only other mailboxes are counted.
    """
    expected = dict(catalog.execute(
        "SELECT g.filename, COUNT(*) FROM mbox_generations g JOIN locations l USING (generation_pk) GROUP BY g.filename"))
    recorded = {name: (count, size) for name, count, size in catalog.execute(
        "SELECT filename, message_count, byte_count FROM mbox_generations")}
    stamp_ns = checkpoint_stamp_ns(archive)
    mailboxes = sorted(mbox_directory(archive).glob("*.mbox"))
    reporter = ByteProgressReporter("Checking mailboxes for changes", sum(path.stat().st_size for path in mailboxes),
                                    observer, listener)
    reporter.display(force=True)
    for path in mailboxes:
        if should_stop is not None and should_stop():
            raise KeyboardInterrupt
        status = path.stat()
        catalogued = int(expected.get(path.name, 0))
        unchanged = (stamp_ns is not None and status.st_mtime_ns < stamp_ns - CHECKPOINT_STAMP_MARGIN_NS
                     and recorded.get(path.name) == (catalogued, status.st_size))
        if not unchanged:
            box = PreservingMbox(path, factory=None, create=False)
            try:
                actual = len(box)
            finally:
                box.close()
            if actual != catalogued:
                raise RuntimeError(f"canonical MBOX/catalog count mismatch for {path.name}: "
                                   f"{actual} records, {catalogued} catalogued")
        reporter.advance(status.st_size)
    reporter.finish()


def _copy_catalog(source: Path, destination: Path, observer: Callable[[RebuildProgress], None] | None,
                  should_stop: Callable[[], bool] | None, listener: Callable[[RebuildProgress], None] | None = None,
                  ) -> None:
    reporter = ByteProgressReporter("Copying catalog", source.stat().st_size, observer, listener)
    reporter.display(force=True)

    def progress(_status: int, remaining: int, total: int) -> None:
        if should_stop is not None and should_stop():
            raise KeyboardInterrupt
        reporter.progress.completed_bytes = (total - remaining) * reporter.progress.total_bytes // max(total, 1)
        reporter.display()

    with closing(sqlite3.connect(source)) as live, closing(sqlite3.connect(destination)) as copy:
        live.backup(copy, pages=4096, progress=progress)
    reporter.finish()


def _remove_database(path: Path) -> None:
    for candidate in (path, path.with_name(path.name + "-journal"), path.with_name(path.name + "-wal"),
                      path.with_name(path.name + "-shm")):
        candidate.unlink(missing_ok=True)


def _discard_stale_new_files(archive: Path) -> None:
    for name in (CATALOG, SEARCH):
        for path in archive.glob(f"{name}{NEW_INFIX}*"):
            _remove_database(path)


def _search_has_attachments(archive: Path) -> bool:
    path = archive / SEARCH
    if not path.is_file():
        return False
    try:
        with closing(sqlite3.connect(sqlite_uri(path), uri=True)) as search:
            return search.execute("SELECT 1 FROM message_metadata WHERE attachment_fts_rowid IS NOT NULL LIMIT 1"
                                  ).fetchone() is not None
    except sqlite3.Error:
        return False


def publish(archive: Path, new_catalog: Path, new_search: Path) -> None:
    """Rename each live database to `.old` (replacing an earlier one), then move the new file in.

    The catalog goes first; search rows are keyed by message hash, so the new catalog
    with the old search index is still consistent if publication is interrupted.
    """
    for name, replacement in ((CATALOG, new_catalog), (SEARCH, new_search)):
        live, old = archive / name, archive / f"{name}{OLD_SUFFIX}"
        if live.exists():
            _remove_database(old)
            os.replace(live, old)
        os.replace(replacement, live)
    sync_directory(archive)


def rebuild_archive(
    archive: Path,
    *,
    workers: int = REBUILD_DEFAULT_WORKERS,
    index_attachments: bool | None = None,
    writer_lease: WriterLease | None = None,
    observer: Callable[[RebuildProgress], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    application_version: str = "",
) -> RebuildResult:
    """Rebuild derived catalog fields and the search index into new files, then publish them.

    ``index_attachments`` defaults to what the current search index does. ``observer``
    receives byte progress instead of terminal output; ``should_stop`` is polled
    between messages. Raises RebuildInterrupted when stopped.
    """
    archive = archive.resolve(strict=True)
    if not (archive / CATALOG).is_file():
        raise ValueError(f"{archive} has no {CATALOG}")
    identity = os.path.normcase(str(archive))
    owned = writer_lease is None
    lease = writer_lease or WriterLease.acquire(archive, identity, "rebuild databases", uuid4().hex,
                                               application_version)
    if not lease.acquired or not lease.lock_path.parent.parent.samefile(archive):
        if owned:
            lease.release()
        raise ValueError("an acquired writer lease for this archive is required")
    try:
        activity = RebuildActivity(archive, workers, application_version)
        try:
            result = _rebuild(archive, workers, _search_has_attachments(archive) if index_attachments is None
                                else index_attachments, observer, should_stop, activity)
        except RebuildInterrupted as error:
            activity.finish("completed" if error.published else "interrupted",
                            "Published; stopped afterwards" if error.published
                            else "Stopped; the existing databases are unchanged")
            raise
        except BaseException as error:
            activity.finish("failed", "Failed; the existing databases are unchanged", f"{type(error).__name__}: {error}")
            raise
        activity.finish("completed", f"Rebuilt from {result.messages:,} messages; {result.changed:,} catalog entries "
                        "changed; previous databases kept as .old")
        return result
    finally:
        if owned:
            lease.release()


def _rebuild(archive: Path, workers: int, index_attachments: bool,
               observer: Callable[[RebuildProgress], None] | None,
               should_stop: Callable[[], bool] | None, activity: RebuildActivity) -> RebuildResult:
    _discard_stale_new_files(archive)
    needed = sum((archive / name).stat().st_size for name in (CATALOG, SEARCH) if (archive / name).exists())
    free = shutil.disk_usage(archive).free
    if free < needed * FREE_SPACE_MARGIN:
        raise ValueError(f"rebuilding needs about {formatted_bytes(int(needed * FREE_SPACE_MARGIN))} free "
                         f"beside the archive; {formatted_bytes(free)} is available")
    token = uuid4().hex[:8]
    new_catalog = archive / f"{CATALOG}{NEW_INFIX}{token}"
    new_search = archive / f"{SEARCH}{NEW_INFIX}{token}"
    published = False
    try:
        _copy_catalog(archive / CATALOG, new_catalog, observer, should_stop, activity.update)
        catalog = create_catalog(new_catalog)
        try:
            search = create_search(new_search)
            try:
                _check_mailboxes(archive, catalog, observer, should_stop, activity.update)
                result = _rederive(archive, catalog, search, workers, index_attachments, observer, should_stop, activity)
                catalog.commit()
                search.commit()
            finally:
                activity.search = None
                search.close()
        finally:
            catalog.close()
        with UninterruptiblePublication():
            publish(archive, new_catalog, new_search)
            published = True
        return result
    except KeyboardInterrupt as error:
        raise RebuildInterrupted(published) from error
    finally:
        if not published:
            _remove_database(new_catalog)
            _remove_database(new_search)


def _rederive(archive: Path, catalog: sqlite3.Connection, search: sqlite3.Connection, workers: int,
              index_attachments: bool, observer: Callable[[RebuildProgress], None] | None,
              should_stop: Callable[[], bool] | None, activity: RebuildActivity) -> RebuildResult:
    total_row = catalog.execute("SELECT COALESCE(SUM(byte_length), 0), COUNT(*) FROM locations").fetchone()
    assert total_row is not None
    reporter = ByteProgressReporter(f"Rebuilding databases ({workers} workers)", int(total_row[0]), observer,
                                    activity.update)
    reporter.display(force=True)
    catalog.execute("BEGIN")
    messages = changed = failures = 0
    live = sqlite3.connect(sqlite_uri(archive / CATALOG), uri=True)
    items = _work_items(live)
    next_item = next(items, None)
    pending: deque[tuple[Future[RebuiltMessage], int, str]] = deque()
    activity.search = search
    in_flight = 0
    # Parsing and indexing are CPU-bound Python, so threads would share one core through the
    # GIL; spawned worker processes use all configured workers. Only this process writes SQLite.
    pool = worker_pool(workers)
    with closing(live):
        try:
            while next_item is not None or pending:
                if should_stop is not None and should_stop():
                    raise KeyboardInterrupt
                while next_item is not None and len(pending) < workers * 2:
                    length = next_item.location.byte_length
                    if pending and in_flight + length > MAX_IN_FLIGHT_BYTES:
                        break
                    pending.append((pool.submit(rebuild_message, archive, index_attachments, next_item), length,
                                    next_item.filename))
                    in_flight += length
                    next_item = next(items, None)
                future, length, filename = pending.popleft()
                result = future.result()
                in_flight -= length
                changed += _apply(catalog, result)
                failures += result.parsed is None
                if result.indexed is not None:
                    write_prepared_search_message(search, result.indexed, date_utc=result.date_utc)
                messages += 1
                activity.messages = messages
                activity.detail.record(result.worker_pid, filename, length)
                reporter.advance(length)
        except BaseException:
            pool.shutdown(wait=True, cancel_futures=True)
            raise
        pool.shutdown(wait=True)
    if messages != int(total_row[1]):
        raise RuntimeError(f"catalog has {total_row[1]} located messages but only {messages} were rebuilt")
    activity.detail.count_names(search)
    reporter.finish()
    return RebuildResult(messages=messages, changed=changed, failures=failures,
                           processing_database=(archive / PROCESSING).is_file())
