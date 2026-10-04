# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Verify Rust reader interoperability with archives produced by Python.
# Existing search fixtures use the production catalog, index, and MBOX writer.
# The compiled reader searches and selects through its actual worker path.
# The test compares all archive files before and after to detect mutations.
# It is headless and never opens a native window or reads personal mail.
# Run through make test-rust-gui-interop after building the experiment binary.

from hashlib import sha256
from pathlib import Path
from os import environ
import subprocess

import pytest

from test_mailsearch import make_archive


def test_rust_reader_displays_python_archive_without_changes(tmp_path: Path) -> None:
    """Rust GUI experiment requirement: search and read the existing canonical format."""
    binary_path = environ.get("RUST_GUI_BINARY")
    if not binary_path:
        pytest.skip("run make test-rust-gui-interop to build and select the Rust reader")
    archive, raw = make_archive(tmp_path)
    before = [(path.relative_to(archive), path.read_bytes()) for path in sorted(archive.rglob("*")) if path.is_file()]
    binary = Path(binary_path)
    result = subprocess.run([str(binary), "--smoke", str(archive), "meeting agenda"],
                            check=False, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert "Subject: planning meeting" in result.stdout
    assert "Meeting agenda." in result.stdout
    assert sha256(raw).hexdigest() in result.stdout
    after = [(path.relative_to(archive), path.read_bytes()) for path in sorted(archive.rglob("*")) if path.is_file()]
    assert after == before
