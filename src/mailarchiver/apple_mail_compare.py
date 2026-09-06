"""Compare complete Apple Mail EMLX records with a Mail Archiver archive read-only."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sqlite3
import sys
from collections import Counter
from itertools import groupby
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import BaseModel, ConfigDict, Field

from .layout import mbox_path
from .mbox import MboxLocation, read_verified_location
from .sources import emlx_bytes
from .standalone_verify import FIELD_NAME_PATTERN, semantic_bytes


SEMANTIC_STANDARD = "h3 semantic-message v1"
DEDUPLICATION_STANDARD = "normalized Message-ID + h2 raw-message SHA-256"
HEADER_SEPARATOR = b"\r\n\r\n"
LINE_ENDINGS = re.compile(rb"\r\n|\r|\n")


class HeaderChange(BaseModel):
    """Aggregate one header-name difference without disclosing header values."""

    name: str
    messages: int = Field(ge=0)
    occurrences: int = Field(ge=0)


class ComparisonReport(BaseModel):
    """Read-only Apple Mail/cache reconciliation results."""

    apple_mail_root: str
    archive_root: str
    comparison_identity: str = SEMANTIC_STANDARD
    deduplication_identity: str = DEDUPLICATION_STANDARD
    complete_emlx: int = 0
    partial_emlx: int = 0
    unreadable_emlx: int = 0
    compared_emlx: int = 0
    archive_messages: int = 0
    exact_raw_matches: int = 0
    semantic_only_matches: int = 0
    cache_only_messages: int = 0
    archive_semantic_matches: int = 0
    archive_only_messages: int = 0
    ambiguous_semantic_matches: int = 0
    header_pairs_analyzed: int = 0
    formatting_only_pairs: int = 0
    source_changed_during_scan: bool = False
    apple_added_headers: list[HeaderChange] = Field(default_factory=list)
    apple_missing_headers: list[HeaderChange] = Field(default_factory=list)
    changed_headers: list[HeaderChange] = Field(default_factory=list)


class ArchiveCandidate(BaseModel):
    """One canonical archive location selected by a semantic digest."""

    model_config = ConfigDict(frozen=True)

    message_pk: int
    raw_sha256: str
    filename: str
    byte_offset: int
    byte_length: int


class CacheInventory(BaseModel):
    """Counts produced while populating the temporary comparison index."""

    complete: int = 0
    partial: int = 0
    unreadable: int = 0


def _mail_state(root: Path) -> tuple[tuple[str, int, int], ...]:
    markers: list[tuple[str, int, int]] = []
    for path in sorted(root.glob("V*/MailData/Envelope Index-wal")):
        stat = path.stat()
        markers.append((str(path), stat.st_size, stat.st_mtime_ns))
    return tuple(markers)


def _walk_error(error: OSError) -> None:
    raise error


def _index_cache(
    database: sqlite3.Connection,
    root: Path,
    progress_every: int,
) -> CacheInventory:
    inventory = CacheInventory()
    inserts: list[tuple[str, str, str]] = []
    for directory, directories, filenames in os.walk(root, onerror=_walk_error, followlinks=False):
        directories.sort()
        for filename in sorted(filenames):
            lowered = filename.lower()
            if not lowered.endswith(".emlx"):
                continue
            path = Path(directory) / filename
            if path.is_symlink():
                continue
            if lowered.endswith(".partial.emlx"):
                inventory.partial += 1
                continue
            inventory.complete += 1
            try:
                raw = emlx_bytes(path)
            except (OSError, ValueError):
                inventory.unreadable += 1
                continue
            inserts.append(
                (
                    path.relative_to(root).as_posix(),
                    hashlib.sha256(raw).hexdigest(),
                    hashlib.sha256(semantic_bytes(raw)).hexdigest(),
                )
            )
            if len(inserts) == 1_000:
                database.executemany(
                    "INSERT INTO cache_messages(relative_path, raw_sha256, semantic_sha256) VALUES (?, ?, ?)",
                    inserts,
                )
                database.commit()
                inserts.clear()
            if progress_every and inventory.complete % progress_every == 0:
                print(f"indexed {inventory.complete:,} complete EMLX records", file=sys.stderr)
    if inserts:
        database.executemany(
            "INSERT INTO cache_messages(relative_path, raw_sha256, semantic_sha256) VALUES (?, ?, ?)",
            inserts,
        )
        database.commit()
    return inventory


def _relaxed_headers(raw: bytes) -> Counter[tuple[str, bytes]]:
    normalized = LINE_ENDINGS.sub(b"\r\n", raw)
    header_block = normalized.partition(HEADER_SEPARATOR)[0]
    fields: list[tuple[str, bytes]] = []
    current_name: str | None = None
    current_value = b""
    for line in header_block.split(b"\r\n"):
        if line.startswith((b" ", b"\t")) and current_name is not None:
            current_value += b"\r\n" + line
            continue
        if current_name is not None:
            fields.append((current_name, current_value))
            current_name = None
        name, marker, value = line.partition(b":")
        if marker and FIELD_NAME_PATTERN.fullmatch(name):
            current_name = name.decode("ascii").lower()
            current_value = value
    if current_name is not None:
        fields.append((current_name, current_value))
    return Counter(
        (name, re.sub(rb"[ \t]+", b" ", re.sub(rb"\r\n[ \t]+", b" ", value)).strip(b" \t"))
        for name, value in fields
    )


def _header_delta(
    apple: Counter[tuple[str, bytes]],
    archived: Counter[tuple[str, bytes]],
) -> tuple[Counter[str], Counter[str], Counter[str]]:
    added_pairs, missing_pairs = apple - archived, archived - apple
    added = Counter[str]()
    missing = Counter[str]()
    for (name, _value), count in added_pairs.items():
        added[name] += count
    for (name, _value), count in missing_pairs.items():
        missing[name] += count
    changed = Counter[str]()
    for name in added.keys() & missing.keys():
        changed[name] = min(added[name], missing[name])
        added[name] -= changed[name]
        missing[name] -= changed[name]
    return +added, +missing, +changed


def _changes(occurrences: Counter[str], messages: Counter[str]) -> list[HeaderChange]:
    return [
        HeaderChange(name=name, occurrences=count, messages=messages[name])
        for name, count in sorted(occurrences.items(), key=lambda item: (-item[1], item[0]))
    ]


def _scalar(database: sqlite3.Connection, statement: str) -> int:
    row = database.execute(statement).fetchone()
    if row is None:
        raise RuntimeError("aggregate query returned no row")
    return int(row[0])


def _candidate(row: tuple[object, ...]) -> ArchiveCandidate:
    return ArchiveCandidate(
        message_pk=int(row[4]),
        raw_sha256=str(row[5]),
        filename=str(row[6]),
        byte_offset=int(row[7]),
        byte_length=int(row[8]),
    )


def _analyze_headers(
    database: sqlite3.Connection,
    apple_root: Path,
    archive_root: Path,
) -> tuple[int, int, list[HeaderChange], list[HeaderChange], list[HeaderChange]]:
    statement = """
        SELECT c.cache_pk, c.relative_path, c.raw_sha256, c.semantic_sha256,
               m.message_pk, m.sha256, g.filename, l.byte_offset, l.byte_length
        FROM cache_messages AS c
        JOIN archive.observations AS o ON o.semantic_sha256 = c.semantic_sha256
        JOIN archive.messages AS m ON m.message_pk = o.message_pk
        JOIN archive.locations AS l ON l.message_pk = m.message_pk
        JOIN archive.mbox_generations AS g ON g.generation_pk = l.generation_pk
        WHERE NOT EXISTS (
            SELECT 1 FROM archive.messages AS exact WHERE exact.sha256 = c.raw_sha256
        )
        GROUP BY c.cache_pk, m.message_pk
        ORDER BY c.cache_pk, m.message_pk
    """
    added_occurrences = Counter[str]()
    missing_occurrences = Counter[str]()
    changed_occurrences = Counter[str]()
    added_messages = Counter[str]()
    missing_messages = Counter[str]()
    changed_messages = Counter[str]()
    analyzed = formatting_only = 0
    for _cache_pk, rows_iter in groupby(database.execute(statement), key=lambda row: int(row[0])):
        rows = list(rows_iter)
        relative_path = str(rows[0][1])
        apple_raw = emlx_bytes(apple_root / relative_path)
        apple_headers = _relaxed_headers(apple_raw)
        best: tuple[int, int, Counter[str], Counter[str], Counter[str]] | None = None
        best_key: tuple[int, int] | None = None
        for row in rows:
            candidate = _candidate(row)
            archived_raw = read_verified_location(
                mbox_path(archive_root, candidate.filename),
                MboxLocation(byte_offset=candidate.byte_offset, byte_length=candidate.byte_length),
                candidate.raw_sha256,
            )
            if hashlib.sha256(semantic_bytes(archived_raw)).hexdigest() != str(row[3]):
                raise RuntimeError(f"stale semantic digest for archive message {candidate.message_pk}")
            added, missing, changed = _header_delta(apple_headers, _relaxed_headers(archived_raw))
            score = sum(added.values()) + sum(missing.values()) + sum(changed.values())
            choice = (score, candidate.message_pk, added, missing, changed)
            if best_key is None or choice[:2] < best_key:
                best = choice
                best_key = choice[:2]
        if best is None:
            continue
        analyzed += 1
        _score, _message_pk, added, missing, changed = best
        if not added and not missing and not changed:
            formatting_only += 1
        for source, occurrence_target, message_target in (
            (added, added_occurrences, added_messages),
            (missing, missing_occurrences, missing_messages),
            (changed, changed_occurrences, changed_messages),
        ):
            occurrence_target.update(source)
            message_target.update(source.keys())
    return (
        analyzed,
        formatting_only,
        _changes(added_occurrences, added_messages),
        _changes(missing_occurrences, missing_messages),
        _changes(changed_occurrences, changed_messages),
    )


def compare_apple_mail(
    apple_mail_root: Path,
    archive_root: Path,
    *,
    progress_every: int = 10_000,
) -> ComparisonReport:
    """Return a content-private, read-only comparison of two mail stores."""
    apple_mail_root = apple_mail_root.expanduser().resolve()
    archive_root = archive_root.expanduser().resolve()
    catalog_path = archive_root / "archive.sqlite3"
    if not apple_mail_root.is_dir():
        raise FileNotFoundError(f"Apple Mail directory not found: {apple_mail_root}")
    if not catalog_path.is_file():
        raise FileNotFoundError(f"archive catalog not found: {catalog_path}")
    before = _mail_state(apple_mail_root)
    with TemporaryDirectory(prefix="mailarchiver-apple-compare-") as temporary:
        database = sqlite3.connect(Path(temporary) / "comparison.sqlite3", uri=True)
        try:
            database.executescript(
                """
                CREATE TABLE cache_messages (
                    cache_pk INTEGER PRIMARY KEY,
                    relative_path TEXT NOT NULL UNIQUE,
                    raw_sha256 TEXT NOT NULL,
                    semantic_sha256 TEXT NOT NULL
                );
                CREATE INDEX cache_raw_sha256 ON cache_messages(raw_sha256);
                CREATE INDEX cache_semantic_sha256 ON cache_messages(semantic_sha256);
                """
            )
            inventory = _index_cache(database, apple_mail_root, progress_every)
            database.execute(
                "ATTACH DATABASE ? AS archive",
                (catalog_path.as_uri() + "?mode=ro",),
            )
            if database.execute("SELECT version FROM archive.schema_info").fetchall() != [(1,)]:
                raise RuntimeError("unsupported archive catalog schema")
            archive_messages = _scalar(database, "SELECT count(*) FROM archive.messages")
            exact = _scalar(
                database,
                "SELECT count(*) FROM cache_messages AS c WHERE EXISTS "
                "(SELECT 1 FROM archive.messages AS m WHERE m.sha256 = c.raw_sha256)",
            )
            semantic_only = _scalar(
                database,
                "SELECT count(*) FROM cache_messages AS c WHERE NOT EXISTS "
                "(SELECT 1 FROM archive.messages AS m WHERE m.sha256 = c.raw_sha256) AND EXISTS "
                "(SELECT 1 FROM archive.observations AS o WHERE o.message_pk IS NOT NULL "
                "AND o.semantic_sha256 = c.semantic_sha256)",
            )
            compared = _scalar(database, "SELECT count(*) FROM cache_messages")
            archive_matches = _scalar(
                database,
                "SELECT count(DISTINCT o.message_pk) FROM archive.observations AS o "
                "JOIN cache_messages AS c ON c.semantic_sha256 = o.semantic_sha256 "
                "WHERE o.message_pk IS NOT NULL",
            )
            ambiguous = _scalar(
                database,
                "SELECT count(*) FROM (SELECT c.cache_pk FROM cache_messages AS c "
                "JOIN archive.observations AS o ON o.semantic_sha256 = c.semantic_sha256 "
                "WHERE o.message_pk IS NOT NULL GROUP BY c.cache_pk "
                "HAVING count(DISTINCT o.message_pk) > 1)",
            )
            analyzed, formatting, added, missing, changed = _analyze_headers(
                database, apple_mail_root, archive_root
            )
        finally:
            database.close()
    after = _mail_state(apple_mail_root)
    return ComparisonReport(
        apple_mail_root=str(apple_mail_root),
        archive_root=str(archive_root),
        complete_emlx=inventory.complete,
        partial_emlx=inventory.partial,
        unreadable_emlx=inventory.unreadable,
        compared_emlx=compared,
        archive_messages=archive_messages,
        exact_raw_matches=exact,
        semantic_only_matches=semantic_only,
        cache_only_messages=compared - exact - semantic_only,
        archive_semantic_matches=archive_matches,
        archive_only_messages=archive_messages - archive_matches,
        ambiguous_semantic_matches=ambiguous,
        header_pairs_analyzed=analyzed,
        formatting_only_pairs=formatting,
        source_changed_during_scan=before != after,
        apple_added_headers=added,
        apple_missing_headers=missing,
        changed_headers=changed,
    )


def _print_changes(label: str, changes: list[HeaderChange]) -> None:
    print(f"{label}:")
    if not changes:
        print("  (none)")
        return
    for change in changes:
        print(f"  {change.name}: {change.occurrences:,} occurrences in {change.messages:,} messages")


def print_report(report: ComparisonReport) -> None:
    """Print a content-private human-readable report."""
    print(f"Comparison identity: {report.comparison_identity}")
    print(f"Archive deduplication identity: {report.deduplication_identity}")
    print(f"Complete EMLX: {report.complete_emlx:,}")
    print(f"Partial EMLX excluded: {report.partial_emlx:,}")
    print(f"Unreadable complete EMLX: {report.unreadable_emlx:,}")
    print(f"Archive messages: {report.archive_messages:,}")
    print(f"Exact raw matches: {report.exact_raw_matches:,}")
    print(f"Semantic-only matches: {report.semantic_only_matches:,}")
    print(f"Cache-only complete messages: {report.cache_only_messages:,}")
    print(f"Archive messages represented in cache: {report.archive_semantic_matches:,}")
    print(f"Archive-only messages: {report.archive_only_messages:,}")
    print(f"Ambiguous semantic matches: {report.ambiguous_semantic_matches:,}")
    print(f"Header pairs analyzed: {report.header_pairs_analyzed:,}")
    print(f"Formatting-only raw differences: {report.formatting_only_pairs:,}")
    print(f"Apple Mail changed during scan: {'yes' if report.source_changed_during_scan else 'no'}")
    _print_changes("Headers present only in Apple Mail", report.apple_added_headers)
    _print_changes("Headers absent from Apple Mail", report.apple_missing_headers)
    _print_changes("Headers with changed normalized values", report.changed_headers)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apple-mail", type=Path, default=Path.home() / "Library/Mail")
    parser.add_argument("--archive", type=Path, default=Path.home() / "mail-archive")
    parser.add_argument("--progress-every", type=int, default=10_000)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.progress_every < 0:
        parser.error("--progress-every must be nonnegative")
    try:
        report = compare_apple_mail(args.apple_mail, args.archive, progress_every=args.progress_every)
    except (OSError, sqlite3.Error, RuntimeError, ValueError) as error:
        parser.exit(1, f"comparison failed: {error}\n")
    if args.json:
        print(report.model_dump_json(indent=2))
    else:
        print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
