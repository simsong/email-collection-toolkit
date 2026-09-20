# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""Direct libclamav scanning in a bounded, run-owned worker; no resident daemon."""
from __future__ import annotations

import multiprocessing
import os
import tempfile
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from pathlib import Path
from threading import RLock, Thread, current_thread
from typing import Self
from uuid import uuid4

from pydantic import BaseModel

from .clamav_definitions import DefinitionSet, definition_selection, library_path, selected_definitions, three_months_after
from .libclamav import Engine, engine_version
from .scan_evidence import ScanEvidence, ScanFailure

CLAMAV_DOWNLOAD_URL = "https://github.com/simsong/mail-archiver/releases"
UNSCANNED_WARNING = "Antivirus unavailable. Importing without scanning may retain infected messages and attachments."
UPDATE_RECOMMENDATION = "Virus definitions are more than three months old. Update virus definitions or download a newer version of Email Collection Toolkit."


class ScannerAvailability(BaseModel):
    """Resource presence and header age; actual engine readiness is checked before import."""
    configured: bool
    detail: str
    engine_version: str | None = None
    definition_date: datetime | None = None
    definition_version: str | None = None
    definition_source: str | None = None
    age_days: int | None = None
    update_recommended: bool = False
    warning: str = ""


def scanner_availability() -> ScannerAvailability:
    try:
        if not library_path().is_file():
            raise ValueError(f"Missing libclamav: {library_path()}")
        selection = definition_selection()
        definitions = selection.definitions
        published = definitions.daily.published
        now = datetime.now(UTC)
        stale = now > three_months_after(published)
        future = published > now
        return ScannerAvailability(configured=True, detail=f"ClamAV {engine_version(library_path())}; embedded engine.",
            engine_version=engine_version(library_path()),
            definition_date=published, definition_version=definitions.versions, definition_source=definitions.source,
            age_days=max(0, (now - published).days), update_recommended=stale or future,
            warning=" ".join(filter(None, (selection.warning, UPDATE_RECOMMENDATION if stale else "Definitions have a future publication date; check the system clock and refresh definitions." if future else ""))))
    except (OSError, ValueError, OverflowError) as error:
        return ScannerAvailability(configured=False, detail=f"{UNSCANNED_WARNING} {error}",
                                   update_recommended=True, warning="Repair or update the application and its virus definitions.")


class ClamScannerStartupError(RuntimeError):
    """The embedded engine could not become ready."""


class ScanRequest(BaseModel):
    request_id: str
    path: Path


class ScanResponse(BaseModel):
    request_id: str
    evidence: ScanEvidence


def engine_worker(connection: Connection, library: Path, definitions: DefinitionSet, workers: int, temporary_directory: Path) -> None:
    """Share one immutable engine among native scan threads; ctypes releases the GIL."""
    engine = None
    send_lock = RLock()
    try:
        engine = Engine(library, definitions, temporary_directory)
        connection.send(ScanEvidence(status="not-scanned", engine_version=engine.version, signature_version=definitions.versions))
        def scan(request: ScanRequest) -> None:
            assert engine is not None
            try:
                evidence = engine.scan(request.path)
            except Exception as error:
                evidence = ScanEvidence(status="scanner-error", detail=f"{type(error).__name__}: {error}",
                                        engine_version=engine.version, signature_version=definitions.versions)
            with send_lock:
                connection.send(ScanResponse(request_id=request.request_id, evidence=evidence))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="libclamav") as pool:
            while True:
                request = connection.recv()
                if request is None:
                    break
                if not isinstance(request, ScanRequest):
                    raise ValueError("Invalid scanner request")
                pool.submit(scan, request)
    except (EOFError, BrokenPipeError):
        pass
    except Exception as error:
        connection.send(ScanEvidence(status="scanner-error", detail=f"{type(error).__name__}: {error}"))
    finally:
        if engine is not None:
            engine.close()
        connection.close()


_SESSIONS: dict[str, "ClamScanner"] = {}
_SESSIONS_LOCK = RLock()


def scan_message(session: str | None, path: Path, timeout: float) -> ScanEvidence:
    with _SESSIONS_LOCK:
        scanner = _SESSIONS.get(session or "")
    if scanner is None:
        raise ScanFailure(ScanEvidence(status="scanner-error", detail="Embedded scanner session is not ready"))
    return scanner.scan(path, timeout)


class ClamScanner(AbstractContextManager["ClamScanner"]):
    """Concurrent scans share an engine; brief IPC locks never surround a native scan."""

    def __init__(self, status_callback: Callable[[], None] | None = None, *, library: Path | None = None,
                 definitions: DefinitionSet | None = None, startup_timeout_seconds: float = 120,
                 scan_timeout_seconds: float = 60, scan_temporary_directory: Path | None = None,
                 workers: int | None = None) -> None:
        if min(startup_timeout_seconds, scan_timeout_seconds) <= 0:
            raise ValueError("ClamAV deadlines must be positive")
        self.library = library
        self.definitions = definitions
        self.status_callback = status_callback
        self.startup_timeout_seconds = startup_timeout_seconds
        self.scan_timeout_seconds = scan_timeout_seconds
        self.scan_temporary_directory = scan_temporary_directory
        self.workers = workers if workers is not None else min(os.cpu_count() or 1, 8)
        if self.workers < 1:
            raise ValueError("Scanner workers must be positive")
        self.session = uuid4().hex
        self.process: BaseProcess | None = None
        self.connection: Connection | None = None
        self.lock = RLock()
        self.pending: dict[str, Future[ScanEvidence]] = {}
        self.reader: Thread | None = None
        self.runtime: tempfile.TemporaryDirectory[str] | None = None
        self.evidence = ScanEvidence(status="not-scanned")

    def __enter__(self) -> Self:
        try:
            definitions = self.definitions or selected_definitions()
            self.runtime = tempfile.TemporaryDirectory(prefix="libclamav-", dir=self.scan_temporary_directory)
            context = multiprocessing.get_context("spawn")
            self.connection, child = context.Pipe()
            self.process = context.Process(target=engine_worker, args=(child, self.library or library_path(), definitions, self.workers, Path(self.runtime.name)), daemon=True)
            try:
                self.process.start()
            finally:
                child.close()
            self.evidence = self._receive(time.monotonic() + self.startup_timeout_seconds, startup=True)
            if self.evidence.status == "scanner-error":
                raise ClamScannerStartupError(self.evidence.detail)
            self.reader = Thread(target=self._read_results, name="clamav-results", daemon=True)
            self.reader.start()
            with _SESSIONS_LOCK:
                _SESSIONS[self.session] = self
            return self
        except (OSError, ValueError, RuntimeError, EOFError) as error:
            self.__exit__()
            raise ClamScannerStartupError(str(error)) from error

    def _receive(self, deadline: float, *, startup: bool = False) -> ScanEvidence:
        assert self.connection is not None
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("ClamAV startup timed out" if startup else "ClamAV scan timed out")
            if self.connection.poll(min(remaining, 0.25)):
                result = self.connection.recv()
                if not isinstance(result, ScanEvidence):
                    raise RuntimeError("Invalid native scanner response")
                return result
            if startup and self.status_callback is not None:
                self.status_callback()
            if self.process is None or not self.process.is_alive():
                raise RuntimeError("ClamAV worker exited without a result")

    def _read_results(self) -> None:
        connection = self.connection
        assert connection is not None
        try:
            while True:
                response = connection.recv()
                if not isinstance(response, ScanResponse):
                    raise RuntimeError("Invalid scanner response")
                with self.lock:
                    future = self.pending.pop(response.request_id, None)
                if future is not None:
                    future.set_result(response.evidence)
        except (OSError, EOFError, RuntimeError, ValueError) as error:
            with self.lock:
                pending = tuple(self.pending.values())
                self.pending.clear()
            for future in pending:
                future.set_exception(RuntimeError(f"ClamAV worker ended: {error}"))

    def scan(self, path: Path, timeout: float | None = None) -> ScanEvidence:
        deadline = time.monotonic() + min(timeout if timeout is not None else self.scan_timeout_seconds, self.scan_timeout_seconds)
        request = ScanRequest(request_id=uuid4().hex, path=path)
        future: Future[ScanEvidence] = Future()
        try:
            with self.lock:
                if self.connection is None or self.process is None or not self.process.is_alive():
                    raise RuntimeError("ClamAV worker is not running")
                self.pending[request.request_id] = future
                self.connection.send(request)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("ClamAV scan timed out")
            try:
                return future.result(timeout=remaining)
            except TimeoutError as error:
                raise TimeoutError("ClamAV scan timed out") from error
        except (OSError, EOFError, RuntimeError, ValueError) as error:
            self.__exit__()
            evidence = self.evidence.model_copy(update={"status": "scanner-error", "detail": str(error)})
            raise ScanFailure(evidence) from error

    def infected(self, raw: bytes) -> bool:
        with tempfile.TemporaryDirectory(prefix="mailarchiver-scan-", dir=self.scan_temporary_directory) as directory:
            path = Path(directory) / "message.eml"
            path.write_bytes(raw)
            evidence = self.scan(path)
        if evidence.status in ("scanner-error", "unscannable"):
            raise ScanFailure(evidence)
        return evidence.status == "infected"

    def __exit__(self, *_: object) -> None:
        with _SESSIONS_LOCK:
            _SESSIONS.pop(self.session, None)
        with self.lock:
            process, self.process = self.process, None
            connection, self.connection = self.connection, None
            pending = tuple(self.pending.values())
            self.pending.clear()
        if process is not None:
            if process.is_alive():
                process.terminate()
            process.join(timeout=5)
            if process.is_alive():
                process.kill()
                process.join(timeout=5)
        if connection is not None:
            connection.close()
        for future in pending:
            if not future.done():
                future.set_exception(RuntimeError("ClamAV session ended before scan completion"))
        if self.reader is not None and self.reader is not current_thread():
            self.reader.join(timeout=5)
        self.reader = None
        if self.runtime is not None:
            self.runtime.cleanup()
            self.runtime = None
