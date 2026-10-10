# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""Explicit, one-shot FreshClam updates; publish only successfully tested generations."""
from __future__ import annotations

import argparse
import importlib
import os
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel

from .clamav_definitions import DEVELOPMENT_DATABASE, PREFIXES, ActiveDefinitions, DefinitionSet, certificates_path, read_definitions, selected_definitions, update_root, updater_path
from .scanner import ClamScanner
from .owned_command import run_owned_command
from .writer_lock import application_write_activity

EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


class UpdateResult(BaseModel):
    versions: str
    published: datetime
    directory: Path


def write_freshclam_config(path: Path, certs: Path | None, *, checks: int | None = None) -> None:
    """Write a one-shot config without relying on the host's ClamAV installation."""
    lines = ["DatabaseMirror database.clamav.net", "TestDatabases yes"]
    if checks is not None:
        lines.append(f"Checks {checks}")
    if certs is not None:
        if "\n" in str(certs) or "\r" in str(certs):
            raise ValueError("Invalid certificate directory")
        lines.append(f"CVDCertsDirectory {certs}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@contextmanager
def update_lock(root: Path):
    """OS-owned lock releases after a crash; never leave a stale directory lock."""
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / "update.lock").open("a+b") as handle:
        handle.seek(0)
        handle.write(b"\0")
        handle.flush()
        handle.seek(0)
        if os.name == "nt":
            locking = importlib.import_module("msvcrt")
            locking.locking(handle.fileno(), locking.LK_NBLCK, 1)
        else:
            locking = importlib.import_module("fcntl")
            locking.flock(handle.fileno(), locking.LOCK_EX | locking.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                locking.locking(handle.fileno(), locking.LK_UNLCK, 1)
            else:
                locking.flock(handle.fileno(), locking.LOCK_UN)


def validate_definitions(definitions: DefinitionSet) -> None:
    """Verify native loading and real clean/EICAR verdicts before publishing."""
    if definitions.daily.published > datetime.now(UTC):
        raise ValueError("Updated definitions have a future publication date")
    with ClamScanner(definitions=definitions) as scanner:
        if scanner.infected(b"From: offline@example.test\nSubject: clean\n\nA clean validation message.\n"):
            raise ValueError("Clean definition-validation fixture was detected as infected")
        verdict = scanner.scan_sample(EICAR)
        if verdict.status != "infected":
            raise ValueError("Updated definitions did not detect EICAR")


def publish_definitions(staging: Path, root: Path, baseline: DefinitionSet | None) -> UpdateResult:
    """Caller holds update_lock. A failed validation cannot replace active.json."""
    definitions = read_definitions(staging, "updated")
    if baseline is not None and definitions.daily.version < baseline.daily.version:
        raise ValueError("Refusing to replace definitions with an older daily database")
    validate_definitions(definitions)
    generation = uuid4().hex
    destination = root / "generations" / generation
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging.rename(destination)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=root, prefix=".active-", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(ActiveDefinitions(generation=generation).model_dump_json())
        handle.flush()
        os.fsync(handle.fileno())
    try:
        temporary.replace(root / "active.json")
    finally:
        temporary.unlink(missing_ok=True)
    return UpdateResult(versions=definitions.versions, published=definitions.daily.published, directory=destination)


def publish_downloaded_definitions(
    staging: Path, root: Path, baseline: DefinitionSet | None, diagnostics: str,
) -> UpdateResult:
    """Retain updater evidence even when publication already moved staging."""
    files = ", ".join(sorted(path.name for path in staging.iterdir()))
    try:
        return publish_definitions(staging, root, baseline)
    except (OSError, ValueError, RuntimeError) as error:
        raise RuntimeError(f"Downloaded definitions failed validation or publication: {error}; files: {files}; "
                           f"FreshClam: {diagnostics[-4096:]}") from error


def refresh_definitions() -> UpdateResult:
    root = update_root()
    with application_write_activity(), update_lock(root):
        try:
            baseline = selected_definitions()
        except (OSError, ValueError, OverflowError):
            baseline = None
        with tempfile.TemporaryDirectory(prefix=".update-", dir=root) as temporary:
            workspace = Path(temporary)
            staging = workspace / "definitions"
            staging.mkdir()
            for path in baseline.directory.iterdir() if baseline is not None else ():
                if path.is_file() and (path.suffix in (".cvd", ".cld", ".sign") or path.name == "freshclam.dat"):
                    shutil.copyfile(path, staging / path.name)
            state = root / "freshclam.dat"
            if state.is_file():
                shutil.copyfile(state, staging / state.name)
            configuration = workspace / "freshclam.conf"
            write_freshclam_config(configuration, certificates_path(), checks=0)
            try:
                result = run_owned_command([str(updater_path()), f"--config-file={configuration}",
                    f"--datadir={staging}", "--stdout"], timeout=600)
            finally:
                if (staging / "freshclam.dat").is_file():
                    shutil.copyfile(staging / "freshclam.dat", state)
            if result.returncode or "cool-down until" in (result.stdout + result.stderr):
                raise RuntimeError(f"Definition update failed ({result.returncode}): {(result.stdout + result.stderr)[-4096:]}")
            return publish_downloaded_definitions(staging, root, baseline, result.stdout + result.stderr)


def seed_definitions(source: Path) -> UpdateResult:
    """Copy and verify a local baseline without changing its source or CDN state."""
    root = update_root()
    definitions = read_definitions(source, "development")
    with application_write_activity(), update_lock(root):
        try:
            baseline = selected_definitions()
        except (OSError, ValueError, OverflowError):
            baseline = None
        with tempfile.TemporaryDirectory(prefix=".seed-", dir=root) as temporary:
            staging = Path(temporary) / "definitions"
            staging.mkdir()
            for path in source.iterdir():
                if path.is_file() and (path in tuple(item.path for item in definitions.files) or path.suffix == ".sign"):
                    shutil.copyfile(path, staging / path.name)
            return publish_definitions(staging, root, baseline)


def refresh_development() -> UpdateResult:
    """Fence developer definition changes against application replacement too."""
    with application_write_activity():
        return _refresh_development()


def _refresh_development() -> UpdateResult:
    """Seed the project copy from an installed database, then update it in place."""
    directory = DEVELOPMENT_DATABASE
    directory.mkdir(parents=True, exist_ok=True)
    if not any(directory.glob("*.c[vl]d")):
        for prefix in PREFIXES:
            installed = prefix / "var/lib/clamav"
            if installed.is_dir():
                for path in installed.iterdir():
                    if path.is_file() and (path.suffix in (".cvd", ".cld", ".sign") or path.name == "freshclam.dat"):
                        shutil.copyfile(path, directory / path.name)
                if any(directory.glob("*.c[vl]d")):
                    break
    with tempfile.TemporaryDirectory(prefix="freshclam-") as temporary:
        configuration = Path(temporary) / "freshclam.conf"
        write_freshclam_config(configuration, certificates_path())
        subprocess.run([str(updater_path()), f"--config-file={configuration}",
                        f"--datadir={directory}", "--stdout"], check=True, timeout=600)
    definitions = read_definitions(directory, "development")
    validate_definitions(definitions)
    return UpdateResult(versions=definitions.versions, published=definitions.daily.published, directory=directory)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=Path, help="verify and copy an existing local definition set into application storage")
    parser.add_argument("--development", action="store_true", help="update etc/clamdb for development and DMG builds")
    args = parser.parse_args()
    if args.seed is not None and args.development:
        parser.error("--seed and --development are mutually exclusive")
    result = seed_definitions(args.seed) if args.seed is not None else refresh_development() if args.development else refresh_definitions()
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
