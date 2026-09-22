# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Verify project-website validation and workflow integrity controls."""

import subprocess
import sys
from pathlib import Path

import pytest
from yaml import safe_load

from scripts.check_website import validate_config, validate_png

ZOLA_SHA256_ARM64 = "303b8e1f3251a6250e47f811eda143316f653c22201faa66777d48ac499c0ee3"
ZOLA_SHA256_X86_64 = "e79edcba2e8d03d22065c9cb8fa2e3abf07b823ef17f00abdc060188dceabba7"
WORKFLOW_ON = "on"
RELEASE = "release"
TYPES = "types"
JOBS = "jobs"
RUNS_ON = "runs-on"


def test_missing_png_reports_a_clear_failure(tmp_path: Path) -> None:
    """Requirement: a missing required icon fails without a file-open traceback."""
    path = tmp_path / "rainbow-post-48.png"

    with pytest.raises(SystemExit, match=f"missing PNG icon: {path}"):
        validate_png(path, 48)


def test_pages_workflow_pins_and_checks_the_zola_archive() -> None:
    """Requirement: Pages must verify the pinned Zola binary before execution."""
    workflow = Path(__file__).parents[1] / ".github/workflows/pages.yml"
    text = workflow.read_text(encoding="utf-8")

    assert f"ZOLA_SHA256_ARM64: {ZOLA_SHA256_ARM64}" in text
    assert f"ZOLA_SHA256_X86_64: {ZOLA_SHA256_X86_64}" in text
    assert "shasum -a 256 --check" in text
    assert "apple-darwin.tar.gz" in text
    configuration = safe_load(text)
    # PyYAML's YAML 1.1 resolver treats an unquoted "on" key as boolean True.
    triggers = configuration.get(WORKFLOW_ON, configuration.get(True))
    assert triggers[RELEASE][TYPES] == ["published"]
    assert configuration[JOBS]["build"]["steps"][0]["with"]["ref"] == "main"


def test_ci_builds_distributions_and_site_and_retains_browser_traces() -> None:
    """Requirement: pull requests validate release/site boundaries and retain browser failures."""
    workflow = Path(__file__).parents[1] / ".github/workflows/continuous-integration.yml"
    text = workflow.read_text(encoding="utf-8")

    assert "run: make distribution-check" in text
    assert "run: make website-build-check" in text
    assert "name: Upload Playwright failure traces" in text
    assert "path: test-results" in text
    configuration = safe_load(text)
    for definition in workflow.parent.glob("*.yml"):
        configuration = safe_load(definition.read_text())
        assert all(job[RUNS_ON] == "macos-15" for job in configuration[JOBS].values()), definition


def test_release_workflow_validates_built_distributions() -> None:
    """Requirement: a tag cannot publish a release without artifact smoke validation."""
    workflow = Path(__file__).parents[1] / ".github/workflows/release.yml"
    text = workflow.read_text(encoding="utf-8")

    assert "run: make distribution-check" in text
    assert "make dmg" in text and "make notarize-dmg" in text and "run: make check-release" not in text
    gates = (
        "name: Verify release commit",
        "name: Verify annotated tag and project version",
        "name: Install dependencies",
        "name: Validate distributions",
        "name: Build source distribution",
        "name: Create draft release",
        "name: Prepare appcast update",
        "name: Sign the final notarized DMG's appcast item",
        "name: Commit appcast and publish release",
    )
    # Validate the release commit and version before installing or building.
    assert [text.index(gate) for gate in gates] == sorted(text.index(gate) for gate in gates)
    makefile = (workflow.parents[2] / "Makefile").read_text(encoding="utf-8")
    assert "uv run --no-project --with packaging --python '>=3.12' python scripts/release_tag.py" in makefile
    signing_step = text[text.index("name: Sign the final notarized DMG's appcast item"):
                        text.index("name: Commit appcast and publish release")]
    assert "SPARKLE_ED25519_PRIVATE_KEY_BASE64" in signing_step
    assert text.index("-f branch=main") < text.index("gh release edit")
    assert "[skip ci]" in text


def test_zola_config_rejects_accidental_template(tmp_path: Path) -> None:
    """Requirement: website validation rejects HTML pasted over Zola TOML."""
    config = tmp_path / "config.toml"
    config.write_text('{% extends "base.html" %}', encoding="utf-8")
    with pytest.raises(SystemExit, match="invalid Zola configuration"):
        validate_config(config)
    config.write_text('base_url = "https://example.org/"', encoding="utf-8")
    validate_config(config)


@pytest.mark.parametrize("failure", ["invalid-utf8", "missing", "directory"])
def test_zola_config_reports_read_failures(tmp_path: Path, failure: str) -> None:
    """Requirement: unreadable or non-UTF-8 configuration fails without a traceback."""
    (tmp_path / "website").mkdir()
    config = tmp_path / "website/config.toml"
    if failure == "invalid-utf8":
        config.write_bytes(b'title = "\xff"')
    elif failure == "directory":
        config.mkdir()
    with pytest.raises(SystemExit, match="invalid Zola configuration") as caught:
        validate_config(config)
    assert str(config) in str(caught.value)
    assert caught.value.__suppress_context__
    result = subprocess.run(
        [sys.executable, str(Path(__file__).parents[1] / "scripts/check_website.py"),
         "--root", str(tmp_path)], capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0
    assert result.stderr.startswith(f"invalid Zola configuration {config}:")
    assert "Traceback" not in result.stderr
