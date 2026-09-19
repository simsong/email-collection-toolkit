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
