# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Verify build provenance using real disposable Git repositories and package files.
# A clean commit, dirty source and later commits exercise identity attribution.
# Embedded metadata must remain tied to its build rather than a nearby checkout.
# Broken or absent provenance must display as unavailable instead of guessing.
# Native GUI tests separately verify the About renderer and macOS launch identity.
"""Delivery requirements: immutable, honest Git attribution for each tested build."""
from __future__ import annotations

import subprocess
import sys
import plistlib
from importlib.metadata import version
from pathlib import Path

import pytest
from packaging.version import Version

from mailarchiver.build_info import BUILD_INFO_FILENAME, BuildInfo, checkout_build_info, load_build_info, write_build_info


def git(root: Path, *arguments: str) -> str:
    """Operate solely on an unsigned synthetic repository, never contributor history."""
    return subprocess.run(["git", "-C", str(root), *arguments], check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    root.mkdir()
    git(root, "init")
    (root / "input.txt").write_text("original", encoding="utf-8")
    git(root, "add", "input.txt")
    git(root, "-c", "user.name=Synthetic Fixture", "-c", "user.email=fixture@example.test",
        "-c", "commit.gpgsign=false", "commit", "-m", "Synthetic build identity fixture")
    return root


def test_checkout_identity_distinguishes_commit_and_local_changes(checkout: Path) -> None:
    """About attribution includes tracked, staged and untracked changes at launch."""
    expected = git(checkout, "rev-parse", "HEAD")
    assert checkout_build_info(checkout) == BuildInfo(git_commit=expected, dirty=False)
    (checkout / "input.txt").write_text("modified", encoding="utf-8")
    assert checkout_build_info(checkout) == BuildInfo(git_commit=expected, dirty=True)
    git(checkout, "add", "input.txt")
    assert checkout_build_info(checkout).dirty
    git(checkout, "reset", "--hard", "HEAD")
    (checkout / "new.txt").write_text("untracked", encoding="utf-8")
    assert checkout_build_info(checkout).dirty


def test_embedded_identity_survives_later_checkout_changes(checkout: Path, tmp_path: Path) -> None:
    """Packaged About retains the build commit, even if installed beside another Git tree."""
    package = checkout / "src/mailarchiver"
    package.mkdir(parents=True)
    built = write_build_info(tmp_path / BUILD_INFO_FILENAME, checkout)
    (package / BUILD_INFO_FILENAME).write_bytes((tmp_path / BUILD_INFO_FILENAME).read_bytes())
    (checkout / "input.txt").write_text("later source", encoding="utf-8")
    git(checkout, "add", "input.txt")
    git(checkout, "-c", "user.name=Synthetic Fixture", "-c", "user.email=fixture@example.test",
        "-c", "commit.gpgsign=false", "commit", "-m", "Later synthetic fixture")
    assert checkout_build_info(checkout).git_commit != built.git_commit
    assert load_build_info(package) == built
    (package / BUILD_INFO_FILENAME).write_text('{"git_commit":"invalid"}', encoding="utf-8")
    assert load_build_info(package) == BuildInfo()


def test_discovery_does_not_guess_from_parent_or_environment(checkout: Path, tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch) -> None:
    """Absent Git data is explicit, and inherited Git overrides cannot misattribute a build."""
    package = checkout / "installed/src/mailarchiver"
    package.mkdir(parents=True)
    assert load_build_info(package) == BuildInfo()
    with pytest.raises(ValueError, match="readable Git checkout"):
        write_build_info(tmp_path / "missing.json", package)
    expected = checkout_build_info(checkout)
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "missing.git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path))
    assert checkout_build_info(checkout) == expected


def test_about_keeps_launch_version_after_installed_metadata_changes(tmp_path: Path) -> None:
    """An editable reinstall must not relabel a running process as the new code."""
    initial = version("mailarchiver")
    parsed = Version(initial)
    replacement = f"{parsed.major}.{parsed.minor}.{parsed.micro + 1}"
    metadata = tmp_path / "mailarchiver.dist-info/METADATA"
    metadata.parent.mkdir()
    metadata.write_text(f"Name: mailarchiver\nVersion: {initial}\n", encoding="utf-8")
    code = """import sys
from pathlib import Path
from importlib.metadata import version
sys.path.insert(0, sys.argv[1])
from mailarchiver.gui_app import application_metadata
assert application_metadata().version == sys.argv[2]
Path(sys.argv[1], 'mailarchiver.dist-info/METADATA').write_text(
    f'Name: mailarchiver\\nVersion: {sys.argv[3]}\\n', encoding='utf-8')
assert version('mailarchiver') == sys.argv[3]
assert application_metadata().version == sys.argv[2]
"""
    subprocess.run([sys.executable, "-c", code, str(tmp_path), initial, replacement],
                   check=True, capture_output=True, text=True, timeout=20)


@pytest.mark.skipif(sys.platform != "darwin", reason="Development bundle uses the macOS executable layout")
def test_development_bundle_preserves_scanner_worker_runtime(tmp_path: Path) -> None:
    """The named launcher must retain dependencies and real spawned ClamAV workers."""
    from scripts.run_gui import development_bundle, development_environment
    executable = development_bundle(tmp_path / "launcher")
    result = subprocess.run([str(executable), "-m", "pytest", "-q",
        "tests/test_scanner.py::test_real_library_clean_mime_eicar_and_concurrent_verdicts"],
        cwd=Path(__file__).resolve().parents[1], env=development_environment(),
        capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(sys.platform != "darwin", reason="Development bundle signing uses macOS codesign")
def test_development_bundle_seals_identity_and_preserves_venv(tmp_path: Path) -> None:
    """Native Keychain trust requires a sealed identity, with the original venv kept outside it."""
    from scripts.run_gui import BUNDLE_NAME, development_bundle, development_environment
    executable = development_bundle(tmp_path / "launcher")
    app = executable.parents[2]
    subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(app)],
                   check=True, capture_output=True, text=True)
    result = subprocess.run([str(executable), "-c", "import sys; print(sys.prefix); print(sys.executable)"],
        env=development_environment(), check=True, capture_output=True, text=True, timeout=10)
    assert result.stdout.splitlines() == [sys.prefix, sys.executable]
    plist = executable.parents[1] / "Info.plist"
    with plist.open("rb") as handle:
        info = plistlib.load(handle)
    info[BUNDLE_NAME] = "Changed after signing"
    with plist.open("wb") as handle:
        plistlib.dump(info, handle)
    invalid = subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(app)],
        check=False, capture_output=True, text=True)
    assert invalid.returncode != 0, "Changed bundle identity still passed signature validation"
