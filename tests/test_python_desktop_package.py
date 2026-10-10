# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Verify Python desktop restoration through the production packaging entry point.
# Headless checks create real synthetic catalogues, indexes and MBOX messages.
# The reader must find results, offer completions and preserve archived bytes.
# Supported Cargo packages must exclude the retired GUI while retaining tools.
# Make's dry run verifies that ordinary DMG builds select the Python freezer.
# These checks do not substitute for installed native Windows or DMG CI gates.
"""Python desktop retirement and packaged-reader regression checks."""
from pathlib import Path
import subprocess
import sys
import tomllib

from mailarchiver.windows_self_test import WindowsReport, exercise

ROOT = Path(__file__).parents[1]


def test_packaged_reader_uses_production_search_and_completion(tmp_path: Path) -> None:
    report = exercise(tmp_path / "direct")
    assert report.implementation == "python"
    assert report.search_count == 1 and report.completion_count > 0
    assert report.processor_count > 0
    assert report.archive_sha256
    output = tmp_path / "entry"
    subprocess.run([sys.executable, ROOT / "scripts/desktop_entry.py", "--msix-test", output],
                   check=True, timeout=30)
    dispatched = WindowsReport.model_validate_json((output / "report.json").read_text())
    assert dispatched.search_count == report.search_count
    assert dispatched.archive_sha256 == report.archive_sha256
    isolated = tmp_path / "isolated"
    subprocess.run([sys.executable, "-I", "-m", "mailarchiver.desktop_entry", "--msix-test", isolated],
                   check=True, timeout=30)
    activated = WindowsReport.model_validate_json((isolated / "report.json").read_text())
    assert activated.installed_msix_tested and activated.processor_count == report.processor_count
    assert activated.archive_sha256 == report.archive_sha256


def test_supported_builds_select_python_and_keep_rust_tools() -> None:
    workspace = tomllib.loads((ROOT / "Cargo.toml").read_text())["workspace"]
    assert "rust/mailsearch-gui" in workspace["exclude"]
    assert "rust/mailsearch-gui" not in workspace["members"]
    for package in ("rust/archive-verifier", "rust/mct-importer", "rust/mime-transfer", "pst"):
        assert package in workspace["members"]
    command = subprocess.run(["make", "-n", "dmg"], cwd=ROOT, text=True, capture_output=True, check=True)
    assert "scripts/build_macos.py" in command.stdout
    assert "--rust-binary" not in command.stdout and "mailsearch-rust" not in command.stdout
    retired = subprocess.run(["make", "rust-gui-retired"], cwd=ROOT, text=True, capture_output=True, check=False)
    assert retired.returncode != 0 and "Rust GUI retired" in retired.stderr
