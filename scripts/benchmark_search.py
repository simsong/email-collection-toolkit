# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Time desktop text search end to end against an existing archive, read-only.

Purpose: a repeatable check that a word search shows its first page quickly and
completes its count, so search-latency regressions are visible across commits.
How it works: for each query, a fresh Python process (like a newly launched app)
calls the same ``gui_service.search_count`` and ``search_page`` the desktop app
uses, first once and then again warm. ``--vocabulary-cache empty`` points the
process at an empty vocabulary cache to time a never-before-searched index.
Results are appended as JSON lines; ``--check`` fails when a goal is missed.
System role: backs ``make benchmark-search``; it only opens archives read-only.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

ROOT = Path(__file__).parents[1]
DEFAULT_RESULTS = ROOT / "benchmarks" / "search" / "results.jsonl"
FIRST_PAGE_GOAL_SECONDS = 1.0
SEARCH_GOAL_SECONDS = 5.0


class QueryTiming(BaseModel):
    query: str
    matches: int | None
    page_rows: int
    first_page_seconds: float
    first_count_seconds: float
    warm_page_seconds: float
    warm_count_seconds: float


class BenchmarkResult(BaseModel):
    recorded_at: str
    git_commit: str
    git_dirty: bool
    label: str
    machine: str
    python: str
    vocabulary_cache: str
    archive_messages: int
    queries: list[QueryTiming]
    first_page_goal_met: bool
    search_goal_met: bool


def _git(*arguments: str) -> str:
    return subprocess.run(["git", *arguments], cwd=ROOT, capture_output=True, text=True, check=False).stdout.strip()


def _timed(function, *arguments):  # type: ignore[no-untyped-def]
    started = time.perf_counter()
    value = function(*arguments)
    return value, round(time.perf_counter() - started, 3)


def child(archive: Path, query: str) -> QueryTiming:
    """Run in a fresh process: the page the app shows first, then the count, then both warm."""
    from mailarchiver.gui_service import search_count, search_page

    page, first_page = _timed(search_page, archive, query)
    count, first_count = _timed(search_count, archive, query)
    _, warm_page = _timed(search_page, archive, query)
    _, warm_count = _timed(search_count, archive, query)
    return QueryTiming(query=query, matches=count.total, page_rows=len(page.results),
                       first_page_seconds=first_page, first_count_seconds=first_count,
                       warm_page_seconds=warm_page, warm_count_seconds=warm_count)


def run(archive: Path, queries: list[str], label: str, empty_cache: bool) -> BenchmarkResult:
    from mailarchiver.gui_service import searchable_message_count

    timings: list[QueryTiming] = []
    with tempfile.TemporaryDirectory(prefix="ect-search-vocabulary-") as empty:
        environment = dict(os.environ)
        if empty_cache:
            environment["MAILARCHIVER_SEARCH_VOCABULARY_CACHE"] = empty
        for query in queries:
            if empty_cache:
                for stored in Path(empty).glob("*"):
                    stored.unlink()
            output = subprocess.run(
                [sys.executable, __file__, "--child", "--archive", str(archive), query],
                check=True, capture_output=True, text=True, env=environment,
            ).stdout
            timings.append(QueryTiming.model_validate_json(output.strip().splitlines()[-1]))
    return BenchmarkResult(
        recorded_at=datetime.now(UTC).isoformat(timespec="seconds"),
        git_commit=_git("rev-parse", "--short", "HEAD"),
        git_dirty=bool(_git("status", "--porcelain", "--untracked-files=no")),
        label=label,
        machine=f"{platform.system()} {platform.release()} {platform.machine()}",
        python=platform.python_version(),
        vocabulary_cache="empty" if empty_cache else "stored",
        archive_messages=searchable_message_count(archive),
        queries=timings,
        first_page_goal_met=all(item.first_page_seconds < FIRST_PAGE_GOAL_SECONDS for item in timings),
        search_goal_met=all(item.first_page_seconds + item.first_count_seconds < SEARCH_GOAL_SECONDS
                            for item in timings),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("queries", nargs="*", default=["gandalf"])
    parser.add_argument("--label", default="")
    parser.add_argument("--vocabulary-cache", choices=("stored", "empty"), default="stored")
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--check", action="store_true", help="exit 1 when a latency goal is missed")
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    arguments = parser.parse_args()
    archive = arguments.archive.expanduser()
    if arguments.child:
        print(child(archive, arguments.queries[0]).model_dump_json())
        return
    result = run(archive, arguments.queries, arguments.label, arguments.vocabulary_cache == "empty")
    arguments.results.parent.mkdir(parents=True, exist_ok=True)
    with arguments.results.open("a", encoding="utf-8") as handle:
        handle.write(result.model_dump_json() + "\n")
    print(json.dumps(result.model_dump(), indent=2))
    if arguments.check and not (result.first_page_goal_met and result.search_goal_met):
        sys.exit(1)


if __name__ == "__main__":
    main()
