# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Run bounded read-only IMAP acquisition against an explicitly separate scratch archive.
# Copy only account references and owner settings; passwords remain in the native vault.
# An owned child executes the real importer and requests cancellation at the deadline.
# The parent allows checkpoint cleanup, then terminates a child that exceeds its grace.
# Repeated invocations resume only scratch archives marked by this tool.
# Verify canonical integrity after shutdown and retain a typed evaluation summary.
"""Timed scratch acceptance for the production IMAP importer."""
from __future__ import annotations

import argparse
from hashlib import sha256
import sys
from pathlib import Path
from threading import Timer

from pydantic import BaseModel, Field, TypeAdapter

from mailarchiver.__main__ import IngestInterrupted, IngestRequest, run_ingest
from mailarchiver.application import create_empty_archive
from mailarchiver.archive_config import ArchiveConfig, load_archive_config, save_archive_config
from mailarchiver.change_signal import ChangeEvent
from mailarchiver.imap_source import imap_locator
from mailarchiver.owned_command import run_owned_command
from mailarchiver.ingest_status import IngestStatus, latest_ingest_status
from mailarchiver.standalone_verify import verify_archive

MARKER = ".ect-imap-acceptance"


class AcceptanceSettings(BaseModel):
    source_archive: Path
    scratch_archive: Path
    seconds: float = Field(default=600, gt=0, allow_inf_nan=False)
    grace_seconds: float = Field(default=120, gt=0, allow_inf_nan=False)
    verification_seconds: float = Field(default=120, gt=0, allow_inf_nan=False)
    workers: int = Field(default=4, ge=1)
    no_scan: bool = False


class AcceptanceResult(BaseModel):
    child_exitcode: int | None
    forced_stop: bool
    integrity_errors: list[str]
    status: IngestStatus | None
    evaluation_error: str | None = None
    progress_observed: bool


def prepare(settings: AcceptanceSettings) -> None:
    """Require a disjoint destination and reuse only this tool's account-scoped scratch state."""
    source, scratch = settings.source_archive.resolve(), settings.scratch_archive.resolve()
    if source == scratch or source in scratch.parents or scratch in source.parents:
        raise ValueError("scratch and source archives must be separate, non-nested directories")
    config = load_archive_config(source)
    accounts = [account for account in config.imap_sources if account.enabled]
    if len(accounts) != 1:
        raise ValueError("scratch acceptance currently requires exactly one enabled IMAP account")
    identity = sha256(accounts[0].model_dump_json().encode()).hexdigest()
    marker = scratch / MARKER
    if scratch.exists() and any(scratch.iterdir()):
        if not marker.is_file() or marker.read_text() != identity:
            raise ValueError("refusing an existing archive without the matching acceptance marker")
        if load_archive_config(scratch).imap_sources != accounts:
            raise ValueError("scratch account configuration changed")
        return
    scratch.mkdir(mode=0o700, parents=True, exist_ok=True)
    create_empty_archive(scratch, owner_rules=config.owner)
    scratch.chmod(0o700)
    save_archive_config(scratch, ArchiveConfig(owner=config.owner, imap_sources=accounts))
    marker.write_text(identity)


def import_cycle(settings: AcceptanceSettings) -> None:
    """Use one extra network slot while preserving a distinct local processing pool."""
    scratch = settings.scratch_archive.resolve()
    account = load_archive_config(scratch).imap_sources[0]
    stop = ChangeEvent()
    deadline = Timer(settings.seconds, stop.set)
    deadline.start()
    try:
        run_ingest(IngestRequest(archive=scratch,
            roots=[imap_locator(scratch, account, settings.workers)], workers=settings.workers,
            imap_connections=1, scan_policy="not-scanned" if settings.no_scan else "clamav"),
            terminal=False, stop_event=stop)
    except IngestInterrupted:
        pass  # The requested deadline is a controlled checkpoint, not a completed import.
    except Exception as error:
        print(f"Scratch import stopped: {error}", flush=True)
        raise SystemExit(1) from None
    finally:
        deadline.cancel()


def worker_command(settings: AcceptanceSettings, operation: str) -> list[str]:
    command = [sys.executable, "-m", "scripts.imap_acceptance", "--worker", operation,
        "--source-archive", str(settings.source_archive.resolve()),
        "--scratch-archive", str(settings.scratch_archive.resolve()),
        "--seconds", str(settings.seconds), "--workers", str(settings.workers)]
    if settings.no_scan:
        command.append("--no-scan")
    return command


def evaluate(settings: AcceptanceSettings) -> AcceptanceResult:
    prepare(settings)
    forced, error, code = False, None, None
    try:
        imported = run_owned_command(worker_command(settings, "import"),
                                     timeout=settings.seconds + settings.grace_seconds)
        code = imported.returncode
        if code:
            error = imported.stderr[-2000:] or imported.stdout[-2000:]
    except RuntimeError as failure:
        forced, error = True, str(failure)
    try:
        verified = run_owned_command(worker_command(settings, "verify"), timeout=settings.verification_seconds)
        errors = TypeAdapter(list[str]).validate_json(verified.stdout)
        if verified.returncode and not errors:
            errors = ["Integrity verifier exited without a valid result"]
    except (RuntimeError, ValueError) as failure:
        errors = [f"Integrity verification did not finish: {failure}"]
    status = latest_ingest_status(settings.scratch_archive)
    return AcceptanceResult(child_exitcode=code, forced_stop=forced, evaluation_error=error,
        integrity_errors=errors, status=status,
        progress_observed=bool(status and status.download_coverage and status.download_coverage.messages))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-archive", required=True, type=Path)
    parser.add_argument("--scratch-archive", required=True, type=Path)
    parser.add_argument("--seconds", type=float, default=600)
    parser.add_argument("--grace-seconds", type=float, default=120)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--no-scan", action="store_true")
    parser.add_argument("--verification-seconds", type=float, default=120)
    parser.add_argument("--worker", choices=("import", "verify"), help=argparse.SUPPRESS)
    args = parser.parse_args()
    settings = AcceptanceSettings(source_archive=args.source_archive, scratch_archive=args.scratch_archive,
        seconds=args.seconds, grace_seconds=args.grace_seconds, verification_seconds=args.verification_seconds,
        workers=args.workers, no_scan=args.no_scan)
    if args.worker:
        if not (settings.scratch_archive / MARKER).is_file():
            raise ValueError("worker requires an initialized acceptance scratch archive")
        prepare(settings)
        if args.worker == "import":
            import_cycle(settings)
            return 0
        errors = verify_archive(settings.scratch_archive, quiet=True)
        print(TypeAdapter(list[str]).dump_json(errors).decode())
        return int(bool(errors))
    try:
        result = evaluate(settings)
    except KeyboardInterrupt:
        return 130
    print(result.model_dump_json(indent=2))
    return int(result.forced_stop or result.child_exitcode != 0 or bool(result.integrity_errors)
               or result.status is None or result.status.state not in {"completed", "interrupted"}
               or result.status.state != "completed" and not result.progress_observed)


if __name__ == "__main__":
    raise SystemExit(main())
