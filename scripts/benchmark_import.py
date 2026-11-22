# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Time a full ``ect-import`` of the synthetic performance corpus.

Purpose: a repeatable import-throughput measurement that can be compared across
commits and machines, so performance regressions and gains are visible.
How it works: generate (or reuse) the seeded corpus from ``perf_corpus.py``,
create a fresh scratch archive, run ``ect-import --no-scan`` as a subprocess,
timestamp each status line to attribute wall time to import phases, and append
one JSON result line (commit, machine, phases, messages/s) to a results file.
System role: backs ``make benchmark-import``; never touches a real archive.
"""

from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent))
from perf_corpus import CorpusManifest, generate  # noqa: E402

ROOT = Path(__file__).parents[1]
DEFAULT_RESULTS = ROOT / "benchmarks" / "import" / "results.jsonl"
PHASE = re.compile(r"^([A-Za-z][A-Za-z ,/-]+?):\s+overall_bytes=")
ARCHIVED = re.compile(r"\barchived=(\d+)")


class BenchmarkResult(BaseModel):
    recorded_at: str
    git_commit: str
    git_dirty: bool
    label: str
    machine: str
    python: str
    cpu_count: int
    workers: str
    corpus_version: int
    corpus_messages: int
    corpus_bytes: int
    archived: int
    wall_seconds: float
    phase_seconds: dict[str, float]
    messages_per_second: float
    exit_code: int


def _git(*arguments: str) -> str:
    return subprocess.run(["git", *arguments], cwd=ROOT, capture_output=True, text=True, check=False).stdout.strip()


def _corpus(directory: Path, megabytes: float, seed: int) -> CorpusManifest:
    manifest_path = directory / "MANIFEST.json"
    if manifest_path.exists():
        manifest = CorpusManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        if manifest.seed == seed and manifest.target_bytes == int(megabytes * 1024 * 1024):
            return manifest
        shutil.rmtree(directory)
    return generate(directory, int(megabytes * 1024 * 1024), seed)


def run(corpus: Path, megabytes: float, seed: int, workers: str | None, label: str, keep: Path | None) -> BenchmarkResult:
    manifest = _corpus(corpus, megabytes, seed)
    scratch = Path(tempfile.mkdtemp(prefix="ect-bench-"))
    archive = (keep or scratch) / "bench.mailarchive"
    if archive.exists():
        shutil.rmtree(archive)
    environment = {**os.environ, "MAIL_ARCHIVE_DIR": ""}
    subprocess.run(["ect-create", str(archive), "--owner", "owner@example.test"],
                   check=True, capture_output=True, env=environment)
    command = ["ect-import", "--archive", str(archive), "--no-scan"]
    if workers:
        command += ["--workers", workers]
    command += [str(corpus / "maildir"), str(corpus / "mbox")]
    phases: dict[str, float] = {}
    archived = 0
    started = last = time.monotonic()
    current = "startup"
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                          env=environment, bufsize=1) as process:
        assert process.stdout is not None
        for line in process.stdout:
            now = time.monotonic()
            phases[current] = phases.get(current, 0.0) + now - last
            last = now
            match = PHASE.match(line)
            if match:
                current = match.group(1).strip().lower()
            if found := ARCHIVED.search(line):
                archived = int(found.group(1))
        exit_code = process.wait()
    finished = time.monotonic()
    phases[current] = phases.get(current, 0.0) + finished - last
    wall = finished - started
    shutil.rmtree(scratch, ignore_errors=True)
    return BenchmarkResult(
        recorded_at=datetime.now(UTC).isoformat(timespec="seconds"),
        git_commit=_git("rev-parse", "--short", "HEAD"),
        git_dirty=bool(_git("status", "--porcelain", "--untracked-files=no")),
        label=label,
        machine=f"{platform.system()} {platform.release()} {platform.machine()}",
        python=platform.python_version(),
        cpu_count=os.cpu_count() or 0,
        workers=workers or "default",
        corpus_version=manifest.corpus_version,
        corpus_messages=manifest.message_count,
        corpus_bytes=manifest.total_bytes,
        archived=archived,
        wall_seconds=round(wall, 2),
        phase_seconds={name: round(value, 2) for name, value in phases.items() if value >= 0.05},
        messages_per_second=round(manifest.message_count / wall, 2) if wall else 0.0,
        exit_code=exit_code,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--corpus", type=Path, default=ROOT / ".tmp" / "perf-corpus")
    parser.add_argument("--megabytes", type=float, default=50.0)
    parser.add_argument("--seed", type=int, default=20261122)
    parser.add_argument("--workers")
    parser.add_argument("--label", default="")
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--keep-archive", type=Path, help="leave the imported archive in this directory")
    parser.add_argument("--no-record", action="store_true", help="print the result without appending it")
    arguments = parser.parse_args()
    result = run(arguments.corpus, arguments.megabytes, arguments.seed, arguments.workers,
                 arguments.label, arguments.keep_archive)
    line = result.model_dump_json()
    print(line)
    if not arguments.no_record:
        arguments.results.parent.mkdir(parents=True, exist_ok=True)
        with arguments.results.open("a", encoding="utf-8") as output:
            output.write(line + "\n")
    if result.exit_code:
        raise SystemExit(result.exit_code)


if __name__ == "__main__":
    main()
