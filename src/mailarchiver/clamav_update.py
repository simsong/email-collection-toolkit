# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""One-shot FreshClam updates; publish only successfully tested generations.

Purpose: Refresh antivirus definitions explicitly or before an ordinary scan session.

How it works:
Supervise FreshClam, validate candidate signatures by scanning, and atomically publish a
generation or an explicitly requested standalone definition directory.

Fundamental dependents:

- ``gui_app``: the About/update workflow depends on verified definition refresh.
- ``self_test``: packaged acceptance exercises this same update and publication path.
"""
from __future__ import annotations

import argparse
import ctypes
import errno
import importlib
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from pydantic import BaseModel

from .clamav_definitions import DATABASE_ENV, DEVELOPMENT_DATABASE, PREFIXES, ActiveDefinitions, DefinitionSet, certificates_path, read_definitions, selected_definitions, update_root, updater_path
from .scanner import ClamScanner
from .owned_command import run_owned_command
from .writer_lock import application_write_activity

EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
DEFINITION_CHECK_URL = "https://database.clamav.net/"


def internet_available() -> bool:
    """Check the update site's HTTPS reachability without downloading definitions."""
    try:
        with urlopen(Request(DEFINITION_CHECK_URL, method="HEAD"), timeout=3):
            return True
    except HTTPError:
        # Even an HTTP refusal establishes connectivity; FreshClam handles CDN policy.
        return True
    except (OSError, HTTPException):
        return False


def definitions_for_scan(definitions: DefinitionSet, *, now: datetime | None = None,
                         status_callback: Callable[[], None] | None = None) -> DefinitionSet:
    """Refresh old default definitions when online; age or update failure never blocks scanning."""
    if os.environ.get(DATABASE_ENV) or (now or datetime.now(UTC)) <= definitions.daily.published + timedelta(days=1):
        return definitions
    if status_callback is not None:
        status_callback()
    if not internet_available():
        return definitions
    try:
        refresh_definitions(status_callback=status_callback)
        return selected_definitions()
    except (OSError, ValueError, RuntimeError, EOFError) as error:
        logging.getLogger(__name__).warning("Virus definition refresh failed; using existing definitions: %s", error)
        return definitions


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


def validate_definitions(definitions: DefinitionSet, status_callback: Callable[[], None] | None = None) -> None:
    """Verify native loading and real clean/EICAR verdicts before publishing."""
    if definitions.daily.published > datetime.now(UTC):
        raise ValueError("Updated definitions have a future publication date")
    with ClamScanner(status_callback, definitions=definitions) as scanner:
        if scanner.infected(b"From: offline@example.test\nSubject: clean\n\nA clean validation message.\n"):
            raise ValueError("Clean definition-validation fixture was detected as infected")
        verdict = scanner.scan_sample(EICAR)
        if verdict.status != "infected":
            raise ValueError("Updated definitions did not detect EICAR")


def publish_definitions(staging: Path, root: Path, baseline: DefinitionSet | None, *,
                        status_callback: Callable[[], None] | None = None) -> UpdateResult:
    """Caller holds update_lock. A failed validation cannot replace active.json."""
    definitions = read_definitions(staging, "updated")
    if baseline is not None and definitions.daily.version < baseline.daily.version:
        raise ValueError("Refusing to replace definitions with an older daily database")
    validate_definitions(definitions, status_callback)
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
    *, status_callback: Callable[[], None] | None = None,
) -> UpdateResult:
    """Retain updater evidence even when publication already moved staging."""
    files = ", ".join(sorted(path.name for path in staging.iterdir()))
    try:
        return publish_definitions(staging, root, baseline, status_callback=status_callback)
    except (OSError, ValueError, RuntimeError) as error:
        raise RuntimeError(f"Downloaded definitions failed validation or publication: {error}; files: {files}; "
                           f"FreshClam: {diagnostics[-4096:]}") from error


def run_freshclam(staging: Path, configuration: Path,
                  status_callback: Callable[[], None] | None = None) -> str:
    """Share the supervised downloader and failure checks between app and installer updates."""
    write_freshclam_config(configuration, certificates_path(), checks=0)
    result = run_owned_command([str(updater_path()), f"--config-file={configuration}",
        f"--datadir={staging}", "--stdout"], timeout=600, status_callback=status_callback)
    diagnostics = result.stdout + result.stderr
    if result.returncode or "cool-down until" in diagnostics:
        raise RuntimeError(f"Definition update failed ({result.returncode}): {diagnostics[-4096:]}")
    return diagnostics


def publish_definition_directory(staging: Path, destination: Path) -> None:
    """Atomically publish a directory without replacing even an empty existing destination."""
    if os.name == "nt":
        staging.rename(destination)  # Windows rename refuses an existing destination.
        return
    native = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        rename = native.renamex_np
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        result = rename(os.fsencode(staging), os.fsencode(destination), 4)  # RENAME_EXCL
    elif sys.platform.startswith("linux"):
        rename = native.renameat2
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        result = rename(-100, os.fsencode(staging), -100, os.fsencode(destination), 1)  # AT_FDCWD, RENAME_NOREPLACE
    else:
        raise OSError(errno.ENOTSUP, "Exclusive directory publication is unsupported")
    if result:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(destination))


def download_definitions(destination: Path, *, state_directory: Path | None = None) -> UpdateResult:
    """Export verified definitions without reading or replacing the user's active generation."""
    destination = destination.absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError(f"Definition destination already exists: {destination}")
    state_root = (state_directory or destination.parent / f".{destination.name}.clamav-update").absolute()
    output_path, state_path = destination.resolve(), state_root.resolve()
    if output_path.is_relative_to(state_path) or state_path.is_relative_to(output_path):
        raise ValueError("Definition output and state directories must not overlap")
    with application_write_activity():
        destination.parent.mkdir(parents=True, exist_ok=True)
        with update_lock(state_root):
            if destination.exists() or destination.is_symlink():
                raise ValueError(f"Definition destination already exists: {destination}")
            with tempfile.TemporaryDirectory(prefix=f".{destination.name}-", dir=destination.parent) as temporary:
                workspace = Path(temporary)
                staging = workspace / "definitions"
                staging.mkdir()
                state = state_root / "freshclam.dat"
                if state.is_file():
                    shutil.copyfile(state, staging / state.name)
                try:
                    run_freshclam(staging, workspace / "freshclam.conf")
                finally:
                    if (staging / state.name).is_file():
                        shutil.copyfile(staging / state.name, state)
                definitions = read_definitions(staging, "updated")
                validate_definitions(definitions)
                (staging / state.name).unlink(missing_ok=True)
                publish_definition_directory(staging, destination)
                return UpdateResult(versions=definitions.versions, published=definitions.daily.published,
                                    directory=destination)


def refresh_definitions(*, status_callback: Callable[[], None] | None = None) -> UpdateResult:
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
            try:
                diagnostics = run_freshclam(staging, configuration, status_callback)
            finally:
                if (staging / "freshclam.dat").is_file():
                    shutil.copyfile(staging / "freshclam.dat", state)
            return publish_downloaded_definitions(staging, root, baseline, diagnostics,
                                                 status_callback=status_callback)


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


def main(argv: list[str] | None = None, *, require_directory: bool = False) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--seed", type=Path, help="verify and copy an existing local definition set into application storage")
    modes.add_argument("--development", action="store_true", help="update etc/clamdb for development")
    modes.add_argument("--directory", type=Path, help="download verified definitions into a new explicit directory")
    parser.add_argument("--state-directory", type=Path, help="retain downloader cooldown state in this separate directory")
    args = parser.parse_args(argv)
    if require_directory and args.directory is None:
        parser.error("--directory is required for the packaged definition downloader")
    if args.state_directory is not None and args.directory is None:
        parser.error("--state-directory requires --directory")
    try:
        if args.directory is not None:
            result = download_definitions(args.directory, state_directory=args.state_directory)
        elif args.seed is not None:
            result = seed_definitions(args.seed)
        else:
            result = refresh_development() if args.development else refresh_definitions()
    except (OSError, ValueError, RuntimeError, EOFError) as error:
        parser.exit(1, f"Virus definition update failed: {error}\n")
    print(result.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
