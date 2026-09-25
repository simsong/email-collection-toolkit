# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Verify project-website validation and workflow integrity controls."""

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from yaml import safe_load

from scripts.check_appcast import check_appcast
from scripts.check_website import validate_config, validate_png
from scripts.update_appcast import AppcastRelease, SignedArchive, append_item
from scripts.update_site_releases import choose_preview

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
    assert triggers["push"]["branches"] == ["main"]
    assert "workflow_call" in triggers
    assert RELEASE not in triggers
    assert configuration[JOBS]["build"]["steps"][0]["with"]["ref"] == "main"


def test_ci_runs_each_repository_branch_push_once_without_building_a_dmg() -> None:
    """Requirement: every non-main branch push runs all tests without duplicate PR or DMG work."""
    workflow = Path(__file__).parents[1] / ".github/workflows/continuous-integration.yml"
    text = workflow.read_text(encoding="utf-8")

    assert "run: make distribution-check" in text
    assert "run: make website-build-check" in text
    assert "name: Upload Playwright failure traces" in text
    assert "path: test-results" in text
    configuration = safe_load(text)
    triggers = configuration.get(WORKFLOW_ON, configuration.get(True))
    assert triggers == {"push": {"branches": ["**", "!main"]}}
    assert "dmg-smoke" not in configuration[JOBS]
    assert any("make check" in step.get("run", "") for step in configuration[JOBS]["pytest"]["steps"])
    for definition in workflow.parent.glob("*.yml"):
        configuration = safe_load(definition.read_text())
        assert all(job.get(RUNS_ON) == "macos-15" or "uses" in job for job in configuration[JOBS].values()), definition


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
        "name: Attach appcast and publish release",
    )
    # Validate the release commit and version before installing or building.
    assert [text.index(gate) for gate in gates] == sorted(text.index(gate) for gate in gates)
    makefile = (workflow.parents[2] / "Makefile").read_text(encoding="utf-8")
    assert "uv run --no-project --with packaging --python '>=3.12' python scripts/release_tag.py" in makefile
    signing_step = text[text.index("name: Sign the final notarized DMG's appcast item"):
                        text.index("name: Attach appcast and publish release")]
    assert "SPARKLE_ED25519_PRIVATE_KEY_BASE64" in signing_step
    assert text.index('gh release upload "$RELEASE_TAG" "$APPCAST"') < text.index("gh release edit")
    configuration = safe_load(text)
    triggers = configuration.get(WORKFLOW_ON, configuration.get(True))
    assert triggers == {"push": {"tags": ["v*"]}}
    assert configuration[JOBS]["pages"]["needs"] == "assemble"
    assert configuration[JOBS]["pages"]["uses"] == "./.github/workflows/pages.yml"
    assert "git merge-base --is-ancestor HEAD refs/remotes/origin/main" in text
    assert "gh workflow run pages.yml" not in text
    pages = (workflow.parent / "pages.yml").read_text(encoding="utf-8")
    assert 'select(.draft == false) | .tag_name' in pages
    assert pages.index("gh release download") < pages.index("name: Build Zola site")
    assert "actions/download-artifact@" in pages
    assert 'if [[ -z "$appcast_tag" ]]; then' in pages
    assert 'if [[ -z "$previous_tag" ]]; then' in text
    assert '"$(git tag --list \'v*\')" != "$RELEASE_TAG"' in text


def test_appcast_gate_rejects_missing_and_unsigned_release_items(tmp_path: Path) -> None:
    """Requirement: Pages cannot silently serve a seed or unsigned feed after publication."""
    appcast = tmp_path / "appcast.xml"
    appcast.write_text('<rss><channel><title>Updates</title></channel></rss>', encoding="utf-8")
    with pytest.raises(ValueError, match="exactly one signed item"):
        check_appcast(appcast, "v1.0.0a10")
    append_item(appcast, AppcastRelease(tag="v1.0.0a10", channel="preview", sparkle_version=1000000110,
                                       display_version="1.0.0a10",
                                       url="https://github.com/simsong/email-collection-toolkit/releases/download/"
                                           "v1.0.0a10/example.dmg",
                                       archive=SignedArchive(signature="signed", length=123)))
    check_appcast(appcast, "v1.0.0a10")
    appcast.write_text(appcast.read_text().replace('sparkle:edSignature="signed"', ''), encoding="utf-8")
    with pytest.raises(ValueError, match="unsigned"):
        check_appcast(appcast, "v1.0.0a10")


@pytest.mark.parametrize("url", [
    "https://evil.example/releases/download/v1.0.0a10/example.dmg",
    "https://github.com.evil.example/simsong/email-collection-toolkit/releases/download/v1.0.0a10/example.dmg",
    "https://github.com/simsong/other/releases/download/v1.0.0a10/example.dmg",
    "https://github.com/simsong/email-collection-toolkit/releases/download/v1.0.0a9/example.dmg",
    "https://github.com/simsong/email-collection-toolkit/releases/download/v1.0.0a10/../example.dmg",
    "https://github.com/simsong/email-collection-toolkit/releases/download/v1.0.0a10/%2e%2e%2fexample.dmg",
    "https://github.com/simsong/email-collection-toolkit/releases/download/v1.0.0a10/example.dmg?next=evil",
])
def test_appcast_gate_rejects_unrelated_or_ambiguous_downloads(tmp_path: Path, url: str) -> None:
    """Requirement: a published feed cannot redirect Sparkle away from this tagged DMG."""
    appcast = tmp_path / "appcast.xml"
    appcast.write_text('<rss><channel><title>Updates</title></channel></rss>', encoding="utf-8")
    append_item(appcast, AppcastRelease(tag="v1.0.0a10", channel="preview", sparkle_version=1000000110,
                                       display_version="1.0.0a10", url=url,
                                       archive=SignedArchive(signature="signed", length=123)))
    with pytest.raises(ValueError, match="invalid archive enclosure"):
        check_appcast(appcast, "v1.0.0a10")


def test_site_links_select_published_pep440_previews(tmp_path: Path) -> None:
    """Requirement: the site links to published alpha/beta tags, not failed or legacy tags."""
    output = tmp_path / "releases.toml"
    script = Path(__file__).parents[1] / "scripts/update_site_releases.py"
    result = subprocess.run([sys.executable, str(script), "--output", str(output)],
                            input="v1.0.0a2\nv1.0.0b1\nv1.0.0a10\nv1.0.0-beta9\n",
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    releases = tomllib.loads(output.read_text(encoding="utf-8"))
    assert releases["preview"]["tag"] == "v1.0.0b1"
    assert releases["current_version"] == "v1.0.0b1"
    assert releases["stable"]["tag"] == ""
    assert choose_preview(["v1.0.0a2", "v1.0.0a10"]) == "v1.0.0a10"


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
