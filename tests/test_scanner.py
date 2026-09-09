"""Requirement: ClamAV health checks and message scans have hard subprocess deadlines."""

from __future__ import annotations

from pathlib import Path

import pytest

from mailarchiver.scanner import ClamScanner


def sleeping_executable(path: Path) -> Path:
    """Create a real subprocess that does not finish before a short test deadline."""
    path.write_text("#!/bin/sh\nexec sleep 10\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_clamav_health_probe_has_a_hard_deadline(tmp_path: Path) -> None:
    """Requirement: a stuck health probe cannot consume the daemon startup deadline."""
    scanner = ClamScanner(
        clamdscan=str(sleeping_executable(tmp_path / "sleeping-clamdscan")),
        ping_timeout_seconds=0.05,
    )

    assert not scanner.available()


def test_clamav_scan_timeout_cleans_up_message_bytes(tmp_path: Path) -> None:
    """Requirement: a stuck scan fails and removes its plaintext temporary message."""
    scan_directory = tmp_path / "scan"
    scan_directory.mkdir()
    scanner = ClamScanner(
        clamdscan=str(sleeping_executable(tmp_path / "sleeping-clamdscan")),
        scan_timeout_seconds=0.05,
        scan_temporary_directory=scan_directory,
    )

    with pytest.raises(RuntimeError, match=r"clamdscan timed out after 0\.05 seconds"):
        scanner.infected(b"From: sender@example.test\n\nprivate body\n")

    assert list(scan_directory.iterdir()) == []
