# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Verify project-website validation and workflow integrity controls."""

import re
from html.parser import HTMLParser
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from yaml import safe_load

from scripts.check_appcast import MAX_FEED_BYTES, check_appcast
from scripts.check_website import validate_config, validate_png
from scripts.update_appcast import AppcastRelease, SignedArchive, append_item
from scripts.update_site_releases import Asset, PublishedRelease, choose_preview, installer_links, select_links

ZOLA_SHA256_ARM64 = "303b8e1f3251a6250e47f811eda143316f653c22201faa66777d48ac499c0ee3"
ZOLA_SHA256_X86_64 = "e79edcba2e8d03d22065c9cb8fa2e3abf07b823ef17f00abdc060188dceabba7"
HREF = "href"
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
    assert "run: make website-download-check website-build-check" in text
    assert "name: Upload Playwright failure traces" in text
    assert "path: test-results" in text
    configuration = safe_load(text)
    triggers = configuration.get(WORKFLOW_ON, configuration.get(True))
    assert triggers == {"push": {"branches": ["**", "!main"]}}
    jobs = configuration[JOBS]
    assert set(jobs) == {"static-rust", "python-browser", "release-candidate"}
    assert jobs["release-candidate"]["if"] == "github.ref == 'refs/heads/work-rust-gui' && contains(github.event.head_commit.message, '[release-ci]')"
    assert jobs["release-candidate"]["uses"] == "./.github/workflows/release-candidate.yml"
    assert all(NEEDS not in job for job in jobs.values())
    static_runs = [step.get(RUN, "") for step in jobs["static-rust"][STEPS]]
    test_runs = [step.get(RUN, "") for step in jobs["python-browser"][STEPS]]
    assert any("make check-static" in run for run in static_runs)
    assert any("make check-tests" in run for run in test_runs)
    assert all("make check-tests" not in run for run in static_runs)
    assert all("make check-static" not in run for run in test_runs)
    makefile = (workflow.parents[2] / "Makefile").read_text(encoding="utf-8")
    assert "check:\n\t$(MAKE) check-static\n\t$(MAKE) check-tests" in makefile
    for definition in workflow.parent.glob("*.yml"):
        if definition.name in {"rust-reader.yml", "windows-msix.yml"}:
            continue  # Its explicit multi-platform build matrix is checked below.
        configuration = safe_load(definition.read_text())
        for name, job in configuration[JOBS].items():
            if definition == workflow and name == "rust-gui":
                continue  # The explicit macos-latest matrix is checked above.
            assert job.get(RUNS_ON) == "macos-15" or "uses" in job, definition


def test_retired_reader_is_not_a_release_dependency() -> None:
    """Retirement: active workflows cannot launch or require the archived GUI."""
    root = Path(__file__).parents[1]
    for name in ("continuous-integration.yml", "release.yml", "release-candidate.yml", "windows-msix.yml"):
        text = (root / ".github/workflows" / name).read_text()
        assert "mailsearch-rust" not in text
        assert "rust-reader.yml" not in text
        assert "test-rust-gui-native" not in text
    assert (root / "rust/mailsearch-gui/ci/rust-reader.yml").is_file()


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
        "name: Sign and verify both final installers and XML",
        "name: Validate signed appcast",
        "name: Create complete draft release",
        "name: Publish complete release",
        "name: Dispatch Pages deployment from main",
    )
    # Validate the release commit and version before installing or building.
    assert [text.index(gate) for gate in gates] == sorted(text.index(gate) for gate in gates)
    makefile = (workflow.parents[2] / "Makefile").read_text(encoding="utf-8")
    assert "uv run --no-project --with packaging --python '>=3.12' python scripts/release_tag.py" in makefile
    signing_step = text[text.index("name: Sign and verify both final installers and XML"):
                        text.index("name: Validate signed appcast")]
    assert "SPARKLE_ED25519_PRIVATE_KEY_BASE64" in signing_step
    assert text.index('dist/SHA256SUMS "$APPCAST"') < text.index('gh release edit "$RELEASE_TAG"')
    configuration = safe_load(text)
    macos_steps = [step["name"] for step in configuration[JOBS]["macos"]["steps"]]
    assert macos_steps.index("Verify Sparkle release signer") < macos_steps.index("Verify signed update history")
    assert macos_steps.index("Verify signed update history") < macos_steps.index("Build DMG, list mounted contents, and run headless self-test")
    ci = safe_load((workflow.parent / "continuous-integration.yml").read_text(encoding="utf-8"))
    assert any(step.get("run") == "make test-sparkle-signing" for step in ci[JOBS]["python-browser"]["steps"])
    triggers = configuration.get(WORKFLOW_ON, configuration.get(True))
    assert triggers == {"push": {"tags": ["v*"]}}
    assert set(configuration[JOBS]) == {"assemble", "macos", "windows-msix", "preflight"}
    assert configuration[JOBS]["macos"][NEEDS] == "preflight"
    assert "git merge-base --is-ancestor HEAD refs/remotes/origin/main" in text
    assert ('gh workflow run pages.yml --repo "$GITHUB_REPOSITORY" '
            '--ref main -f release_tag="$RELEASE_TAG"') in text
    assert configuration[JOBS]["assemble"]["permissions"]["actions"] == "write"
    pages = (workflow.parent / "pages.yml").read_text(encoding="utf-8")
    assert "select(.draft == false)" in pages
    assert '--paginate --slurp' in pages
    assert 'make website-release-data' in pages
    assert 'appcast_tag="$RELEASE_TAG"' in pages
    assert pages.index("gh release download") < pages.index("name: Build Zola site")
    assert "actions/download-artifact@" not in pages
    assert 'if [[ -z "$appcast_tag" ]]; then' in pages
    history = (workflow.parents[2] / "scripts/fetch_release_appcast.sh").read_text(encoding="utf-8")
    assert 'if [[ -z "$previous_tag" ]]; then' in history
    assert '"$(git tag --list \'v*\')" != "$candidate_tag"' in history
    previous_gate = 'make check-appcast APPCAST="$appcast_path" RELEASE_TAG="$previous_tag" ARGS=--require-signed-feed'
    assert previous_gate.replace('$appcast_path', '$output') in history
    assert text.index('make release-appcast-base APPCAST="$appcast_path"') < text.index("name: Sign and verify both final installers and XML")
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


def test_msix_same_bundle_is_installed_without_rebuilding() -> None:
    """Windows x64 policy: explicit runs, one reused artifact, no signing-key upload."""
    root = Path(__file__).parents[1]
    workflow = safe_load((root / ".github/workflows/windows-msix.yml").read_text(encoding="utf-8"))
    triggers = workflow.get("on", workflow.get(True))
    assert set(triggers) == {"workflow_dispatch", "workflow_call", "push"}
    assert triggers["push"]["branches"] == ["codex/windows-msix-ci"]
    assert "[msix-ci]" in workflow[JOBS]["payload"]["if"]
    jobs = workflow[JOBS]
    assert jobs["bundle"][NEEDS] == "payload"
    assert jobs["install"][NEEDS] == "bundle"
    expected_matrix = [{"os": "windows-latest", "arch": "x64"}]
    assert jobs["payload"]["strategy"]["matrix"]["include"] == expected_matrix
    assert jobs["install"]["strategy"]["matrix"]["include"] == expected_matrix
    install = jobs["install"][STEPS]
    downloads = [step for step in install if step.get("uses", "").startswith("actions/download-artifact@")]
    assert len(downloads) == 2
    assert downloads[0]["with"]["name"] == "windows-msix-install-test"
    assert not any("cargo " in step.get(RUN, "") or "uv sync" in step.get(RUN, "") for step in install)
    upload = next(step for step in jobs["bundle"][STEPS] if step.get("uses", "").startswith("actions/upload-artifact@"))
    assert upload["with"]["name"] == downloads[0]["with"]["name"]
    assert set(upload["with"]["path"].splitlines()) == {
        "dist/bundle/base.msixbundle", "dist/bundle/*.cer", "dist/bundle/sha256.json",
        "dist/bundle/README.txt", "dist/bundle/Install-Test-Certificate.ps1"}
    fixture = jobs["bundle"][STEPS][-1]["with"]
    assert fixture["name"] == downloads[1]["with"]["name"] == "ci-only-msix-upgrade-fixture"
    assert fixture["path"] == "dist/bundle/upgrade.msixbundle"
    assert downloads[1]["with"]["path"] == downloads[0]["with"]["path"]
    release = safe_load((root / ".github/workflows/release.yml").read_text(encoding="utf-8"))
    assert release[JOBS]["windows-msix"][NEEDS] == "preflight"
    assert "windows-msix" in release[JOBS]["assemble"][NEEDS]
    secret = "MSIX_TEST_CERT_PFX_BASE64"
    assert triggers["workflow_call"]["secrets"][secret]["required"] is True
    signer = next(step for step in jobs["bundle"][STEPS] if "bundle_test_msix.ps1" in step.get(RUN, ""))
    assert signer["env"][secret] == "${{ secrets.MSIX_TEST_CERT_PFX_BASE64 }}"
    assert release[JOBS]["windows-msix"]["secrets"][secret] == signer["env"][secret]


@pytest.mark.parametrize("event,reference,message,expected", [
    ("push", "branch", "ordinary change", False),
    ("push", "branch", "explicit [msix-ci] build", True),
    ("push", "tag", "ordinary release", True),
    ("workflow_dispatch", "branch", "ordinary change", True),
])
def test_msix_payload_guard_keeps_release_dependencies_runnable(event: str, reference: str, message: str, expected: bool) -> None:
    """Release requirement: a reusable tag-push call cannot skip macOS assembly's dependency."""
    workflow = safe_load((Path(__file__).parents[1] / ".github/workflows/windows-msix.yml").read_text())
    condition = workflow[JOBS]["payload"]["if"]
    outcomes = []
    for term in condition.split(" || "):
        comparison = re.fullmatch(r"github\.(event_name|ref_type) (==|!=) '([^']*)'", term)
        if comparison:
            field, operator, value = comparison.groups()
            actual = event if field == "event_name" else reference
            outcomes.append((actual == value) if operator == "==" else (actual != value))
        else:
            contains = re.fullmatch(r"contains\(github\.event\.head_commit\.message, '([^']*)'\)", term)
            assert contains, f"Unsupported CI condition: {term}"
            outcomes.append(contains[1] in message)
    assert any(outcomes) is expected


def public_release(tag: str, *, windows: bool = True) -> PublishedRelease:
    """Purpose-made publication metadata, independent of the candidate version."""
    version = tag.removeprefix("v")
    names = [f"Email-Collection-Toolkit-{version}-arm64.dmg", "appcast.xml"]
    if windows:
        names.extend([f"ECT-{version}-windows-x64-arm64.msixbundle", f"ECT-{version}-windows-x64-arm64.zip"])
    return PublishedRelease(tag_name=tag, draft=False, prerelease=bool(re.search(r"[ab]\d+$", tag)),
                            assets=[Asset(name=name, size=123, state="uploaded",
                                          browser_download_url=f"https://github.com/simsong/email-collection-toolkit/releases/download/{tag}/{name}")
                                    for name in names])


@pytest.mark.parametrize("defect", ["draft", "missing", "empty", "uploading", "wrong-url", "duplicate", "missing-trust", "missing-feed"])
def test_site_selection_keeps_complete_release_when_newer_upload_is_incomplete(defect: str) -> None:
    """Release requirement: never replace working platform buttons with partial publication."""
    older, newer = public_release("v2.0.0a1"), public_release("v2.0.0a2")
    asset = newer.assets[0]
    if defect == "draft":
        newer.draft = True
    elif defect == "missing":
        newer.assets.pop(0)
    elif defect == "empty":
        asset.size = 0
    elif defect == "uploading":
        asset.state = "new"
    elif defect == "wrong-url":
        asset.browser_download_url = "https://example.invalid/installer.dmg"
    elif defect == "duplicate":
        newer.assets.append(asset.model_copy())
    elif defect == "missing-trust":
        newer.assets = [item for item in newer.assets if not item.name.endswith(".zip")]
    else:
        newer.assets = [item for item in newer.assets if item.name != "appcast.xml"]
    assert select_links([older, newer], True) == installer_links(older)


def test_site_data_uses_real_uploaded_assets_and_distinguishes_preview(tmp_path: Path) -> None:
    """Release requirement: stable/preview installers are static, direct, and public."""
    from pydantic import TypeAdapter
    releases = [public_release("v2.0.0"), public_release("v2.1.0b1"), public_release("v2.2.0a1", windows=False)]
    source, output = tmp_path / "public.json", tmp_path / "data.toml"
    source.write_bytes(TypeAdapter(list[PublishedRelease]).dump_json(releases))
    command = [sys.executable, str(Path(__file__).parents[1] / "scripts/update_site_releases.py"),
               "--releases-json", str(source), "--output", str(output)]
    result = subprocess.run(command + ["--require-complete-tag", "v2.1.0b1"], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    data = tomllib.loads(output.read_text())
    assert data["current_version"] == "v2.0.0"
    assert data["preview"]["tag"] == "v2.1.0b1"
    assert data["stable"]["mac_url"].endswith("/Email-Collection-Toolkit-2.0.0-arm64.dmg")
    assert data["preview"]["windows_url"].endswith("/ECT-2.1.0b1-windows-x64-arm64.msixbundle")
    assert data["preview"]["windows_help_url"].endswith(".zip")
    before = output.read_bytes()
    failure = subprocess.run(command + ["--require-complete-tag", "v2.2.0a1"], capture_output=True, text=True, check=False)
    assert failure.returncode != 0 and "not public with complete" in failure.stderr
    assert output.read_bytes() == before
    assert not installer_links(public_release("v2.0.0a1", windows=False)).windows_url
    assert not select_links([], False).tag
    draft = releases[0].model_copy(deep=True)
    draft.draft = True
    assert not installer_links(draft).tag


class DownloadLinks(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.urls: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            for key, value in attrs:
                if key == HREF and value:
                    self.urls.add(value)


@pytest.mark.parametrize("tags", [("v2.0.0", "v2.1.0b1"), ("v2.1.0b1",), ("v2.0.0a1",)])
def test_static_platform_downloads_render_without_javascript(tmp_path: Path, tags: tuple[str, ...]) -> None:
    """Website requirement: real Zola HTML exposes direct platform assets with preview labels."""
    import shutil
    from pydantic import TypeAdapter
    if not shutil.which("zola"):
        pytest.skip("Zola is required; run make website-download-check after provisioning the website tool")
    root = Path(__file__).parents[1]
    site, output = tmp_path / "site", tmp_path / "public"
    shutil.copytree(root / "website", site)
    releases = [public_release(tag, windows=tag != "v2.0.0a1") for tag in tags]
    source = tmp_path / "public-releases.json"
    source.write_bytes(TypeAdapter(list[PublishedRelease]).dump_json(releases))
    subprocess.run([sys.executable, str(root / "scripts/update_site_releases.py"), "--releases-json", str(source),
                    "--output", str(site / "data/releases.toml")], check=True, capture_output=True, text=True)
    result = subprocess.run(["zola", "--root", str(site), "build", "--output-dir", str(output)],
                            check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    html = (output / "index.html").read_text()
    anchors = DownloadLinks()
    anchors.feed(html)
    for release in releases:
        links = installer_links(release)
        assert links.mac_url in anchors.urls
        if links.windows_url:
            assert links.windows_url in anchors.urls
            assert links.windows_help_url in anchors.urls
    assert "Current release:" in html
    if tags == ("v2.0.0a1",):
        assert "Download for Windows" not in html
        assert "Download for Mac (.dmg) — Preview" in html
    elif len(tags) == 1:
        assert "Download for Windows (.msixbundle) — Preview" in html
    else:
        assert "Preview v2.1.0b1:" in html
        assert "Download for Mac (.dmg) — Preview" not in html
