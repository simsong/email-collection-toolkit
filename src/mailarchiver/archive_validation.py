# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Revalidate a whole archive on request and write a readable HTML report.
# Routine checkpoints reread only MBOX files changed since the previous one;
# this is the explicit full pass the user starts from File > Revalidate Archive.
# Every file in the archive is listed. Each MBOX's catalogued messages are read
# and checked against their recorded SHA-256, and each file's digest is compared
# with the BagIt payload or tag manifest that lists it. It never writes to the
# archive; the report goes to the system temporary directory.
"""Read-only whole-archive validation with an HTML report."""

from __future__ import annotations

import hashlib
import html
import sqlite3
import tempfile
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from .bagit import PAYLOAD_MANIFEST, TAG_MANIFEST
from .layout import mbox_directory
from .mbox import MboxLocation, read_verified_location

ManifestStatus = Literal["matches", "differs", "not listed", "listed but missing"]
REPORT_DIRECTORY_NAME = "Email Collection Toolkit validation"


class ValidationStopped(Exception):
    """The user closed the archive or quit while validation was running."""


class FileValidation(BaseModel):
    """One file in the archive and what validation found."""

    path: str
    byte_count: int
    manifest: ManifestStatus
    messages: int | None = None
    validated: int | None = None
    redacted: int | None = None
    failures: list[str] = []


class ArchiveValidation(BaseModel):
    """Everything the HTML report shows."""

    archive: str
    started_at: datetime
    finished_at: datetime
    files: list[FileValidation]

    @property
    def messages(self) -> int:
        return sum(item.messages or 0 for item in self.files)

    @property
    def validated(self) -> int:
        return sum(item.validated or 0 for item in self.files)

    @property
    def problems(self) -> int:
        return sum(1 for item in self.files if item.failures or item.manifest in {"differs", "listed but missing"})


def _read_manifest(path: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    if not path.is_file():
        return entries
    for line in path.read_text(encoding="utf-8").splitlines():
        digest, separator, name = line.partition("  ")
        if separator:
            entries[name.replace("%0A", "\n").replace("%0D", "\r").replace("%25", "%")] = digest
    return entries


def _sha256(path: Path, should_stop: Callable[[], bool]) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(4 * 1024 * 1024), b""):
            if should_stop():
                raise ValidationStopped("validation stopped")
            digest.update(block)
    return digest.hexdigest()


def validate_archive(
    archive: Path,
    *,
    should_stop: Callable[[], bool] = lambda: False,
    progress: Callable[[str], None] = lambda _message: None,
) -> ArchiveValidation:
    """Check every file and every catalogued message; never modify the archive."""
    started = datetime.now(UTC)
    manifests = {**_read_manifest(archive / TAG_MANIFEST), **_read_manifest(archive / PAYLOAD_MANIFEST)}
    seen: set[str] = set()
    files: list[FileValidation] = []
    catalog_uri = f"{(archive / 'archive.sqlite3').resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(catalog_uri, uri=True)) as catalog:
        paths = sorted(path for path in archive.rglob("*") if path.is_file() and not path.is_symlink())
        for number, path in enumerate(paths, 1):
            relative = path.relative_to(archive).as_posix()
            progress(f"Validating {relative} ({number:,} of {len(paths):,} files)")
            seen.add(relative)
            expected = manifests.get(relative)
            status: ManifestStatus = "not listed"
            if expected is not None:
                status = "matches" if _sha256(path, should_stop) == expected else "differs"
            entry = FileValidation(path=relative, byte_count=path.stat().st_size, manifest=status)
            if path.parent == mbox_directory(archive) and path.suffix == ".mbox":
                entry.messages, entry.validated, entry.redacted = 0, 0, 0
                rows = catalog.execute(
                    "SELECT l.byte_offset, l.byte_length, m.sha256, m.message_pk FROM locations l "
                    "JOIN messages m USING (message_pk) JOIN mbox_generations g USING (generation_pk) "
                    "WHERE g.filename = ? ORDER BY l.byte_offset", (path.name,))
                for offset, length, sha256, message_pk in rows:
                    if should_stop():
                        raise ValidationStopped("validation stopped")
                    entry.messages += 1
                    try:
                        read_verified_location(path, MboxLocation(byte_offset=offset, byte_length=length), sha256)
                        entry.validated += 1
                    except (OSError, ValueError) as error:
                        entry.failures.append(f"message {message_pk} at byte {offset}: {error}")
            files.append(entry)
    for missing in sorted(set(manifests) - seen):
        files.append(FileValidation(path=missing, byte_count=0, manifest="listed but missing"))
    return ArchiveValidation(archive=str(archive), started_at=started, finished_at=datetime.now(UTC), files=files)


def _cell(value: object) -> str:
    return "<td>—</td>" if value is None else f"<td class=\"number\">{value:,}</td>" if isinstance(value, int) else f"<td>{html.escape(str(value))}</td>"


def render_report(report: ArchiveValidation) -> str:
    """Return a self-contained HTML page for the report."""
    rows = []
    for item in report.files:
        problem = item.failures or item.manifest in {"differs", "listed but missing"}
        rows.append(
            f"<tr class=\"{'problem' if problem else ''}\"><td class=\"path\">{html.escape(item.path)}</td>"
            f"{_cell(item.byte_count)}{_cell(item.messages)}{_cell(item.validated)}{_cell(item.redacted)}"
            f"<td>{html.escape(item.manifest)}</td></tr>")
        for failure in item.failures[:50]:
            rows.append(f"<tr class=\"problem detail\"><td colspan=\"6\">{html.escape(failure)}</td></tr>")
        if len(item.failures) > 50:
            rows.append(f"<tr class=\"problem detail\"><td colspan=\"6\">… and {len(item.failures) - 50:,} more</td></tr>")
    verdict = ("No problems found." if not report.problems else
               f"{report.problems:,} file{'s' if report.problems != 1 else ''} with problems.")
    elapsed = (report.finished_at - report.started_at).total_seconds()
    local = report.started_at.astimezone()
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Archive validation report</title>
<style>
:root {{ color-scheme: light dark; font: 14px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
body {{ margin: 24px; }}
h1 {{ font-size: 20px; margin: 0 0 4px; }}
p {{ margin: 4px 0; }}
.verdict {{ font-weight: 650; margin: 14px 0; }}
table {{ border-collapse: collapse; width: 100%; margin-top: 12px; }}
th, td {{ padding: 5px 8px; border-bottom: 1px solid color-mix(in srgb, CanvasText 18%, transparent); text-align: left; vertical-align: top; }}
th {{ position: sticky; top: 0; background: Canvas; }}
.number {{ text-align: right; font-variant-numeric: tabular-nums; }}
.path {{ font-family: ui-monospace, Menlo, monospace; overflow-wrap: anywhere; }}
.problem {{ color: #c43b35; }}
.detail td {{ padding-left: 24px; font-size: 12px; }}
</style></head><body>
<h1>Archive validation report</h1>
<p>{html.escape(report.archive)}</p>
<p>Started {local:%Y-%m-%d %H:%M:%S %Z}; took {elapsed:,.0f} s.</p>
<p class="verdict">{html.escape(verdict)} {report.validated:,} of {report.messages:,} messages validated.</p>
<p>Messages validate when their bytes match the SHA-256 recorded at import. Manifest compares each file with
the archive's BagIt manifests; operational files such as the SQLite databases are not listed there.
Redaction is not implemented yet, so no messages are redacted.</p>
<table><thead><tr><th>File</th><th class="number">Bytes</th><th class="number">Messages</th>
<th class="number">Validated</th><th class="number">Redacted</th><th>Manifest</th></tr></thead>
<tbody>
{chr(10).join(rows)}
</tbody></table></body></html>
"""


def write_report(report: ArchiveValidation, directory: Path | None = None) -> Path:
    """Write the HTML report under the temporary directory and return its path."""
    folder = directory or Path(tempfile.gettempdir()) / REPORT_DIRECTORY_NAME
    folder.mkdir(parents=True, exist_ok=True)
    name = Path(report.archive).name or "archive"
    destination = folder / f"{name} validation {report.started_at.astimezone():%Y-%m-%d %H%M%S}.html"
    destination.write_text(render_report(report), encoding="utf-8")
    return destination


__all__ = ["ArchiveValidation", "FileValidation", "ValidationStopped", "render_report", "validate_archive",
           "write_report"]
