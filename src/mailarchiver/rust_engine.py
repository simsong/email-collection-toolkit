# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Expose archive services to the Rust desktop without loading the Python GUI.
# A private stdin/stdout JSON-line channel binds every request to one archive.
# Typed requests validate operations; writer leases serialize mutations across windows.
# Imports run on a worker and retain canonical bytes through the existing engine.
# Closing the owning pipe requests message-boundary cancellation and bounded exit.
# This transitional helper can be replaced without changing the shared HTML interface.
from __future__ import annotations

import os
from pathlib import Path
from queue import Queue
import signal
import sys
import time
from threading import Event, Thread
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, JsonValue

from .__main__ import IngestInterrupted, IngestRequest, run_ingest
from .application import SetupSelection, create_empty_archive, validate_archive
from .archive_config import remember_import_directory
from .clamav_update import refresh_definitions
from .document_options import DocumentOptions
from .gui_processing import picker_page, resume_request, save_identity, unfinished_work
from .ingest_status import latest_ingest_status, read_ingest_history
from .owner_rules import OwnerRules
from .processing.identities import IdentityFilter, ManualDecision
from .scanner import scanner_availability
from .writer_lock import WriterLease


class Request(BaseModel):
    id: int
    method: Literal["ping", "capabilities", "recover", "create", "options_status", "options_update", "identity_query", "identity_update",
                    "processing_work", "resume_processing", "ingest_overview", "history", "antivirus", "import_defaults",
                    "start_import", "stop_import", "job_status", "refresh_definitions"]
    args: list[JsonValue] = Field(default_factory=list)


class Reply(BaseModel):
    id: int
    result: JsonValue = None
    error: str | None = None


class ImportSelection(BaseModel):
    source: Path
    include: str
    exclude: str = ""
    revision: str
    scan_policy: Literal["clamav", "not-scanned"] = "clamav"
    index_attachments: bool = True


class JobState(BaseModel):
    kind: Literal["import", "definitions"] = "import"
    active: bool = False
    stopping: bool = False
    error: str | None = None
    warning: str | None = None
    generation: int = 0


class WorkState(BaseModel):
    available: bool
    ingest: int
    content: int
    failed: int
    source_roots: list[str]
    active: bool


class Overview(BaseModel):
    status: JsonValue = None


class ImportDefaults(BaseModel):
    source: str
    include: list[str]
    exclude: list[str]
    revision: str
    antivirus: JsonValue


class Capabilities(BaseModel):
    available: bool = True
    write_available: bool = os.name != "nt"
    write_detail: str = "" if os.name != "nt" else "Windows archive writing is not supported. Open an existing archive for reading."


WRITE_METHODS = ("create", "recover", "options_update", "identity_update", "start_import", "resume_processing", "refresh_definitions")


class Engine:
    """One archive-bound service; only its import worker runs concurrently."""

    def __init__(self, archive: Path) -> None:
        self.archive = Path(os.path.abspath(archive.expanduser()))
        self.stop = Event()
        self.finished = Event()
        self.finished.set()
        self.idle = Event()
        self.idle.set()
        self.job = JobState()

    def start(self, request: IngestRequest, revision: str | None = None) -> bool:
        if not self.finished.is_set():
            raise ValueError("This window already has an active import or processing job.")
        lease = WriterLease.acquire(self.archive, os.path.normcase(str(self.archive)), "Rust desktop import", uuid4().hex, "rust-engine")
        try:
            if revision is not None and request.owner_rules is not None:
                DocumentOptions(self.archive).save(request.owner_rules, lease, revision)
        except Exception:
            lease.release()
            raise
        self.stop.clear()
        self.finished.clear()
        self.job = JobState(active=True, generation=self.job.generation)

        def work() -> None:
            try:
                run_ingest(request, lease, terminal=False, stop_event=self.stop)
                try:
                    if request.roots:
                        remember_import_directory(self.archive, [Path(root) for root in request.roots])
                except (OSError, ValueError) as error:
                    self.job.warning = f"Import completed, but the source directory could not be saved: {error}"
            except IngestInterrupted:
                pass
            except Exception as error:
                self.job.error = f"{type(error).__name__}: {error}"
            finally:
                lease.release()
                self.job.active = False
                self.job.generation += 1
                self.finished.set()
        Thread(target=work, name="rust-import", daemon=True).start()
        return True

    def call(self, request: Request) -> JsonValue:
        method, args = request.method, request.args
        capabilities = Capabilities()
        if method == "capabilities":
            return capabilities.model_dump(mode="json")
        if method in WRITE_METHODS and not capabilities.write_available:
            raise ValueError(capabilities.write_detail)
        options = DocumentOptions(self.archive)
        if method == "refresh_definitions":
            if not self.finished.is_set():
                raise ValueError("Wait for the current import or definition update to finish.")
            self.finished.clear()
            self.job = JobState(active=True, kind="definitions", generation=self.job.generation)
            def update() -> None:
                try:
                    refresh_definitions()
                except Exception as error:
                    self.job.error = f"{type(error).__name__}: {error}"
                finally:
                    self.job.active = False
                    self.job.generation += 1
                    self.finished.set()
            Thread(target=update, name="rust-definitions", daemon=True).start()
            return True
        if method == "ping":
            return True
        if method == "create":
            create_empty_archive(self.archive)
            return True
        if method == "recover":
            validate_archive(self.archive, recover=True)
            return True
        if method == "options_status":
            state = options.state()
            state.editable = self.finished.is_set() and capabilities.write_available
            return state.model_dump(mode="json")
        if method == "options_update":
            include, exclude, revision = args
            if not all(isinstance(value, str) for value in (include, exclude, revision)):
                raise ValueError("Owner rules and revision must be text")
            with WriterLease.acquire(self.archive, os.path.normcase(str(self.archive)), "Rust owner rules", uuid4().hex, "rust-engine") as lease:
                return options.save(OwnerRules.from_text(str(include), str(exclude)), lease, str(revision)).model_dump(mode="json")
        if method == "identity_query":
            kind = "institution" if args[0] == "institution" else "name"
            return picker_page(self.archive, kind, IdentityFilter.model_validate(args[1])).model_dump(mode="json")
        if method == "identity_update":
            save_identity(self.archive, ManualDecision.model_validate(args[0]))
            return True
        if method == "processing_work":
            work = unfinished_work(self.archive)
            return WorkState(available=work.available and capabilities.write_available, ingest=work.ingest, content=work.content, failed=work.failed,
                             source_roots=work.source_roots, active=self.job.active).model_dump(mode="json")
        if method == "resume_processing":
            return self.start(resume_request(self.archive, args[0] is True, args[1] is True))
        if method == "ingest_overview":
            status = latest_ingest_status(self.archive)
            return Overview(status=status.model_dump(mode="json") if status else None).model_dump(mode="json")
        if method == "history":
            return read_ingest_history(self.archive).model_dump(mode="json")
        if method == "antivirus":
            return scanner_availability().model_dump(mode="json")
        if method == "import_defaults":
            source = Path(str(args[0])).resolve(strict=True)
            SetupSelection(source=source, destination=self.archive).validate_paths()
            state = options.state([source])
            return ImportDefaults(source=str(source), include=state.include, exclude=state.exclude,
                                  revision=state.revision, antivirus=scanner_availability().model_dump(mode="json")).model_dump(mode="json")
        if method == "start_import":
            selection = ImportSelection.model_validate(args[0])
            SetupSelection(source=selection.source, destination=self.archive).validate_paths()
            rules = OwnerRules.from_text(selection.include, selection.exclude)
            if not rules.include:
                raise ValueError("Enter at least one owner email rule before importing.")
            return self.start(IngestRequest(archive=self.archive, roots=[str(selection.source.resolve(strict=True))],
                                           owner_rules=rules, scan_policy=selection.scan_policy,
                                           index_attachments=selection.index_attachments), selection.revision)
        if method == "stop_import":
            self.stop.set()
            self.job.stopping = self.job.active
            return True
        if method == "job_status":
            return self.job.model_dump(mode="json")
        raise ValueError("Unknown engine operation")


def main() -> None:
    """The pipe reader observes owner death even if the service thread is blocked."""
    engine = Engine(Path(sys.argv[1]))
    channel = sys.stdout
    sys.stdout = sys.stderr  # Library progress must never corrupt the RPC stream.
    requests: Queue[str | None] = Queue(maxsize=64)

    def read() -> None:
        try:
            for line in sys.stdin:
                requests.put(line)
        finally:
            engine.stop.set()
            # This thread cannot wait on a service lock or a blocked callback.
            deadline = time.monotonic() + 5
            engine.finished.wait(5)
            engine.idle.wait(max(0, deadline - time.monotonic()))
            if os.name == "posix" and os.getpgrp() == os.getpid():
                os.killpg(os.getpid(), signal.SIGKILL)
            os._exit(0)
    Thread(target=read, name="rust-owner-pipe", daemon=True).start()
    while True:
        line = requests.get()
        if line is None:
            return
        identifier = 0
        engine.idle.clear()
        try:
            request = Request.model_validate_json(line)
            identifier = request.id
            reply = Reply(id=identifier, result=engine.call(request))
        except Exception as error:
            reply = Reply(id=identifier, error=f"{type(error).__name__}: {error}")
        engine.idle.set()
        channel.write(reply.model_dump_json() + "\n")
        channel.flush()


if __name__ == "__main__":
    main()
