# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Verify project-website validation and workflow integrity controls."""

import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from yaml import safe_load

from scripts.check_appcast import MAX_FEED_BYTES, check_appcast
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
STEPS = "steps"
RUN = "run"
NEEDS = "needs"


def test_missing_png_reports_a_clear_failure(tmp_path: Path) -> None:
    """Requirement: a missing required icon fails without a file-open traceback."""
    path = tmp_path / "rainbow-post-48.png"

    with pytest.raises(SystemExit, match=re.escape(f"missing PNG icon: {path}")):
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
    assert "workflow_dispatch" in triggers
    assert triggers["workflow_dispatch"]["inputs"]["release_tag"]["required"] is False
    assert "workflow_call" not in triggers
    assert RELEASE not in triggers
    assert configuration[JOBS]["build"]["steps"][0]["with"]["ref"] == "main"


def test_ci_runs_parallel_branch_jobs_without_building_a_dmg() -> None:
    """Requirement: each non-main push runs independent static and test jobs without a DMG."""
    workflow = Path(__file__).parents[1] / ".github/workflows/continuous-integration.yml"
    text = workflow.read_text(encoding="utf-8")

    assert "run: make distribution-check" in text
    assert "run: make website-build-check" in text
    assert "name: Upload Playwright failure traces" in text
    assert "path: test-results" in text
    configuration = safe_load(text)
    triggers = configuration.get(WORKFLOW_ON, configuration.get(True))
    assert triggers == {"push": {"branches": ["**", "!main"]}}
    jobs = configuration[JOBS]
    assert set(jobs) == {"static-rust", "python-browser", "rust-gui", "rust-reader"}
    assert all(NEEDS not in job for job in jobs.values())
    static_runs = [step.get(RUN, "") for step in jobs["static-rust"][STEPS]]
    test_runs = [step.get(RUN, "") for step in jobs["python-browser"][STEPS]]
    assert any("make check-static" in run for run in static_runs)
    assert any("make check-tests" in run for run in test_runs)
    assert all("make check-tests" not in run for run in static_runs)
    assert all("make check-static" not in run for run in test_runs)
    native = jobs["rust-gui"]
    assert native["strategy"]["matrix"] == {"os": ["macos-latest"]}
    assert native[RUNS_ON] == "${{ matrix.os }}"
    native_runs = [step.get(RUN, "") for step in native[STEPS]]
    assert native_runs.count("make test-rust-gui-native") == 1
    assert all("rust-gui-build" not in run and "test-rust-gui " not in f"{run} "
               and "check-static" not in run for run in native_runs)
    upload = next(step for step in native[STEPS] if step.get("uses", "").startswith("actions/upload-artifact@"))
    assert upload["if"] == "always()"
    assert upload["with"]["path"] == ".tmp/rust-gui-native"
    assert upload["with"]["include-hidden-files"] is True
    assert upload["with"]["if-no-files-found"] == "error"
    makefile = (workflow.parents[2] / "Makefile").read_text(encoding="utf-8")
    assert "check:\n\t$(MAKE) check-static\n\t$(MAKE) check-tests" in makefile
    for definition in workflow.parent.glob("*.yml"):
        if definition.name == "rust-reader.yml":
            continue  # Its explicit multi-platform build matrix is checked below.
        configuration = safe_load(definition.read_text())
        for name, job in configuration[JOBS].items():
            if definition == workflow and name == "rust-gui":
                continue  # The explicit macos-latest matrix is checked above.
            assert job.get(RUNS_ON) == "macos-15" or "uses" in job, definition


def test_cargo_reader_builds_gate_branch_and_tag_workflows() -> None:
    """Windows delivery: both architectures must build/test and retain executable artifacts."""
    workflows = Path(__file__).parents[1] / ".github/workflows"
    ci = safe_load((workflows / "continuous-integration.yml").read_text(encoding="utf-8"))
    opt_in = ci[JOBS]["rust-reader"]
    assert opt_in["if"] == "contains(github.event.head_commit.message, '[windows-ci]')"
    assert opt_in["uses"] == "./.github/workflows/rust-reader.yml"
    assert opt_in["with"] == {"windows_only": True}
    release = safe_load((workflows / "release.yml").read_text(encoding="utf-8"))
    assert release[JOBS]["rust-reader"]["uses"] == "./.github/workflows/rust-reader.yml"
    assert "rust-reader" in release[JOBS]["assemble"][NEEDS]
    reader = safe_load((workflows / "rust-reader.yml").read_text(encoding="utf-8"))
    job = reader[JOBS]["reader"]
    assert job[RUNS_ON] == "${{ matrix.os }}"
    assert job["strategy"]["matrix"]["os"] == (
        "${{ fromJSON(inputs.windows_only && '[\"windows-latest\",\"windows-11-arm\"]'"
        " || '[\"windows-latest\",\"windows-11-arm\",\"macos-latest\"]') }}"
    )
    runs = [step.get(RUN, "") for step in job[STEPS]]
    assert runs.index("cargo reader-check") < runs.index("cargo reader-build --release")
    windows_upload = next(step for step in job[STEPS]
                          if step.get("if") == "runner.os == 'Windows'" and step.get("uses", "").startswith("actions/upload-artifact@"))
    assert windows_upload["uses"].startswith("actions/upload-artifact@")
    assert "target/release/mailsearch-webview.exe" in windows_upload["with"]["path"]
    assert windows_upload["with"]["if-no-files-found"] == "error"


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
        "name: Prepare appcast update",
        "name: Sign the final notarized DMG's appcast item",
        "name: Validate signed appcast",
        "name: Create complete draft release",
        "name: Publish complete release",
        "name: Dispatch Pages deployment from main",
    )
    # Validate the release commit and version before installing or building.
    assert [text.index(gate) for gate in gates] == sorted(text.index(gate) for gate in gates)
    makefile = (workflow.parents[2] / "Makefile").read_text(encoding="utf-8")
    assert "uv run --no-project --with packaging --python '>=3.12' python scripts/release_tag.py" in makefile
    signing_step = text[text.index("name: Sign the final notarized DMG's appcast item"):
                        text.index("name: Validate signed appcast")]
    assert "SPARKLE_ED25519_PRIVATE_KEY_BASE64" in signing_step
    assert text.index('dist/* "$APPCAST"') < text.index('gh release edit "$RELEASE_TAG"')
    configuration = safe_load(text)
    macos_steps = [step["name"] for step in configuration[JOBS]["macos"]["steps"]]
    assert macos_steps.index("Verify Sparkle release signer") < macos_steps.index("Verify signed update history")
    assert macos_steps.index("Verify signed update history") < macos_steps.index("Build DMG, list mounted contents, and run headless self-test")
    ci = safe_load((workflow.parent / "continuous-integration.yml").read_text(encoding="utf-8"))
    assert any(step.get("run") == "make test-sparkle-signing" for step in ci[JOBS]["python-browser"]["steps"])
    triggers = configuration.get(WORKFLOW_ON, configuration.get(True))
    assert triggers == {"push": {"tags": ["v*"]}}
    assert set(configuration[JOBS]) == {"assemble", "macos", "rust-reader", "preflight"}
    assert configuration[JOBS]["macos"][NEEDS] == "preflight"
    assert configuration[JOBS]["rust-reader"][NEEDS] == "preflight"
    assert "git merge-base --is-ancestor HEAD refs/remotes/origin/main" in text
    assert ('gh workflow run pages.yml --repo "$GITHUB_REPOSITORY" '
            '--ref main -f release_tag="$RELEASE_TAG"') in text
    assert configuration[JOBS]["assemble"]["permissions"]["actions"] == "write"
    pages = (workflow.parent / "pages.yml").read_text(encoding="utf-8")
    assert 'select(.draft == false) | .tag_name' in pages
    assert 'appcast_tag="$RELEASE_TAG"' in pages
    assert pages.index("gh release download") < pages.index("name: Build Zola site")
    assert "actions/download-artifact@" not in pages
    assert 'if [[ -z "$appcast_tag" ]]; then' in pages
    history = (workflow.parents[2] / "scripts/fetch_release_appcast.sh").read_text(encoding="utf-8")
    assert 'if [[ -z "$previous_tag" ]]; then' in history
    assert '"$(git tag --list \'v*\')" != "$candidate_tag"' in history
    previous_gate = 'make check-appcast APPCAST="$appcast_path" RELEASE_TAG="$previous_tag" ARGS=--require-signed-feed'
    assert previous_gate.replace('$appcast_path', '$output') in history
    assert text.index('make release-appcast-base APPCAST="$appcast_path"') < text.index("name: Sign the final notarized DMG's appcast item")
    pages_configuration = safe_load(pages)
    validation_runs = [step.get("run", "") for step in pages_configuration[JOBS]["build"]["steps"]]
    validation_runs = [run for run in validation_runs if "make check-appcast" in run]
    assert len(validation_runs) == 1
    assert all("ARGS=--require-signed-feed" in run for run in validation_runs)


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


@pytest.mark.parametrize("tamper", ["content", "length", "signature", "public-key", "trailing", "duplicate"])
def test_signed_feed_gate_authenticates_bytes_not_marker_text(tmp_path: Path, tamper: str) -> None:
    """Issue #91: Pages rejects forged blocks, wrong keys, length edits and post-signing changes."""
    import base64
    from Cryptodome.Signature import eddsa

    key = eddsa.import_private_key(bytes(range(32)))
    public = base64.b64encode(key.public_key().export_key(format="raw")).decode("ascii")
    content = b"<rss><channel><title>Fixture</title></channel></rss>"
    signature = base64.b64encode(eddsa.new(key, "rfc8032").sign(content))
    block = b"<!-- sparkle-signatures:\nedSignature: " + signature + f"\nlength: {len(content)}\n-->\n".encode()
    signed = content + block
    appcast = tmp_path / "signed.xml"
    appcast.write_bytes(signed)
    check_appcast(appcast, require_signed_feed=True, public_key=public)
    if tamper == "content":
        signed = signed.replace(b"Fixture", b"Changed")
    elif tamper == "length":
        signed = signed.replace(f"length: {len(content)}".encode(), b"length: 1")
    elif tamper == "signature":
        signed = signed.replace(signature, base64.b64encode(bytes(64)))
    elif tamper == "public-key":
        public = base64.b64encode(eddsa.import_private_key(bytes(reversed(range(32)))).public_key().export_key(format="raw")).decode()
    elif tamper == "trailing":
        signed += b"<!-- unsigned extra content -->"
    else:
        signed += block
    appcast.write_bytes(signed)
    with pytest.raises(ValueError, match="signature|signed length"):
        check_appcast(appcast, require_signed_feed=True, public_key=public)


def test_feed_limit_rejects_oversized_input_before_parsing(tmp_path: Path) -> None:
    """Issue #91: the deployment checker bounds input without reading a whole oversized feed."""
    appcast = tmp_path / "oversized.xml"
    with appcast.open("wb") as handle:
        handle.truncate(MAX_FEED_BYTES + 1)
    with pytest.raises(ValueError, match="16 MiB feed limit"):
        check_appcast(appcast, require_signed_feed=True)


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
