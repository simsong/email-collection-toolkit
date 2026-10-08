# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Desktop delivery requirements: actual no-scanner ingest and headless diagnostics.

Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""

import os
import plistlib
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
from pathlib import Path

import pytest

from mailarchiver.ingest_status import read_ingest_history
from mailarchiver.clamav_definitions import updater_path
from mailarchiver.clamav_update import write_freshclam_config
from mailarchiver.self_test import SelfTestReport
from mailarchiver.standalone_verify import verify_archive

ROOT = Path(__file__).resolve().parents[1]


def test_frozen_entry_dispatches_private_rust_service(tmp_path: Path) -> None:
    """Rust preview: service dispatch preserves JSON stdout without importing GUI."""
    from mailarchiver.rust_engine import Reply, Request

    request = Request(id=1, method="ping")
    process = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts/desktop_entry.py"), "--rust-engine", str(tmp_path / "missing.mailarchive")],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, cwd=tmp_path,
    )
    assert process.stdin is not None and process.stdout is not None
    with ThreadPoolExecutor(max_workers=1) as pool:
        response = pool.submit(process.stdout.readline)
        try:
            process.stdin.write(request.model_dump_json() + "\n")
            process.stdin.flush()
            reply = Reply.model_validate_json(response.result(timeout=10))
        finally:
            process.stdin.close()
            try:
                process.wait(timeout=6)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
    assert process.returncode == 0
    assert reply.id == 1 and not reply.error
    assert not (tmp_path / "missing.mailarchive").exists()


def test_historical_notice_gate_matches_the_shipped_release(tmp_path: Path) -> None:
    """Issue #91: the a10 repair may omit its absent Sparkle notice, but new DMGs may not."""
    scripts = str(ROOT / "scripts")
    sys.path.insert(0, scripts)
    try:
        from build_macos import verify_mounted_notices
        from mailarchiver.update_metadata import SPARKLE_PUBLIC_KEY
    finally:
        sys.path.remove(scripts)
    app = tmp_path / "Email Collection Toolkit.app"
    info = app / "Contents/Info.plist"
    info.parent.mkdir(parents=True)
    info.write_bytes(plistlib.dumps({
        "CFBundleVersion": "1000000110",
        "CFBundleShortVersionString": "1.0.0a10",
        "SUPublicEDKey": SPARKLE_PUBLIC_KEY,
    }))
    notices = app / "Contents/Resources/Third Party Notices"
    notices.mkdir(parents=True)
    for name in ("LICENSE", "COPYRIGHT", "THIRD_PARTY_NOTICES.md", "ClamAV-COPYING.txt", "OpenSSL-LICENSE.txt"):
        (notices / name).write_text("reviewed historical notice\n", encoding="utf-8")
    verify_mounted_notices(app, "v1.0.0a10")
    with pytest.raises(RuntimeError, match="Sparkle-LICENSE.txt"):
        verify_mounted_notices(app)
    with pytest.raises(ValueError, match="only the audited a10 release"):
        verify_mounted_notices(app, "v1.0.0a11")
    with pytest.raises(ValueError, match="only the audited a10 release"):
        verify_mounted_notices(app, "1.0.0a10")
    altered = plistlib.loads(info.read_bytes())
    altered["SUPublicEDKey"] = "different-key"
    info.write_bytes(plistlib.dumps(altered))
    with pytest.raises(ValueError, match="version or Sparkle public key"):
        verify_mounted_notices(app, "v1.0.0a10")


def test_pyinstaller_loader_reports_its_native_cause(tmp_path: Path) -> None:
    """The packaging-only frozen ctypes hook must preserve dlopen's actual failure."""
    pytest.importorskip("PyInstaller")
    library = tmp_path / "libclamav.dylib"
    library.write_bytes(b"not a dynamic library")
    code = """import sys
from pathlib import Path
sys._MEIPASS = sys.argv[1]
from PyInstaller.loader.pyimod03_ctypes import install
install()
from mailarchiver.libclamav import load_library
try:
    load_library(Path(sys.argv[2]))
except OSError as error:
    print(error)
else:
    raise AssertionError('invalid dylib loaded')
"""
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path), str(library)],
                            capture_output=True, text=True, check=True)
    assert "present (21 bytes) but cannot be loaded" in result.stdout
    assert "Most likely this dynlib/dll was not found" not in result.stdout


@pytest.mark.skipif(sys.platform != "darwin", reason="Mounted-DMG updater check requires macOS FreshClam")
def test_freshclam_uses_explicit_app_relative_configuration(tmp_path: Path) -> None:
    """Mounted-DMG updater validation must not require the host freshclam.conf."""
    updater = updater_path()
    if not updater.is_file():
        pytest.skip("FreshClam is not installed")
    certs = tmp_path / "Mounted App.app/Contents/Resources/clamav/certs"
    certs.mkdir(parents=True)
    config = tmp_path / "freshclam.conf"
    write_freshclam_config(config, certs, checks=0)
    assert f"CVDCertsDirectory {certs}\n" in config.read_text()
    result = subprocess.run([str(updater), "--version", f"--config-file={config}"],
                            capture_output=True, text=True, check=False, timeout=20)
    assert result.returncode == 0, result.stderr


def test_mounted_inventory_retains_av_files_and_logs_each_entry(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """Release validation retains and logs every mounted file/link before self-test."""
    scripts = str(ROOT / "scripts")
    sys.path.insert(0, scripts)
    try:
        from build_macos import ImageContents, ImageEntry, record_image_contents
    finally:
        sys.path.remove(scripts)
    mount = tmp_path / "mounted"
    library = mount / "Email Collection Toolkit.app/Contents/Frameworks/clamav/libclamav.dylib"
    library.parent.mkdir(parents=True)
    library.write_bytes(b"native fixture")
    definitions = mount / "Email Collection Toolkit.app/Contents/Resources/clamav/definitions/daily.cvd"
    definitions.parent.mkdir(parents=True)
    definitions.write_bytes(b"definitions")
    (mount / "Applications").symlink_to("/Applications")
    manifest = tmp_path / "reports/fixture.contents.json"
    record_image_contents(mount, manifest, log_entries=True)
    entries = {entry.path: entry for entry in ImageContents.model_validate_json(manifest.read_text()).entries}
    assert set(entries) == {str(library.relative_to(mount)), str(definitions.relative_to(mount)), "Applications"}
    assert entries[str(library.relative_to(mount))].kind == "file"
    assert entries[str(library.relative_to(mount))].size_bytes == len(b"native fixture")
    assert entries["Applications"].kind == "symlink" and entries["Applications"].target == "/Applications"
    output = capsys.readouterr().out.splitlines()
    assert len(output) == len(entries) + 1
    assert output[0].startswith("Recorded 3 mounted DMG files and links:")
    assert [ImageEntry.model_validate_json(line).path for line in output[1:]] == sorted(entries)


@pytest.mark.skipif(sys.platform != "darwin", reason="AppKit renders the macOS installer background")
def test_dmg_retina_background_keeps_text_and_arrow_in_bounds(tmp_path: Path) -> None:
    """Desktop delivery: Retina background ink must align with the real Finder icons."""
    from scripts.dmg_layout import background_image, verify_background

    background = tmp_path / "background.tiff"
    background_image(background)
    verify_background(background)


@pytest.mark.skipif(sys.platform != "darwin", reason="Native import confirmation uses AppKit")
@pytest.mark.parametrize("buttons", [("Import", "Cancel"), ("Cancel", "Import Without Scanning", "Install ClamAV…")])
def test_import_confirmation_has_wide_selectable_body(buttons: tuple[str, ...]) -> None:
    """Import confirmation must retain complete paths in a wider native dialog."""
    AppKit = import_module("AppKit")
    from mailarchiver.gui_app import IMPORT_CONFIRMATION_WIDTH, create_macos_alert

    AppKit.NSApplication.sharedApplication()
    message = ("Destination archive: /Users/example/tiny.mailarchive\n\n"
               "Read-only sources:\n/Users/example/gits/mail-archiver/tests/data\n\n"
               "Sent-mail owner names: /Users/example/tiny.mailarchive/owner-names.txt")
    alert = create_macos_alert("Import into tiny.mailarchive", message, buttons,
                               body_width=IMPORT_CONFIRMATION_WIDTH)
    alert.layout()
    body = alert.accessoryView()
    assert body.stringValue() == message
    assert body.isSelectable()
    assert body.frame().size.width >= IMPORT_CONFIRMATION_WIDTH
    assert alert.window().frame().size.width >= IMPORT_CONFIRMATION_WIDTH
    assert [button.title() for button in alert.buttons()] == list(buttons)
    assert alert.buttons()[0].keyEquivalent() == ("\x1b" if buttons[0] == "Cancel" else "\r")


def test_headless_self_test_without_scanner_or_display(tmp_path: Path) -> None:
    """No user archive, saved preference, native window, or installed antivirus is required."""
    report_path = tmp_path / "report.json"
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/desktop_entry.py"), "--self-test", "--report", str(report_path)],
        cwd=tmp_path, capture_output=True, text=True, check=False, timeout=30,
        env={**os.environ, "DISPLAY": "", "MAIL_ARCHIVE_DIR": str(tmp_path / "must-not-exist")},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = SelfTestReport.model_validate_json(report_path.read_text(encoding="utf-8"))
    assert report.passed and report.mode == "headless"
    assert not (tmp_path / "must-not-exist").exists()


def test_scanner_failure_never_silently_imports_unscanned(tmp_path: Path) -> None:
    """Mandatory scanning fails closed; explicit opt-out retains evidence and bytes."""
    source = tmp_path / "message.eml"
    raw = b"From: sender@example.net\nDate: Mon, 07 Sep 2026 12:00:00 +0000\nSubject: Scanner regression\n\nOriginal bytes\n"
    source.write_bytes(raw)
    owners = tmp_path / "owners.txt"
    owners.write_text("sender@example.net\n", encoding="utf-8")
    environment = {**os.environ, "MAILARCHIVER_CLAMAV_LIBRARY": str(tmp_path / "missing")}
    archive = tmp_path / "test.mailarchive"
    command = [sys.executable, str(ROOT / "scripts/desktop_entry.py"), "--cli", "--archive", str(archive),
               "ingest", str(source), "--owner-names-file", str(owners)]
    refused = subprocess.run(command + ["--clamav"], env=environment, capture_output=True, check=False, timeout=30)
    assert refused.returncode != 0
    with sqlite3.connect(archive / "archive.sqlite3") as catalog:
        assert catalog.execute("SELECT count(*) FROM messages").fetchone() == (0,)
    accepted = subprocess.run(command + ["--no-scan"], env=environment, capture_output=True, check=False, timeout=30)
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert b"NOT scanned" in accepted.stderr
    assert read_ingest_history(archive).statuses[0].scan_policy == "not-scanned"
    with sqlite3.connect(archive / "archive.sqlite3") as catalog:
        assert catalog.execute("SELECT count(*) FROM metadata_defects WHERE field='antivirus' AND detail LIKE 'not-scanned:%'").fetchone() == (1,)
    assert source.read_bytes() == raw
    assert not verify_archive(archive)


@pytest.mark.skipif(sys.platform != "darwin", reason="Mach-O linkage requires Apple's toolchain")
def test_clamav_bundles_its_matching_openssl_pair(tmp_path: Path) -> None:
    """Requirement: PyInstaller's OpenSSL choice cannot replace ClamAV's linked ABI."""
    from mailarchiver.clamav_definitions import library_path

    if not library_path().is_file():
        pytest.skip("ClamAV is not installed")
    scripts = str(ROOT / "scripts")
    sys.path.insert(0, scripts)
    try:
        from build_macos import bundle_clamav_openssl, clamav_openssl_sources, load_dependencies
    finally:
        sys.path.remove(scripts)
    ssl_source, crypto_source = clamav_openssl_sources()
    frameworks = tmp_path / "Fixture.app/Contents/Frameworks"
    frameworks.mkdir(parents=True)
    ssl = frameworks / ssl_source.name
    crypto = frameworks / crypto_source.name
    ssl.write_bytes(b"older PyInstaller choice")
    crypto.write_bytes(b"older PyInstaller choice")
    bundle_clamav_openssl(tmp_path / "Fixture.app", "-")
    assert ssl.stat().st_size > len(b"older PyInstaller choice")
    commands = subprocess.run(["/usr/bin/otool", "-l", str(ssl)], check=True, capture_output=True, text=True).stdout
    assert f"@loader_path/{crypto.name}" in load_dependencies(commands)
    for library in (crypto, ssl):
        subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(library)], check=True)


@pytest.mark.skipif(sys.platform != "darwin", reason="Mach-O linkage requires Apple's toolchain")
def test_dependency_audit_rejects_external_rpath(tmp_path: Path) -> None:
    """Requirement: an @rpath dependency cannot hide a build-machine LC_RPATH."""
    import shutil

    if shutil.which("cc") is None:
        pytest.skip("C compiler is unavailable")
    # build_macos is also an executable script importing its adjacent module.
    scripts = str(ROOT / "scripts")
    sys.path.insert(0, scripts)
    try:
        from build_macos import verify_dependencies
    finally:
        sys.path.remove(scripts)
    app = tmp_path / "Fixture.app"
    binary = app / "Contents/MacOS/fixture"
    binary.parent.mkdir(parents=True)
    source = tmp_path / "fixture.c"
    source.write_text("int main(void) { return 0; }\n", encoding="utf-8")
    subprocess.run(["cc", str(source), "-Wl,-rpath,/opt/homebrew/lib", "-o", str(binary)], check=True)
    with pytest.raises(RuntimeError, match="nonportable binary search path"):
        verify_dependencies(app)
    subprocess.run(["cc", str(source), "-Wl,-rpath,@executable_path", "-o", str(binary)], check=True)
    verify_dependencies(app)
    # A dylib's install name is its own identity, not a dependency on another file.
    library_source = tmp_path / "library.c"
    library_source.write_text("int fixture_value(void) { return 0; }\n", encoding="utf-8")
    library = binary.parent / "library.dylib"
    subprocess.run(["cc", "-dynamiclib", str(library_source), "-Wl,-install_name,@rpath/identity-only.dylib", "-o", str(library)], check=True)
    verify_dependencies(app)
    # Once an executable actually loads that name, it must resolve within the app.
    source.write_text("extern int fixture_value(void); int main(void) { return fixture_value(); }\n", encoding="utf-8")
    subprocess.run(["cc", str(source), str(library), "-Wl,-rpath,@executable_path", "-o", str(binary)], check=True)
    with pytest.raises(RuntimeError, match="unresolved bundled dependency"):
        verify_dependencies(app)


@pytest.mark.skipif(sys.platform != "darwin", reason="Mach-O linkage requires Apple's toolchain")
def test_dependency_audit_filters_release_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #91: real otool probes must not inherit release keys or loader overrides."""
    from scripts.macos_signing import RELEASE_SECRET_NAMES

    scripts = str(ROOT / "scripts")
    sys.path.insert(0, scripts)
    try:
        import build_macos
    finally:
        sys.path.remove(scripts)
    app = tmp_path / "Fixture.app"
    binary = app / "Contents/MacOS/fixture"
    binary.parent.mkdir(parents=True)
    source = tmp_path / "fixture.c"
    source.write_text("int main(void) { return 0; }\n", encoding="utf-8")
    subprocess.run(["cc", str(source), "-o", str(binary)], check=True)
    for name in (*RELEASE_SECRET_NAMES, "PYTHONPATH", "DYLD_INSERT_LIBRARIES", "LD_PRELOAD", "ARCHIVE_OVERRIDE"):
        monkeypatch.setenv(name, "fixture-secret")
    actual_run = build_macos.run
    observed: list[Path] = []

    def checked_run(*arguments, **kwargs):
        assert arguments[0] == "/usr/bin/otool"
        environment = kwargs["env"]
        assert all(name not in environment for name in RELEASE_SECRET_NAMES)
        assert all(name not in environment for name in
                   ("PYTHONPATH", "DYLD_INSERT_LIBRARIES", "LD_PRELOAD", "ARCHIVE_OVERRIDE"))
        assert environment["PATH"] == "/usr/bin:/bin:/usr/sbin:/sbin"
        observed.append(Path(arguments[-1]))
        return actual_run(*arguments, **kwargs)

    monkeypatch.setattr(build_macos, "run", checked_run)
    build_macos.verify_dependencies(app)
    assert observed == [binary, binary]


@pytest.mark.parametrize("succeeds", [False, True])
def test_gui_remembers_source_only_after_success(tmp_path: Path, succeeds: bool) -> None:
    """Requirement: failed GUI imports retain picker state and do not publish a generation."""
    from threading import Event

    from mailarchiver.__main__ import IngestRequest
    from mailarchiver.application import ApplicationController, ApplicationPreferencesStore, IngestJob
    from mailarchiver.archive_config import import_directory, remember_import_directory
    from mailarchiver.gui_app import PyWebViewApplication
    from mailarchiver.writer_lock import WriterLease

    controller = ApplicationController(ApplicationPreferencesStore(tmp_path / "preferences.json"))
    document = controller.create_document(tmp_path / "archive.mailarchive")
    assert document.path is not None
    session = controller.new_search_window(document)
    previous = tmp_path / "previous"
    previous.mkdir()
    remember_import_directory(document.path, [previous])
    source = tmp_path / "new-source" / "message.eml"
    source.parent.mkdir()
    if succeeds:
        source.write_bytes(b"From: sender@example.net\nDate: Mon, 07 Sep 2026 12:00:00 +0000\nSubject: fixture\n\nbody\n")
    owners = tmp_path / "owners.txt"
    owners.write_text("fixture-owner\n", encoding="utf-8")
    lease = WriterLease.acquire(document.path, document.descriptor.identity, "fixture", "fixture", "test")
    controller.begin_ingest(document.descriptor.document_id, IngestJob(operation_id="fixture", owner_window_id=session.window_id), lease)
    host = PyWebViewApplication(controller)
    host._run_import(document, "fixture", lease, IngestRequest(archive=document.path, owner_names_file=owners, roots=[str(source)], scan_policy="not-scanned"), Event())
    assert import_directory(document.path) == (source.parent if succeeds else previous)
    assert document.generation == int(succeeds)
    assert not lease.acquired
# Release requirement: private signing keys and synthetic upgrade fixtures must
# not become alpha assets; retained installer bytes must match tested hashes.
def test_release_staging_authenticates_base_bundle_and_excludes_private_files(tmp_path: Path) -> None:
    import zipfile
    from pydantic import TypeAdapter
    from scripts.release_files import FileHash, ROOT as RELEASE_ROOT, digest, stage

    windows = tmp_path / "windows"
    windows.mkdir()
    (windows / "base.msixbundle").write_bytes(b"synthetic signed bundle fixture")
    (windows / "local-test.cer").write_bytes((RELEASE_ROOT / "scripts/win/test-signing.cer").read_bytes())
    (windows / "README.txt").write_bytes((RELEASE_ROOT / "scripts/win/MSIX_README.txt").read_bytes())
    (windows / "Install-Test-Certificate.ps1").write_bytes((RELEASE_ROOT / "scripts/win/Install-Test-Certificate.ps1").read_bytes())
    (windows / "upgrade.msixbundle").write_bytes(b"CI-only future-version fixture")
    (windows / "local-test.pfx").write_bytes(b"private-key sentinel")
    hashes = [FileHash(Algorithm="SHA256", Hash=digest(windows / name).upper(), Path=f"C:\\build\\{name}")
              for name in ("base.msixbundle", "local-test.cer")]
    (windows / "sha256.json").write_bytes(TypeAdapter(list[FileHash]).dump_json(hashes, by_alias=True))
    destination = tmp_path / "release"
    bundle = stage(windows, destination)
    assert bundle.read_bytes() == (windows / "base.msixbundle").read_bytes()
    with zipfile.ZipFile(bundle.with_suffix(".zip")) as archive:
        assert set(archive.namelist()) == {bundle.name, "local-test.cer", "README.txt", "Install-Test-Certificate.ps1", "SHA256SUMS"}
        readme = archive.read("README.txt").decode()
        assert bundle.name in readme and "SHA256SUMS" in readme and "sha256.json" not in readme
        from hashlib import sha256
        sums = archive.read("SHA256SUMS").decode().splitlines()
        assert len(sums) == 3
        for line in sums:
            expected, name = line.split("  ", 1)
            assert sha256(archive.read(name)).hexdigest() == expected
    assert not (destination / "upgrade.msixbundle").exists()
    assert not (destination / "local-test.pfx").exists()
    assert f"{digest(bundle)}  {bundle.name}" in (destination / "SHA256SUMS").read_text()
    (windows / "base.msixbundle").write_bytes(b"tampered downloaded bundle")
    with pytest.raises(ValueError, match="hash mismatch"):
        stage(windows, destination)
    assert bundle.read_bytes() == b"synthetic signed bundle fixture"


def test_native_build_command_receives_mapped_identity_and_literal_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    """Build requirement: both native packages use the mapper, not inherited metadata."""
    import tomllib
    from mailarchiver.release_versions import release_metadata
    from scripts.rust_release_metadata import BUILD, CHANNEL, FEED, PUBLIC_KEY, VERSION
    from mailarchiver.update_metadata import SPARKLE_FEED_URL, SPARKLE_PUBLIC_KEY

    monkeypatch.setenv(BUILD, "incorrect inherited build")
    _, channel, build, display = release_metadata(tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"])
    names = (VERSION, BUILD, CHANNEL, FEED, PUBLIC_KEY)
    child = f"import os,sys; print(*(os.environ[name] for name in {names!r}), sep='\\n'); print(sys.argv[1:])"
    result = subprocess.run([sys.executable, ROOT / "scripts/rust_release_metadata.py", "--build-command",
                             sys.executable, "-c", child, "--child-option", "literal value"],
                            text=True, capture_output=True, check=True, timeout=15)
    assert result.stdout.splitlines() == [display, str(build), channel, SPARKLE_FEED_URL, SPARKLE_PUBLIC_KEY,
                                          "['--child-option', 'literal value']"]
    assert os.environ[BUILD] == "incorrect inherited build"
