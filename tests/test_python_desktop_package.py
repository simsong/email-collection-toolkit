# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Verify Python desktop restoration through the production packaging entry point.
# Headless checks create real synthetic catalogues, indexes and MBOX messages.
# The reader must find results, offer completions and preserve archived bytes.
# Supported Cargo packages retain maintained tools; the experimental UI is archived.
# Make's dry run verifies that ordinary DMG builds select the Python freezer.
# These checks do not substitute for installed native Windows or DMG CI gates.
"""Python desktop retirement and packaged-reader regression checks."""
from pathlib import Path
import subprocess
import sys
import tomllib

import pytest

from mailarchiver.windows_self_test import WindowsReport, exercise

ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize("entry", [(str(ROOT / "scripts/desktop_entry.py"),),
                                   ("-I", "-m", "mailarchiver.desktop_entry")])
def test_packaged_downloader_requires_an_explicit_new_directory(tmp_path: Path, entry: tuple[str, ...]) -> None:
    """Delivery: both shipped entry points dispatch headlessly and refuse existing output."""
    command = [sys.executable, *entry, "--update-virus-definitions"]
    missing = subprocess.run(command, text=True, capture_output=True, timeout=15, check=False)
    assert missing.returncode == 2 and "--directory is required" in missing.stderr
    destination = tmp_path / "keep-definitions"
    destination.mkdir()
    sentinel = destination / "keep"
    sentinel.write_bytes(b"existing output")
    existing = subprocess.run([*command, "--directory", str(destination)], cwd=tmp_path,
                              text=True, capture_output=True, timeout=15, check=False)
    assert existing.returncode == 1 and "destination already exists" in existing.stderr
    assert list(destination.iterdir()) == [sentinel] and sentinel.read_bytes() == b"existing output"
    assert not (tmp_path / ".keep-definitions.clamav-update").exists()


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
    assert not (ROOT / "rust/mailsearch-gui").exists()
    assert "rust/mailsearch-gui" not in workspace["members"]
    for package in ("rust/archive-verifier", "rust/mct-importer", "rust/mime-transfer", "pst"):
        assert package in workspace["members"]
    command = subprocess.run(["make", "-n", "dmg"], cwd=ROOT, text=True, capture_output=True, check=True)
    assert "scripts/build_macos.py" in command.stdout
    assert "--rust-binary" not in command.stdout and "mailsearch-rust" not in command.stdout
    help_text = subprocess.run([sys.executable, ROOT / "scripts/build_macos.py", "--help"],
                               cwd=ROOT, text=True, capture_output=True, check=True)
    assert "--rust-binary" not in help_text.stdout
