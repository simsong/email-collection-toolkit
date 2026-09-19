# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""Offline ClamAV resources and immutable per-user definition generations."""
from __future__ import annotations

import calendar
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .identity import APPLICATION_NAME

DATABASE_NAMES = ("main", "daily", "bytecode")
LIBRARY_ENV = "MAILARCHIVER_CLAMAV_LIBRARY"
DATABASE_ENV = "MAILARCHIVER_CLAMAV_DATABASE"
UPDATE_ENV = "MAILARCHIVER_CLAMAV_UPDATES"
UPDATER_ENV = "MAILARCHIVER_FRESHCLAM"
CERTIFICATES_ENV = "MAILARCHIVER_CLAMAV_CERTIFICATES"
DEVELOPMENT_DATABASE = Path(__file__).resolve().parents[2] / "etc/clamdb"
PREFIXES = (Path("/opt/homebrew"), Path("/usr/local"), Path("/usr/local/clamav"), Path("/usr"))


class DefinitionFile(BaseModel):
    model_config = ConfigDict(frozen=True)
    path: Path
    version: int = Field(ge=0)
    published: datetime


class DefinitionSet(BaseModel):
    model_config = ConfigDict(frozen=True)
    directory: Path
    source: Literal["bundled", "updated", "development"]
    files: tuple[DefinitionFile, ...]

    @property
    def daily(self) -> DefinitionFile:
        return next(item for item in self.files if item.path.stem == "daily")

    @property
    def versions(self) -> str:
        return ",".join(f"{item.path.stem}:{item.version}" for item in self.files)


class ActiveDefinitions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    generation: str


def bundle_root() -> Path | None:
    """PyInstaller exposes Resources through its private bundle directory."""
    return Path(getattr(sys, "_MEIPASS")) / "clamav" if getattr(sys, "frozen", False) else None


def update_root() -> Path:
    if override := os.environ.get(UPDATE_ENV):
        return Path(override)
    if sys.platform == "darwin":
        root = Path.home() / "Library/Application Support"
    elif sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local")))
    else:
        root = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
    return root / APPLICATION_NAME / "clamav"


def library_path() -> Path:
    if override := os.environ.get(LIBRARY_ENV):
        return Path(override)
    name = "libclamav.dylib" if sys.platform == "darwin" else "libclamav.dll" if sys.platform == "win32" else "libclamav.so"
    if root := bundle_root():
        return root / name
    candidates = [prefix / "lib" / name for prefix in PREFIXES]
    candidates += list(Path("/usr/lib").glob(f"*-linux-gnu/{name}*"))
    return next((path for path in candidates if path.is_file()), candidates[0])


def updater_path() -> Path:
    if override := os.environ.get(UPDATER_ENV):
        return Path(override)
    name = "freshclam.exe" if sys.platform == "win32" else "freshclam"
    if root := bundle_root():
        return root / name
    return next((prefix / "bin" / name for prefix in PREFIXES if (prefix / "bin" / name).is_file()), PREFIXES[0] / "bin" / name)


def certificates_path() -> Path | None:
    if override := os.environ.get(CERTIFICATES_ENV):
        return Path(override)
    if root := bundle_root():
        return root / "certs"
    candidates = [prefix / "etc/clamav/certs" for prefix in PREFIXES]
    return next((path for path in candidates if path.is_dir()), None)


def read_definitions(directory: Path, source: Literal["bundled", "updated", "development"]) -> DefinitionSet:
    """Read bounded CVD/CLD headers; engine loading separately verifies authenticity."""
    files = []
    for name in DATABASE_NAMES:
        present = [directory / (name + suffix) for suffix in (".cvd", ".cld") if (directory / (name + suffix)).is_file()]
        if len(present) != 1:
            raise ValueError(f"Expected exactly one {name}.cvd or {name}.cld in {directory}")
        path = present[0]
        with path.open("rb") as handle:
            fields = handle.read(512).decode("ascii").strip().split(":")
        if len(fields) < 9 or fields[0] != "ClamAV-VDB":
            raise ValueError(f"Invalid ClamAV definition header: {path}")
        files.append(DefinitionFile(path=path, version=int(fields[2]), published=datetime.fromtimestamp(int(fields[8]), UTC)))
    return DefinitionSet(directory=directory, source=source, files=tuple(files))


def bundled_definitions() -> DefinitionSet:
    if override := os.environ.get(DATABASE_ENV):
        return read_definitions(Path(override), "development")
    if root := bundle_root():
        return read_definitions(root / "definitions", "bundled")
    return read_definitions(DEVELOPMENT_DATABASE, "development")


class DefinitionSelection(BaseModel):
    definitions: DefinitionSet
    warning: str = ""


def choose_definitions(baseline: DefinitionSet, root: Path) -> DefinitionSelection:
    manifest = root / "active.json"
    if not manifest.is_file():
        return DefinitionSelection(definitions=baseline)
    try:
        active = ActiveDefinitions.model_validate_json(manifest.read_bytes())
        if not active.generation or Path(active.generation).name != active.generation or active.generation in (".", ".."):
            raise ValueError("Invalid definition generation")
        directory = root / "generations" / active.generation
        if not directory.resolve().is_relative_to((root / "generations").resolve()):
            raise ValueError("Definition generation escapes update directory")
        updated = read_definitions(directory, "updated")
    except (OSError, ValueError, OverflowError) as error:
        return DefinitionSelection(definitions=baseline, warning=f"Updated definitions unavailable; using baseline: {error}")
    return DefinitionSelection(definitions=updated if updated.daily.version > baseline.daily.version else baseline)


def definition_selection() -> DefinitionSelection:
    baseline = bundled_definitions()
    # Explicit development/test database overrides must be deterministic.
    return DefinitionSelection(definitions=baseline) if os.environ.get(DATABASE_ENV) else choose_definitions(baseline, update_root())


def selected_definitions() -> DefinitionSet:
    return definition_selection().definitions


def three_months_after(published: datetime) -> datetime:
    month = published.month - 1 + 3
    year = published.year + month // 12
    month = month % 12 + 1
    return published.replace(year=year, month=month, day=min(published.day, calendar.monthrange(year, month)[1]))
