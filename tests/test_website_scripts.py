"""Verify project-website validation and workflow integrity controls."""

from pathlib import Path

import pytest

from scripts.check_website import validate_png

ZOLA_SHA256 = "54d1a347781b2f32330914fcc02def81c7e3ddb6111b36d1cc89c06557aed1de"


def test_missing_png_reports_a_clear_failure(tmp_path: Path) -> None:
    """Requirement: a missing required icon fails without a file-open traceback."""
    path = tmp_path / "rainbow-post-48.png"

    with pytest.raises(SystemExit, match=f"missing PNG icon: {path}"):
        validate_png(path, 48)


def test_pages_workflow_pins_and_checks_the_zola_archive() -> None:
    """Requirement: Pages must verify the pinned Zola binary before execution."""
    workflow = Path(__file__).parents[1] / ".github/workflows/pages.yml"
    text = workflow.read_text(encoding="utf-8")

    assert f"ZOLA_SHA256: {ZOLA_SHA256}" in text
    assert "sha256sum --check" in text
    assert "release:\n    types: [published]" in text


def test_ci_builds_distributions_and_site_and_retains_browser_traces() -> None:
    """Requirement: pull requests validate release/site boundaries and retain browser failures."""
    workflow = Path(__file__).parents[1] / ".github/workflows/continuous-integration.yml"
    text = workflow.read_text(encoding="utf-8")

    assert "run: make distribution-check" in text
    assert "run: make website-build-check" in text
    assert "name: Upload Playwright failure traces" in text
    assert "path: test-results" in text


def test_release_workflow_validates_built_distributions() -> None:
    """Requirement: a tag cannot create a draft release without artifact smoke validation."""
    workflow = Path(__file__).parents[1] / ".github/workflows/release.yml"
    text = workflow.read_text(encoding="utf-8")

    assert "run: make distribution-check" in text
