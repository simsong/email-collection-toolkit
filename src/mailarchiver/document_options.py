"""Per-archive owner aliases; never change canonical message classification.

Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import tempfile

from pydantic import BaseModel

from .writer_lock import WriterLease

OWNER_NAMES_FILENAME = "owner-names.txt"
OWNER_NAMES_USED = "owner-names-used.txt"


def sorted_owner_names(names: list[str]) -> list[str]:
    """Preserve multiword file entries; deduplicate case-insensitively."""
    result = []
    seen = set()
    for name in names:
        name = name.strip()
        if not name or name.startswith("#"):
            continue
        if any(character in name for character in "\x00\r\n"):
            raise ValueError("Owner names must contain one entry per line.")
        if name.casefold() not in seen:
            result.append(name)
            seen.add(name.casefold())
    return sorted(result, key=str.casefold)


def split_owner_names(text: str) -> list[str]:
    """The Add control accepts commas, semicolons, and whitespace separators."""
    return sorted_owner_names(re.split(r"[,;\s]+", text))


def read_owner_names(path: Path) -> list[str]:
    try:
        return sorted_owner_names(path.read_text(encoding="utf-8").splitlines())
    except FileNotFoundError:
        return []


def source_owner_names(roots: list[Path]) -> list[str]:
    """Read only the top level of explicitly selected source directories."""
    return sorted_owner_names([
        name for root in roots if root.is_dir()
        for name in read_owner_names(root / OWNER_NAMES_FILENAME)
    ])


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".owner-names-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(text)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class OwnerNamesState(BaseModel):
    names: list[str]
    revision: str
    changed_since_import: bool
    import_names_known: bool
    editable: bool = True


class DocumentOptions:
    """Small document settings store, guarded by the shared archive writer lease."""

    def __init__(self, archive: Path) -> None:
        self.archive = archive
        self.path = archive / OWNER_NAMES_FILENAME
        self.used_path = archive / "status" / OWNER_NAMES_USED

    def state(self) -> OwnerNamesState:
        names = read_owner_names(self.path)
        known = self.used_path.exists()
        previous = read_owner_names(self.used_path) if known else []
        return OwnerNamesState(
            names=names,
            revision=hashlib.sha256("\n".join(names).encode("utf-8")).hexdigest(),
            changed_since_import=known and {name.casefold() for name in names} != {name.casefold() for name in previous},
            import_names_known=known,
        )

    def _check_lease(self, lease: WriterLease) -> None:
        if not lease.acquired or lease.lock_path.parent.parent.resolve() != self.archive.resolve():
            raise ValueError("An active writer lease for this document is required.")

    def save(self, names: list[str], lease: WriterLease, revision: str | None = None) -> OwnerNamesState:
        self._check_lease(lease)
        if revision is not None and revision != self.state().revision:
            raise ValueError("Owner names changed in another window. Reload and try again.")
        names = sorted_owner_names(names)
        _atomic_text(self.path, "".join(name + "\n" for name in names))
        return self.state()

    def merge(self, additions: list[str], lease: WriterLease) -> OwnerNamesState:
        self._check_lease(lease)
        return self.save(self.state().names + additions, lease)

    def record_import(self, names: list[str], lease: WriterLease) -> None:
        """Record aliases actually used by this run, including CLI imports."""
        self._check_lease(lease)
        _atomic_text(self.used_path, "".join(name + "\n" for name in sorted_owner_names(names)))
