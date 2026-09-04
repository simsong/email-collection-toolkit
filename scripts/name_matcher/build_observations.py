"""Build a private, disposable identity-evidence database from a mail archive."""

from __future__ import annotations

import argparse
import concurrent.futures
import os
import sqlite3
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from mailarchiver.layout import mbox_path
from mailarchiver.mbox import MboxLocation, read_verified_location

from scripts.name_matcher.observations import (
    EXTRACTOR_NAME,
    EXTRACTOR_VERSION,
    create_database,
    extract_message_evidence,
    store_message,
)


REPOSITORY = Path(__file__).parents[2]
SCHEMA = REPOSITORY / "doc" / "research" / "name-matcher" / "prototype_schema.sql"


class GenerationWork(BaseModel):
    archive: Path
    filename: str
    message_limit: int | None = Field(default=None, ge=1)
    shard: Path


class GenerationResult(BaseModel):
    filename: str
    shard: Path
    messages: int = Field(ge=0)
    errors: int = Field(ge=0)


def open_catalog(archive: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{archive / 'archive.sqlite3'}?mode=ro", uri=True, timeout=30)


def generation_plan(archive: Path, shard_directory: Path, limit: int | None) -> list[GenerationWork]:
    plan: list[GenerationWork] = []
    remaining = limit
    with open_catalog(archive) as catalog:
        rows = catalog.execute("SELECT filename, message_count FROM mbox_generations ORDER BY filename")
        for ordinal, (filename, message_count) in enumerate(rows):
            if remaining is not None and remaining <= 0:
                break
            selected = int(message_count) if remaining is None else min(int(message_count), remaining)
            if selected <= 0:
                continue
            plan.append(
                GenerationWork(
                    archive=archive,
                    filename=str(filename),
                    message_limit=selected,
                    shard=shard_directory / f"{ordinal:04d}.sqlite3",
                )
            )
            if remaining is not None:
                remaining -= selected
    return plan


def extract_generation(work: GenerationWork) -> GenerationResult:
    output = create_database(work.shard, SCHEMA)
    messages = 0
    errors = 0
    query = (
        "SELECT m.message_pk, m.sha256, m.date_utc, l.byte_offset, l.byte_length "
        "FROM messages m JOIN locations l USING (message_pk) "
        "JOIN mbox_generations g USING (generation_pk) WHERE g.filename = ? "
        "ORDER BY l.byte_offset LIMIT ?"
    )
    with open_catalog(work.archive) as catalog:
        rows = catalog.execute(query, (work.filename, work.message_limit))
        for message_pk, digest, observed_at, byte_offset, byte_length in rows:
            try:
                raw = read_verified_location(
                    mbox_path(work.archive, work.filename),
                    MboxLocation(byte_offset=int(byte_offset), byte_length=int(byte_length)),
                    str(digest),
                )
                evidence = extract_message_evidence(
                    raw,
                    catalog_message_pk=int(message_pk),
                    observed_at=str(observed_at),
                    mbox_filename=work.filename,
                    expected_sha256=str(digest),
                )
                store_message(output, evidence)
                messages += 1
            except Exception as error:  # Retain a typed defect instead of dropping it silently.
                output.execute(
                    "INSERT INTO extraction_errors(catalog_message_pk, message_sha256, stage, error_type, detail) "
                    "VALUES (?, ?, 'message-evidence', ?, ?)",
                    (int(message_pk), str(digest), type(error).__name__, str(error)),
                )
                errors += 1
            if (messages + errors) % 1000 == 0:
                output.commit()
    output.commit()
    output.close()
    return GenerationResult(filename=work.filename, shard=work.shard, messages=messages, errors=errors)


def merge_shard(output: sqlite3.Connection, shard: Path) -> None:
    output.execute("ATTACH DATABASE ? AS shard", (str(shard),))
    try:
        for table in (
            "messages",
            "header_observations",
            "message_markers",
            "signature_blocks",
            "signature_facts",
            "extraction_errors",
        ):
            output.execute(f"INSERT INTO main.{table} SELECT * FROM shard.{table}")
        output.commit()
    finally:
        output.execute("DETACH DATABASE shard")


def database_summary(database: sqlite3.Connection) -> dict[str, int]:
    return {
        "messages": int(database.execute("SELECT COUNT(*) FROM messages").fetchone()[0]),
        "errors": int(database.execute("SELECT COUNT(*) FROM extraction_errors").fetchone()[0]),
        "header_observations": int(database.execute("SELECT COUNT(*) FROM header_observations").fetchone()[0]),
        "message_markers": int(database.execute("SELECT COUNT(*) FROM message_markers").fetchone()[0]),
        "distinct_addresses": int(database.execute("SELECT COUNT(DISTINCT address) FROM header_observations").fetchone()[0]),
        "distinct_nonempty_names": int(
            database.execute(
                "SELECT COUNT(DISTINCT normalized_name) FROM header_observations WHERE normalized_name <> ''"
            ).fetchone()[0]
        ),
        "signature_blocks": int(database.execute("SELECT COUNT(*) FROM signature_blocks").fetchone()[0]),
        "signature_facts": int(database.execute("SELECT COUNT(*) FROM signature_facts").fetchone()[0]),
    }


def build_database(archive: Path, output_path: Path, *, workers: int, limit: int | None) -> dict[str, int]:
    summary_path = output_path.with_suffix(output_path.suffix + ".summary.yaml")
    if output_path.exists() or summary_path.exists():
        existing = output_path if output_path.exists() else summary_path
        raise FileExistsError(f"refusing to overwrite {existing}")
    if not (archive / "archive.sqlite3").is_file():
        raise FileNotFoundError(f"missing archive catalog: {archive / 'archive.sqlite3'}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc).isoformat()
    run_id = str(uuid.uuid4())
    with tempfile.TemporaryDirectory(prefix="name-matcher-shards-", dir=output_path.parent) as temporary:
        shard_directory = Path(temporary)
        plan = generation_plan(archive, shard_directory, limit)
        temporary_output = shard_directory / "combined.sqlite3"
        output = create_database(temporary_output, SCHEMA)
        config = {"limit": limit, "workers": workers, "signature_extractor": "heuristic-v1"}
        output.execute(
            "INSERT INTO extraction_runs(run_id, started_at, archive_catalog, extractor_name, extractor_version, "
            "configuration_yaml) VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, started, str(archive / "archive.sqlite3"), EXTRACTOR_NAME, EXTRACTOR_VERSION, yaml.safe_dump(config)),
        )
        if workers == 1:
            results = [extract_generation(item) for item in plan]
        else:
            with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as executor:
                results = list(executor.map(extract_generation, plan))
        for result in results:
            merge_shard(output, result.shard)
        completed = datetime.now(timezone.utc).isoformat()
        output.execute("UPDATE extraction_runs SET completed_at = ? WHERE run_id = ?", (completed, run_id))
        summary = database_summary(output)
        output.commit()
        output.close()
        os.replace(temporary_output, output_path)
    summary_path.write_text(yaml.safe_dump({"run_id": run_id, **summary}, sort_keys=False), encoding="utf-8")
    summary_path.chmod(0o600)
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True, help="read-only Mail Archiver root")
    parser.add_argument("--output", type=Path, required=True, help="new private SQLite evidence database")
    parser.add_argument("--workers", type=int, default=max(1, min(4, os.cpu_count() or 1)))
    parser.add_argument("--limit", type=int, help="optional global message limit for a bounded experiment")
    args = parser.parse_args(argv)
    if args.workers < 1:
        parser.error("--workers must be at least 1")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        summary = build_database(args.archive.resolve(), args.output.resolve(), workers=args.workers, limit=args.limit)
    except (FileExistsError, FileNotFoundError, ValueError) as error:
        print(error, file=sys.stderr)
        return 2
    print(yaml.safe_dump(summary, sort_keys=False), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
