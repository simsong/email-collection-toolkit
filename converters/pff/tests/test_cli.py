# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""Source-safety requirement: receipt/diagnostic destinations cannot overwrite input."""
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("option", ["--receipt", "--diagnostics"])
def test_refuses_source_as_output_even_through_hardlink(tmp_path: Path, option: str) -> None:
    source = tmp_path / "source.pst"
    source.write_bytes(b"private source bytes")
    destination = tmp_path / "output.json"
    os.link(source, destination)
    result = subprocess.run([sys.executable, "-m", "pff_converter.cli", option, str(destination), str(source)],
                            capture_output=True, check=False)
    assert result.returncode == 1 and b"distinct from the source" in result.stderr
    assert source.read_bytes() == b"private source bytes"


def test_refuses_receipt_and_diagnostics_hardlinked_to_each_other(tmp_path: Path) -> None:
    """Distinct evidence destinations must not alias even when neither aliases the source."""
    source = tmp_path / "source.pst"
    source.write_bytes(b"invalid but immutable PST fixture")
    receipt = tmp_path / "receipt.json"
    receipt.write_bytes(b"existing evidence")
    diagnostics = tmp_path / "diagnostics.txt"
    os.link(receipt, diagnostics)
    result = subprocess.run([sys.executable, "-m", "pff_converter.cli", "--receipt", str(receipt),
                             "--diagnostics", str(diagnostics), str(source)],
                            capture_output=True, check=False, timeout=15)
    assert result.returncode == 1 and b"distinct from the source and each other" in result.stderr
    assert receipt.read_bytes() == diagnostics.read_bytes() == b"existing evidence"
    assert source.read_bytes() == b"invalid but immutable PST fixture"
