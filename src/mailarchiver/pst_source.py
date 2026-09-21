# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""Read-only Rust PST adapter into the common Python ingest processor pipeline."""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from .clamav_definitions import LIBRARY_ENV, DATABASE_ENV, CERTIFICATES_ENV, library_path, selected_definitions, certificates_path
from .plugin_api import FileProbe, MailContainer, MailObject, PluginContext, ProgressEvent
from .sources import LocalSourcePlugin, MboxFileParser

IMPORT_HEADERS = (b"X-Imported-URI: ", b"X-Importer-Name: ", b"X-Importer-Version: ")


class PstSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    executable: str | None = None
    max_output_bytes: int = Field(default=1024 * 1024 * 1024, gt=0)
    max_diagnostics_bytes: int = Field(default=64 * 1024 * 1024, gt=0)
    timeout_seconds: float = Field(default=60, gt=0, allow_inf_nan=False)


class ImportReceipt(BaseModel):
    executable: Path
    executable_sha256: str
    source: Path
    source_sha256: str
    exit_code: int | None = None
    emitted: int = 0
    reconstructed_mime: bool = True
    output_bytes: int = 0
    diagnostics_bytes: int = 0
    output_truncated: bool = False
    diagnostics_truncated: bool = False


def file_hash(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def validate_record(raw: bytes) -> None:
    lines = raw.split(b"\n", 3)
    for prefix, line in zip(IMPORT_HEADERS, lines, strict=False):
        value = line.removeprefix(prefix).rstrip(b"\r")
        if not line.startswith(prefix) or not value or any(byte < 32 or byte > 126 for byte in value):
            raise ValueError("invalid MCT importer provenance header")
    if len(lines) < 4 or not urlsplit(lines[0][len(IMPORT_HEADERS[0]):].decode("ascii").strip()).scheme:
        raise ValueError("invalid MCT importer URI or incomplete header block")



class PstFileParser:
    kind = "pst"

    def __init__(self, context: PluginContext) -> None:
        self.context = context

    def recognizes(self, probe: FileProbe) -> bool:
        if probe.prefix.startswith(b"!BDN"):
            return probe.prefix[8:10] != b"SO"
        return probe.path.suffix.lower() == ".pst"

    def configuration_fingerprint(self) -> str:
        settings = PstSettings.model_validate(self.context.get_my_config())
        return hashlib.sha256(("pst-v3:" + settings.model_dump_json()).encode()).hexdigest()

    def messages(self, container: MailContainer, resume_cursor: str | None) -> Iterator[MailObject | ProgressEvent]:
        yield from self._rust_messages(container, resume_cursor)

    def _rust_messages(self, container: MailContainer, resume_cursor: str | None) -> Iterator[MailObject | ProgressEvent]:
        if self.context.archive is None:
            raise ValueError("PST extraction requires an archive workspace")
        settings = PstSettings.model_validate(self.context.get_my_config())
        bundled = Path(getattr(sys, "_MEIPASS", "")) / "importers/pst-importer"
        executable = settings.executable or (str(bundled) if getattr(sys, "frozen", False) else shutil.which("pst-importer"))
        if executable is None:
            candidate = Path(__file__).resolve().parents[2] / "target" / "release" / "pst-importer"
            if candidate.is_file():
                executable = str(candidate)
        if executable is None:
            raise FileNotFoundError("PST importer unavailable; run make pst-importer or configure plugins.pst.executable")
        program = Path(executable).resolve()
        source = LocalSourcePlugin.source_file(container)
        root = self.context.archive / "processing-pst"
        root.mkdir(exist_ok=True)
        workspace = Path(tempfile.mkdtemp(dir=root))
        output_path = workspace / "output.mboxrd"
        diagnostics_path = workspace / "stderr.txt"
        receipt = ImportReceipt(executable=program, executable_sha256=file_hash(program),
                                source=source.path, source_sha256=file_hash(source.path))
        environment = os.environ.copy()
        environment.pop("MAILARCHIVER_SCAN", None)
        if self.context.scan_policy == "clamav":
            environment["MAILARCHIVER_SCAN"] = "1"
            environment[LIBRARY_ENV] = str(library_path())
            environment[DATABASE_ENV] = str(selected_definitions().directory)
            if certs := certificates_path():
                environment[CERTIFICATES_ENV] = str(certs)
        completed = False
        try:
            with output_path.open("wb") as output, diagnostics_path.open("wb") as diagnostics:
                with subprocess.Popen([str(program), "--", str(source.path)], stdout=output, stderr=diagnostics, env=environment) as process:
                    try:
                        import time
                        deadline = time.monotonic() + settings.timeout_seconds
                        while process.poll() is None:
                            if time.monotonic() >= deadline:
                                raise TimeoutError("PST importer timeout")
                            if output_path.stat().st_size > settings.max_output_bytes or diagnostics_path.stat().st_size > settings.max_diagnostics_bytes:
                                raise ValueError("PST importer output limit exceeded")
                            yield ProgressEvent(work_id=container.work_id, phase="extracting PST", completed=output_path.stat().st_size, unit="bytes")
                            try:
                                process.wait(timeout=0.05)
                            except subprocess.TimeoutExpired:
                                pass
                    finally:
                        if process.poll() is None:
                            process.kill()
                            process.wait()
                        receipt.exit_code = process.returncode
            if output_path.stat().st_size > settings.max_output_bytes or diagnostics_path.stat().st_size > settings.max_diagnostics_bytes:
                raise ValueError("PST importer output limit exceeded")
            extracted = source.model_copy(update={"path": output_path, "byte_length": output_path.stat().st_size, "kind": "mbox"})
            previous = None
            # A failed producer's final record may be truncated: release it only on success.
            for record in MboxFileParser().messages(extracted):
                if previous is not None:
                    validate_record(previous.raw)
                    receipt.emitted += 1
                    if resume_cursor is None or previous.source_offset >= int(resume_cursor):
                        yield MailObject(source=container.source, work_id=container.work_id,
                            cursor=str(previous.source_offset), raw=previous.raw, mbox_envelope=previous.mbox_envelope,
                            completed_messages=receipt.emitted,
                            scan_responsibility="producer")
                previous = record
            if receipt.exit_code != 0:
                raise RuntimeError(f"PST extraction incomplete (exit {receipt.exit_code}); evidence retained at {workspace}")
            if previous is not None:
                validate_record(previous.raw)
                receipt.emitted += 1
                if resume_cursor is None or previous.source_offset >= int(resume_cursor):
                    yield MailObject(source=container.source, work_id=container.work_id,
                        cursor=str(previous.source_offset), raw=previous.raw, mbox_envelope=previous.mbox_envelope,
                        completed_messages=receipt.emitted,
                        scan_responsibility="producer")
            if file_hash(source.path) != receipt.source_sha256:
                raise ValueError("PST source changed during extraction")
            completed = True
        finally:
            # Bound retained derived evidence even when a fast exit bypassed polling.
            receipt.output_bytes = output_path.stat().st_size if output_path.exists() else 0
            receipt.diagnostics_bytes = diagnostics_path.stat().st_size if diagnostics_path.exists() else 0
            receipt.output_truncated = receipt.output_bytes > settings.max_output_bytes
            receipt.diagnostics_truncated = receipt.diagnostics_bytes > settings.max_diagnostics_bytes
            for path, size, limit in ((output_path, receipt.output_bytes, settings.max_output_bytes),
                                       (diagnostics_path, receipt.diagnostics_bytes, settings.max_diagnostics_bytes)):
                if size > limit:
                    with path.open("r+b") as retained:
                        retained.truncate(limit)
            (workspace / "receipt.json").write_text(receipt.model_dump_json())
            if completed:
                output_path.unlink()  # Canonical messages are now filed; retain receipt and diagnostics.
