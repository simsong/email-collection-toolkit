# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Exercise File > Revalidate Archive's read-only validation and HTML report.
# A real ingest builds the archive; the tests then damage copies of its files
# and check that the report lists every file, counts messages per MBOX, flags
# altered messages and manifest mismatches, and never writes to the archive.
# Redaction is planned but not implemented, so its count must read zero.
"""Requirement: an explicit whole-archive validation reports every file and message."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from mailarchiver.__main__ import IngestRequest, run_ingest
from mailarchiver.archive_validation import ValidationStopped, render_report, validate_archive, write_report
from mailarchiver.owner_rules import OwnerRules


def build_archive(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    source.mkdir()
    for year in (2001, 2002):
        (source / f"{year}.eml").write_bytes(
            f"From: owner@example.test\nTo: friend@example.test\nSubject: year {year}\n"
            f"Date: Mon, 1 Jan {year} 12:00:00 +0000\nMessage-ID: <m{year}@example.test>\n\nbody {year}\n".encode())
    archive = tmp_path / "archive"
    run_ingest(IngestRequest(archive=archive, roots=[str(source)], scan_policy="not-scanned",
                             owner_rules=OwnerRules(include=["owner@example.test"]), continue_content=False), terminal=False)
    return archive


def snapshot(archive: Path) -> dict[str, str]:
    return {path.relative_to(archive).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in archive.rglob("*") if path.is_file()}


def test_validation_lists_every_file_and_counts_messages(tmp_path: Path) -> None:
    archive = build_archive(tmp_path)
    before = snapshot(archive)
    report = validate_archive(archive)
    assert snapshot(archive) == before, "validation must not write to the archive"
    assert {item.path for item in report.files} == set(before)
    boxes = [item for item in report.files if item.path.startswith("data/mbox/")]
    assert boxes and all(item.manifest == "matches" and item.redacted == 0 for item in boxes)
    assert report.messages == report.validated == 2 and report.problems == 0
    assert next(item for item in report.files if item.path == "archive.sqlite3").manifest == "not listed"
    destination = write_report(report, tmp_path / "reports")
    page = destination.read_text(encoding="utf-8")
    assert destination.suffix == ".html" and "No problems found." in page and "2 of 2 messages validated" in page
    for item in report.files:
        assert item.path in page


def test_validation_flags_altered_messages_and_manifests(tmp_path: Path) -> None:
    archive = build_archive(tmp_path)
    box = sorted((archive / "data" / "mbox").glob("*.mbox"))[0]
    data = box.read_bytes()
    box.write_bytes(data.replace(b"body ", b"BODY ", 1))
    report = validate_archive(archive)
    damaged = next(item for item in report.files if item.path == f"data/mbox/{box.name}")
    assert damaged.manifest == "differs" and damaged.validated == 0 and damaged.failures
    assert report.problems == 1 and report.validated == report.messages - 1
    assert "problem" in render_report(report)


def test_validation_stops_when_asked(tmp_path: Path) -> None:
    archive = build_archive(tmp_path)
    with pytest.raises(ValidationStopped):
        validate_archive(archive, should_stop=lambda: True)
