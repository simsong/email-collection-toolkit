"""Requirement: ClamAV health checks and message scans have hard subprocess deadlines."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="ClamScanner uses POSIX fcntl locking and these deadline fixtures require /bin/sh",
)


def sleeping_executable(path: Path) -> Path:
    """Create a real subprocess that does not finish before a short test deadline."""
    path.write_text("#!/bin/sh\nexec sleep 10\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_clamav_health_probe_has_a_hard_deadline(tmp_path: Path) -> None:
    """Requirement: a stuck health probe cannot consume the daemon startup deadline."""
    from mailarchiver.scanner import ClamScanner

    scanner = ClamScanner(
        clamdscan=str(sleeping_executable(tmp_path / "sleeping-clamdscan")),
        ping_timeout_seconds=0.05,
    )

    assert not scanner.available()


@pytest.mark.parametrize("helper_state", ["missing", "not-executable", "invalid-executable"])
def test_clamav_health_probe_execution_failure_is_unavailable(tmp_path: Path, helper_state: str) -> None:
    """Requirement: OS execution errors cannot escape the scanner readiness probe."""
    from mailarchiver.scanner import ClamScanner

    helper = tmp_path / "clamdscan"
    if helper_state != "missing":
        helper.write_text("not an executable format\n", encoding="utf-8")
        helper.chmod(0o755 if helper_state == "invalid-executable" else 0o644)

    assert not ClamScanner(clamdscan=str(helper)).available()


def test_clamav_scan_timeout_cleans_up_message_bytes(tmp_path: Path) -> None:
    """Requirement: a stuck scan fails and removes its plaintext temporary message."""
    from mailarchiver.scanner import ClamScanner

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
