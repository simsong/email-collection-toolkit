# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Configure and synchronize read-only IMAP sources from a terminal.

Purpose: Provide standalone interactive setup and one-run IMAP synchronization.

How it works:
Save native-keyring credentials, acquire the archive lease, run ingest and report checkpointed
progress.

Fundamental dependents:

- No internal module relies on this standalone CLI; it drives the shared ingest engine.
"""
from __future__ import annotations

import argparse
from collections.abc import Callable
from contextlib import closing
import getpass
import os
from importlib.metadata import version
from pathlib import Path
import signal
import sqlite3
import sys
from threading import Event, Thread
import time
from uuid import uuid4

from .__main__ import IngestRequest, run_ingest, safe_status_text
from .application import ArchiveDocument, configured_workers, create_empty_archive
from .worker_preferences import worker_argument
from .archive_config import load_archive_config, save_archive_config
from .imap_credentials import password_for, save_password
from .imap_models import ImapAccount, ImapTotals
from .imap_source import imap_locator
from .imap_state import totals
from .owner_rules import OwnerRules
from .sqlite_paths import sqlite_uri
from .writer_lock import WriterLease

STATUS_SECONDS = 300


def prompt_account(existing: ImapAccount | None = None, *,
                   prompt: Callable[[str], str] = input,
                   secret_prompt: Callable[[str], str] = getpass.getpass) -> tuple[ImapAccount, str]:
    """Collect connection settings without ever placing a secret in a saved model."""
    def field(name: str, default: str) -> str:
        entered = prompt(f"{name}" + (f" [{default}]" if default else "") + ": ").strip()
        return entered or default
    server = field("IMAP server", existing.server if existing else "")
    port = int(field("Port", str(existing.port) if existing else "993"))
    tls_value = field("TLS mode (implicit/starttls)",
                      existing.tls if existing else "starttls" if port == 143 else "implicit")
    if tls_value not in {"implicit", "starttls"}:
        raise ValueError("TLS mode must be implicit or starttls")
    tls = "starttls" if tls_value == "starttls" else "implicit"
    username = field("Username", existing.username if existing else "")
    account = ImapAccount(server=server, port=port, username=username, tls=tls)
    if existing is not None and (server, port, username, tls) == (
            existing.server, existing.port, existing.username, existing.tls):
        account = existing
    return account, secret_prompt("Password or app password (stored in the OS keyring): ")


class SyncReporter:
    def __init__(self, archive: Path, show_messages: bool, *, interval: float = STATUS_SECONDS) -> None:
        self.archive = archive
        self.show_messages = show_messages
        self.interval = interval
        self.baseline = totals(archive)
        with closing(sqlite3.connect(sqlite_uri(archive / "archive.sqlite3"), uri=True)) as database:
            self.cursor = int(database.execute("SELECT coalesce(max(observation_pk),0) FROM observations").fetchone()[0])
        self.finished = Event()
        self.thread = Thread(target=self._run, name="imap-status", daemon=True)
        self.failure: Exception | None = None

    def display(self, status: str) -> None:
        current = totals(self.archive)
        difference = ImapTotals(archived=current.archived - self.baseline.archived,
            duplicates=current.duplicates - self.baseline.duplicates,
            excluded=current.excluded - self.baseline.excluded, errors=current.errors - self.baseline.errors)
        print(f"{status}: newly archived={difference.archived} duplicate downloads={difference.duplicates} "
              f"excluded={difference.excluded} errors={difference.errors}", file=sys.stderr, flush=True)

    def _messages(self) -> None:
        if not self.show_messages:
            return
        with closing(sqlite3.connect(sqlite_uri(self.archive / "archive.sqlite3"), uri=True)) as database:
            rows = database.execute("SELECT o.observation_pk,o.disposition,"
                "coalesce(json_extract(sf.metadata_json,'$.display_name'),sf.source_path),coalesce(m.subject,'') "
                "FROM observations o JOIN source_files sf USING(source_file_pk) "
                "LEFT JOIN messages m USING(message_pk) WHERE o.observation_pk>? "
                "AND sf.source_plugin='imap' AND instr(o.source_cursor,'#mime=')=0 ORDER BY o.observation_pk",
                (self.cursor,))
            for observation, disposition, mailbox, subject in rows:
                print(f"{disposition}: {safe_status_text(mailbox)}: {safe_status_text(subject)}",
                      file=sys.stderr, flush=True)
                self.cursor = observation

    def _run(self) -> None:
        next_report = time.monotonic() + self.interval
        try:
            while not self.finished.wait(min(1.0, self.interval)):
                self._messages()
                if time.monotonic() >= next_report:
                    self.display("running")
                    next_report = time.monotonic() + self.interval
        except Exception as error:  # Progress failure must not cancel canonical publication.
            self.failure = error
            print(f"IMAP status reporting failed: {error}", file=sys.stderr, flush=True)

    def start(self) -> None:
        self.display("starting")
        self.thread.start()

    def finish(self, status: str) -> None:
        self.finished.set()
        self.thread.join()
        self._messages()
        self.display(status)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    target = result.add_mutually_exclusive_group(required=True)
    target.add_argument("--new", type=Path, metavar="ARCHIVE", help="create an empty new archive; never overwrite")
    target.add_argument("--archive", type=Path, help="synchronize an existing archive")
    result.add_argument("--source", help="synchronize only this saved source ID")
    setup = result.add_mutually_exclusive_group()
    setup.add_argument("--configure", action="store_true", help="prompt for settings and update saved credentials")
    setup.add_argument("--add-source", action="store_true", help="prompt for another account")
    result.add_argument("--workers", type=worker_argument, help="override worker preferences (default: half CPUs; maximum: twice CPUs)")
    result.add_argument("--show-messages", action="store_true", help="print each completed message download and disposition")
    result.add_argument("--no-scan", action="store_true", help="explicitly import without antivirus; retain not-scanned evidence")
    return result


def synchronize(args: argparse.Namespace) -> int:
    workers = configured_workers(args.workers)
    if args.source and (args.new or args.add_source):
        raise ValueError("--source selects an existing account; omit it with --new or --add-source")
    path: Path = (args.new or args.archive).expanduser().absolute()
    if args.new:
        create_empty_archive(path)
    else:
        ArchiveDocument.open(path)
    lease = WriterLease.acquire(path, os.path.normcase(str(path.resolve())), "IMAP synchronization", uuid4().hex,
                                version("mailarchiver"))
    reporter: SyncReporter | None = None
    stop = Event()
    previous = signal.getsignal(signal.SIGINT)
    result = "failed"
    try:
        config = load_archive_config(path)
        if not config.imap_sources or args.configure or args.add_source:
            if not sys.stdin.isatty():
                raise ValueError("IMAP setup requires an interactive terminal; configure once before unattended runs")
            existing: ImapAccount | None = None
            if args.configure and config.imap_sources:
                candidates = [source for source in config.imap_sources if not args.source or source.id == args.source]
                if len(candidates) != 1:
                    raise ValueError("Use --source ID to choose the account to configure")
                existing = candidates[0]
            account, password = prompt_account(existing)
            save_password(account, password)
            password = ""
            config.imap_sources = [source for source in config.imap_sources if existing is None or source.id != existing.id]
            config.imap_sources.append(account)
            if config.owner is None or not config.owner.include:
                config.owner = OwnerRules(include=[account.username])
            save_archive_config(path, config)
            print(f"Saved IMAP source {account.id} for {account.username}", file=sys.stderr)
            if args.source and existing is not None:
                args.source = account.id
        selected = [source for source in config.imap_sources
                    if source.enabled and (not args.source or source.id == args.source)]
        if not selected:
            raise ValueError("No enabled IMAP sources match the requested source ID")
        for account in selected:
            password_for(account)  # Fail before starting ingest if a secret is absent or inaccessible.
        signal.signal(signal.SIGINT, lambda _signal, _frame: stop.set())
        reporter = SyncReporter(path, args.show_messages)
        reporter.start()
        request = IngestRequest(archive=path, roots=[imap_locator(path, source, workers) for source in selected],
            workers=workers, index_attachments=True,
            scan_policy="not-scanned" if args.no_scan else "clamav")
        run_ingest(request, writer_lease=lease, terminal=False, stop_event=stop)
        result = "completed"
        return 0
    except KeyboardInterrupt:
        result = "interrupted; saved progress will resume on the next run"
        return 130
    finally:
        signal.signal(signal.SIGINT, previous)
        try:
            if reporter is not None:
                reporter.finish(result)
        finally:
            lease.release()


def main() -> int:
    args = parser().parse_args()
    try:
        return synchronize(args)
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130
    except Exception as error:
        print(f"IMAP synchronization failed: {safe_status_text(str(error))}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
