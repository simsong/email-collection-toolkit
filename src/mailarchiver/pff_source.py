# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""Run the independent libpff converter; never import its native reader in the host."""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Generator, Iterator
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .clamav_definitions import LIBRARY_ENV, DATABASE_ENV, CERTIFICATES_ENV, library_path, selected_definitions, certificates_path
from .plugin_api import FileProbe, MailContainer, MailObject, PluginContext, ProgressEvent
from .pst_source import file_hash, validate_record
from .sources import LocalSourcePlugin, MboxFileParser

ROOT = Path(__file__).resolve().parents[2]


class PffSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    executable: str | None = None
    timeout_seconds: float = Field(default=60, gt=0, allow_inf_nan=False)
    max_message_bytes: int = Field(default=64 * 1024 * 1024, gt=0)
    max_folder_depth: int = Field(default=64, gt=0)
    max_output_bytes: int = Field(default=1024 * 1024 * 1024, gt=0)
    max_diagnostics_bytes: int = Field(default=64 * 1024 * 1024, gt=0)


class PffReceipt(BaseModel):
    source: Path
    source_sha256: str
    libpff_version: str
    process_id: int
    content_type: int | None = None
    folders: int = 0
    encountered: int = 0
    emitted: int = 0
    non_mail: int = 0
    errors: int = 0
    complete: bool = False
    failure: str | None = None
    reconstructed_mime: bool = True


def executable(name: str, configured: str | None = None) -> Path:
    suffix = ".exe" if os.name == "nt" else ""
    if configured:
        return Path(configured).resolve(strict=True)
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS")) / "importers"
        candidate = base / "pff-converter" / (name + suffix) if name == "pff-converter" else base / (name + suffix)
    elif name == "pff-converter":
        candidate = ROOT / "converters/pff/.venv" / ("Scripts" if os.name == "nt" else "bin") / (name + suffix)
    else:
        candidate = ROOT / "target/release" / (name + suffix)
    if candidate.is_file():
        return candidate.resolve()
    if found := shutil.which(name):
        return Path(found).resolve()
    raise FileNotFoundError(f"{name} unavailable; run make {name} or configure plugins.ost.executable")


def run_converter(command: list[str], output: Path, diagnostics: Path, settings: PffSettings,
                  work_id: str, environment: dict[str, str], evidence: tuple[Path, ...] = ()) -> Generator[ProgressEvent, None, int]:
    """Bound a child process and its files, including when it dies or the iterator closes."""
    limits = ((output, settings.max_output_bytes), (diagnostics, settings.max_diagnostics_bytes),
              *((path, settings.max_diagnostics_bytes) for path in evidence))
    try:
        with output.open("wb") as stdout, diagnostics.open("wb") as stderr:
            with subprocess.Popen(command, stdout=stdout, stderr=stderr, env=environment) as process:
                try:
                    deadline = time.monotonic() + settings.timeout_seconds
                    while process.poll() is None:
                        if time.monotonic() >= deadline:
                            raise TimeoutError("libpff extraction timeout")
                        if any(path.exists() and path.stat().st_size > limit for path, limit in limits):
                            raise ValueError("libpff converter output limit exceeded")
                        yield ProgressEvent(work_id=work_id, phase="extracting Outlook", completed=output.stat().st_size, unit="bytes")
                        try:
                            process.wait(timeout=0.05)
                        except subprocess.TimeoutExpired:
                            pass
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait()
        if any(path.exists() and path.stat().st_size > limit for path, limit in limits):
            raise ValueError("libpff converter output limit exceeded")
        return process.returncode
    finally:
        for path, limit in limits:
            if path.exists() and path.stat().st_size > limit:
                with path.open("r+b") as retained:
                    retained.truncate(limit)


class PffFileParser:
    kind = "ost"

    def __init__(self, context: PluginContext) -> None:
        self.context = context

    def recognizes(self, probe: FileProbe) -> bool:
        if probe.prefix.startswith(b"!BDN"):
            return probe.prefix[8:10] == b"SO"
        return probe.path.suffix.lower() == ".ost"

    def configuration_fingerprint(self) -> str:
        settings = PffSettings.model_validate(self.context.get_my_config())
        return hashlib.sha256(("libpff-external-v1:" + settings.model_dump_json()).encode()).hexdigest()

    def messages(self, container: MailContainer, resume_cursor: str | None) -> Iterator[MailObject | ProgressEvent]:
        del resume_cursor  # Re-import and let the host's canonical deduplication handle repeats.
        if self.context.archive is None:
            raise ValueError("libpff extraction requires an archive workspace")
        settings = PffSettings.model_validate(self.context.get_my_config())
        program = executable("pff-converter", settings.executable)
        source = LocalSourcePlugin.source_file(container)
        root = self.context.archive / "processing-libpff"
        root.mkdir(exist_ok=True)
        workspace = Path(tempfile.mkdtemp(dir=root))
        receipt_path = workspace / "receipt.json"
        diagnostics = workspace / "diagnostics.jsonl"
        output = workspace / "output.mboxrd"
        initial_hash = file_hash(source.path)
        environment = os.environ.copy()
        environment.pop("MAILARCHIVER_SCAN", None)
        try:
            code = yield from run_converter([str(program), "--receipt", str(receipt_path),
                "--diagnostics", str(diagnostics), "--timeout-seconds", str(settings.timeout_seconds),
                "--max-message-bytes", str(settings.max_message_bytes),
                "--max-folder-depth", str(settings.max_folder_depth), "--", str(source.path)],
                output, workspace / "stderr.txt", settings, container.work_id, environment, (receipt_path, diagnostics))
            if code not in (0, 3):
                raise RuntimeError(f"libpff extraction incomplete (exit {code}); evidence at {workspace}")
            receipt = PffReceipt.model_validate_json(receipt_path.read_text())
            if receipt.source_sha256 != initial_hash or file_hash(source.path) != initial_hash:
                raise ValueError("libpff source changed during extraction")
            if self.context.scan_policy == "clamav":
                environment["MAILARCHIVER_SCAN"] = "1"
                environment[LIBRARY_ENV] = str(library_path())
                environment[DATABASE_ENV] = str(selected_definitions().directory)
                if certs := certificates_path():
                    environment[CERTIFICATES_ENV] = str(certs)
                scanned = workspace / "scanned.mboxrd"
                scan_code = yield from run_converter([str(executable("mcti-scan")), str(output)], scanned,
                    workspace / "scanner-stderr.txt", settings, container.work_id, environment)
                if scan_code:
                    raise RuntimeError(f"libpff producer scan failed (exit {scan_code}); evidence at {workspace}")
                output = scanned
            extracted = source.model_copy(update={"path": output, "byte_length": output.stat().st_size, "kind": "mbox"})
            count = 0
            for record in MboxFileParser().messages(extracted):
                validate_record(record.raw)
                count += 1
                uri = record.raw.split(b"\n", 1)[0].decode("ascii").strip()
                node = uri.rsplit("#item=", 1)[-1]
                if not node.isdecimal():
                    raise ValueError("libpff converter emitted an invalid PST item URI")
                yield MailObject(work_id=container.work_id, source=container.source, cursor=f"item:{node}",
                    raw=record.raw, completed_messages=count, scan_responsibility="producer")
            if count != receipt.emitted:
                raise RuntimeError("libpff converter record count mismatch")
            if code or not receipt.complete:
                raise RuntimeError(f"libpff extraction incomplete; evidence at {workspace}")
            output.unlink()
            (workspace / "output.mboxrd").unlink(missing_ok=True)
        except Exception as error:
            if not receipt_path.exists():
                receipt_path.write_text(PffReceipt(source=source.path, source_sha256=initial_hash,
                    libpff_version="unknown", process_id=0, failure=str(error)[:4096]).model_dump_json())
            raise
