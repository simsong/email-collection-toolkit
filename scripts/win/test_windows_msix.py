# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Validate the relocated Python MSIX through its frozen application entry point.
# Unpack an unsigned development payload without installing or trusting it.
# The application creates a synthetic archive and exercises real reader APIs.
# Its report records FTS, autocomplete and byte-preservation checks.
# Installed package activation and upgrade are checked by the separate CI VM.
# No private mail, certificates or machine configuration participate here.
"""Relocated Python MSIX acceptance."""
import argparse
from pathlib import Path
import subprocess
import zipfile

from mailarchiver.windows_self_test import WindowsReport


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("package", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    unpacked = output / "relocated"
    with zipfile.ZipFile(args.package) as package:
        package.extractall(unpacked)
    checks = output / "checks"
    subprocess.run([unpacked / "ect.exe", "--msix-test", checks], check=True, timeout=60, cwd=output)
    report = WindowsReport.model_validate_json((checks / "report.json").read_text())
    assert report.implementation == "python" and report.search_count == 1 and report.completion_count > 0


if __name__ == "__main__":
    main()
