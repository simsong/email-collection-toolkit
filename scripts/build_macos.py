# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Build a self-contained app and DMG, optionally Developer ID signed, and test it mounted."""

from __future__ import annotations

import argparse
import os
import platform
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from importlib.metadata import distribution, version
from pathlib import Path
from typing import Literal

from mailarchiver.self_test import SelfTestReport
from mailarchiver.clamav_definitions import DEVELOPMENT_DATABASE, certificates_path, library_path, read_definitions, updater_path
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from pydantic import BaseModel

from dmg_layout import create_image, verify_layout
from macos_signing import (
    NotarizationCredentials, SigningSecrets, dmg_filename, notarize_image, release_safe_environment, sign_image,
    signing_identity,
)
from release_tag import release_metadata

ROOT = Path(__file__).resolve().parents[1]
APP_NAME = "Email Collection Toolkit"
IDENTIFIER = "net.simson.mailarchiver"
SPARKLE_FEED_URL = "https://simsong.github.io/email-collection-toolkit/updates/mac/appcast.xml"
SPARKLE_PUBLIC_KEY = "qkdXdvt9A3YjGYwpENGrEEBY7kp3hU+TIjhCz/b7cjw="
PLIST_DOCUMENT_TYPES = "CFBundleDocumentTypes"
PLIST_EXPORTED_TYPES = "UTExportedTypeDeclarations"
PLIST_SHORT_VERSION = "CFBundleShortVersionString"
PLIST_BUILD_VERSION = "CFBundleVersion"
PLIST_SPARKLE_FEED_URL = "SUFeedURL"
PLIST_SPARKLE_PUBLIC_KEY = "SUPublicEDKey"
PLIST_COPYRIGHT = "NSHumanReadableCopyright"
PLIST_TYPE_NAME = "CFBundleTypeName"
PLIST_TYPE_ROLE = "CFBundleTypeRole"
PLIST_HANDLER_RANK = "LSHandlerRank"
PLIST_CONTENT_TYPES = "LSItemContentTypes"
PLIST_IS_PACKAGE = "LSTypeIsPackage"
PLIST_UTI = "UTTypeIdentifier"
PLIST_DESCRIPTION = "UTTypeDescription"
PLIST_CONFORMS = "UTTypeConformsTo"
PLIST_TAGS = "UTTypeTagSpecification"
PLIST_EXTENSION = "public.filename-extension"
COPYRIGHT = "Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved."
MACHO_MAGIC = {bytes.fromhex(value) for value in ("feedface", "cefaedfe", "feedfacf", "cffaedfe", "cafebabe", "bebafeca", "cafebabf", "bfbafeca")}


class ImageEntry(BaseModel):
    path: str
    kind: Literal["file", "symlink"]
    size_bytes: int | None = None
    target: str | None = None


class ImageContents(BaseModel):
    entries: list[ImageEntry]


def record_image_contents(mount: Path, destination: Path, *, log_entries: bool = False) -> None:
    """Preserve a relative mounted-file inventory even when a later self-test fails."""
    entries = []
    for path in sorted(mount.rglob("*")):
        relative = path.relative_to(mount).as_posix()
        if path.is_symlink():
            entries.append(ImageEntry(path=relative, kind="symlink", target=os.readlink(path)))
        elif path.is_file():
            entries.append(ImageEntry(path=relative, kind="file", size_bytes=path.stat().st_size))
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(ImageContents(entries=entries).model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(f"Recorded {len(entries)} mounted DMG files and links: {destination}", flush=True)
    if log_entries:
        for entry in entries:
            print(entry.model_dump_json(exclude_none=True))
        sys.stdout.flush()


def run(*arguments: str | Path, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run([str(argument) for argument in arguments], check=True, **kwargs)


def icon(work: Path) -> Path:
    iconset = work / "MailArchiver.iconset"
    iconset.mkdir()
    source = ROOT / "gui/icons/rainbow-post-192.png"
    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            pixels = str(size * scale)
            suffix = "@2x" if scale == 2 else ""
            run("/usr/bin/sips", "-z", pixels, pixels, source, "--out",
                iconset / f"icon_{size}x{size}{suffix}.png", stdout=subprocess.DEVNULL)
    result = work / "MailArchiver.icns"
    run("/usr/bin/iconutil", "-c", "icns", "-o", result, iconset)
    return result


def configure_bundle(app: Path, signing_identity: str) -> None:
    plist = app / "Contents/Info.plist"
    with plist.open("rb") as handle:
        info = plistlib.load(handle)
    _, _, sparkle_version, display_version = release_metadata(version("mailarchiver"))
    info[PLIST_SHORT_VERSION] = display_version
    info[PLIST_BUILD_VERSION] = str(sparkle_version)
    info[PLIST_SPARKLE_FEED_URL] = SPARKLE_FEED_URL
    info[PLIST_SPARKLE_PUBLIC_KEY] = SPARKLE_PUBLIC_KEY
    info[PLIST_COPYRIGHT] = COPYRIGHT
    info[PLIST_DOCUMENT_TYPES] = [{
        PLIST_TYPE_NAME: "Mail Archive", PLIST_TYPE_ROLE: "Editor",
        PLIST_HANDLER_RANK: "Owner", PLIST_CONTENT_TYPES: [IDENTIFIER + ".archive"],
        PLIST_IS_PACKAGE: True,
    }]
    info[PLIST_EXPORTED_TYPES] = [{
        PLIST_UTI: IDENTIFIER + ".archive", PLIST_DESCRIPTION: "Mail Archive",
        PLIST_CONFORMS: ["com.apple.package"],
        PLIST_TAGS: {PLIST_EXTENSION: ["mailarchive"]},
    }]
    with plist.open("wb") as handle:
        plistlib.dump(info, handle)
    # PyInstaller signs every nested binary; refresh the outer seal after metadata changes.
    options = [] if signing_identity == "-" else ["--options", "runtime", "--timestamp"]
    # Collected executables can inherit hardened-runtime flags while being re-signed.
    # An ad-hoc child has no Team ID and must not enforce Team-ID library validation.
    children = (
        app / "Contents/Frameworks/importers/pff-converter/pff-converter",
        app / "Contents/Frameworks/clamav/freshclam",
    )
    child_options = ["--options", "0"] if signing_identity == "-" else options
    for child in children:
        run("/usr/bin/codesign", "--force", "--sign", signing_identity, *child_options, child)
    run("/usr/bin/codesign", "--force", "--sign", signing_identity, *options, app)
    run("/usr/bin/codesign", "--verify", "--deep", "--strict", app)


@contextmanager
def mounted_image(dmg: Path):
    """Never recursively clean a directory that might still contain a mounted volume."""
    temporary = Path(tempfile.mkdtemp(prefix="mailarchiver-dmg-test-"))
    mount = temporary / "mounted"
    mount.mkdir()
    try:
        run("/usr/bin/hdiutil", "attach", "-readonly", "-nobrowse", "-mountpoint", mount, dmg)
        try:
            yield mount
        finally:
            # WebKit helpers can retain a framework briefly after the GUI process exits.
            result: subprocess.CompletedProcess[str] | None = None
            for _ in range(10):
                result = subprocess.run(["/usr/bin/hdiutil", "detach", str(mount)],
                                        capture_output=True, text=True, check=False)
                if result.returncode == 0:
                    break
                time.sleep(1)
            else:
                assert result is not None
                raise RuntimeError(f"Could not eject test volume {mount}: {result.stderr}")
    finally:
        # rmdir is nonrecursive and fails safely on an occupied mount or unexpected file.
        if not os.path.ismount(mount):
            try:
                mount.rmdir()
                temporary.rmdir()
            except OSError:
                pass


def test_image(dmg: Path, *, gui: bool = False, manifest_path: Path | None = None,
               log_contents: bool = False) -> None:
    """Mount read-only and test the actual bundle outside the source checkout."""
    with mounted_image(dmg) as mount:
        manifest = manifest_path or ROOT / "dist" / f"{dmg.stem}.contents.json"
        record_image_contents(mount, manifest, log_entries=log_contents)
        app = mount / f"{APP_NAME}.app"
        library = app / "Contents/Frameworks/clamav/libclamav.dylib"
        if not library.is_file():
            raise RuntimeError(f"Bundled ClamAV library is missing: {library}; see {manifest}")
        run("/usr/bin/codesign", "--verify", "--deep", "--strict", app)
        if not (mount / "Applications").is_symlink() or os.readlink(mount / "Applications") != "/Applications":
            raise RuntimeError("DMG is missing its Applications shortcut")
        verify_dependencies(app)
        verify_layout(mount, app.name)
        executable = app / "Contents/MacOS" / APP_NAME
        environment = release_safe_environment(
            os.environ, ("PYTHON", "DYLD_", "MAILARCHIVER", "MAIL_ARCHIVE"))
        environment["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
        from mailarchiver.clamav_update import write_freshclam_config
        with tempfile.TemporaryDirectory(prefix="freshclam-mounted-test-") as temporary:
            configuration = Path(temporary) / "freshclam.conf"
            certs = app / "Contents/Resources/clamav/certs"
            write_freshclam_config(configuration, certs if certs.is_dir() else None, checks=0)
            run(app / "Contents/Frameworks/clamav/freshclam", "--version",
                f"--config-file={configuration}", cwd=mount.parent, env=environment, timeout=20)
        converter = app / "Contents/Resources/importers/pff-converter/pff-converter"
        with tempfile.TemporaryDirectory(prefix="pff-mounted-test-") as temporary:
            receipt = Path(temporary) / "receipt.json"
            run(converter, "--receipt", receipt, "--", ROOT / "rust/mct-importer/tests/fixtures/empty.pst",
                cwd=temporary, env=environment, timeout=60)
            from mailarchiver.pff_source import PffReceipt
            converted = PffReceipt.model_validate_json(receipt.read_text())
            if not converted.complete or converted.emitted:
                raise RuntimeError("mounted standalone converter failed the empty PST fixture")
            archive = Path(temporary) / "archive"
            archive.mkdir()
            owners = Path(temporary) / "owners.txt"
            owners.write_text("fixture@example.test\n")
            run(executable, "--cli", "--archive", archive, "ingest", "--no-scan",
                "--owner-names-file", owners, "--defer-content",
                ROOT / "rust/mct-importer/tests/fixtures/empty.pst", cwd=temporary, env=environment, timeout=90)
            from mailarchiver.pst_source import ImportReceipt
            receipts = list(archive.glob("processing-pst/*/receipt.json"))
            if len(receipts) != 1 or ImportReceipt.model_validate_json(receipts[0].read_text()).emitted:
                raise RuntimeError("mounted application did not retain Rust PST import evidence")
        for mode in (("self-test", "self-test-gui") if gui else ("self-test",)):
            detail = "opens and closes synthetic test windows" if mode == "self-test-gui" else "no windows"
            print(f"Running mounted {mode} ({detail}); waiting for the test process to exit.", flush=True)
            report_path = dmg.with_suffix(f".{mode}.json")
            run(executable, f"--{mode}", "--report", report_path,
                cwd=mount.parent, env=environment, timeout=150)
            report = SelfTestReport.model_validate_json(report_path.read_text(encoding="utf-8"))
            if not report.passed or not report.frozen:
                raise RuntimeError(f"mounted {mode} failed: {report}")


def load_rpaths(commands: str) -> tuple[str, ...]:
    """Read LC_RPATH values from otool load-command output, preserving spaces."""
    paths = []
    in_rpath = False
    for line in commands.splitlines():
        value = line.strip()
        if value.startswith("cmd "):
            in_rpath = value == "cmd LC_RPATH"
        elif in_rpath and value.startswith("path "):
            paths.append(value[5:].rsplit(" (offset ", 1)[0])
            in_rpath = False
    return tuple(paths)


def load_dependencies(commands: str) -> tuple[str, ...]:
    """Read actual dylib load commands; LC_ID_DYLIB describes the binary itself."""
    paths = []
    dependency = False
    for line in commands.splitlines():
        value = line.strip()
        if value.startswith("cmd "):
            dependency = value in {
                "cmd LC_LOAD_DYLIB", "cmd LC_LOAD_WEAK_DYLIB", "cmd LC_REEXPORT_DYLIB",
                "cmd LC_LAZY_LOAD_DYLIB", "cmd LC_LOAD_UPWARD_DYLIB",
            }
        elif dependency and value.startswith("name "):
            paths.append(value[5:].rsplit(" (offset ", 1)[0])
            dependency = False
    return tuple(paths)


def bundle_loader_path(app: Path, binary: Path, value: str) -> Path:
    """Expand loader paths and reject build-machine or escaping search paths."""
    if value == "@loader_path" or value.startswith("@loader_path/"):
        target = binary.parent / value.removeprefix("@loader_path").lstrip("/")
    elif value == "@executable_path" or value.startswith("@executable_path/"):
        target = app / "Contents/MacOS" / value.removeprefix("@executable_path").lstrip("/")
    else:
        target = Path(value)
        if not target.is_absolute():
            raise RuntimeError(f"unsupported binary search path: {binary}: {value}")
    resolved = target.resolve()
    if not resolved.is_relative_to(app.resolve()):
        raise RuntimeError(f"nonportable binary search path: {binary}: {value}")
    return resolved


def verify_dependencies(app: Path) -> None:
    """Reject accidental Homebrew/build-machine linkage in every bundled Mach-O file."""
    checked: set[Path] = set()
    rpaths: set[Path] = set()
    for candidate in app.rglob("*"):
        if not candidate.is_file():
            continue
        path = candidate.resolve()
        if not path.is_relative_to(app.resolve()):
            raise RuntimeError(f"bundle file links outside app: {candidate}")
        if path in checked:
            continue
        with path.open("rb") as handle:
            if handle.read(4) not in MACHO_MAGIC:
                continue
        checked.add(path)
        commands = run("/usr/bin/otool", "-l", path, capture_output=True, text=True).stdout
        rpaths.update(bundle_loader_path(app, path, value) for value in load_rpaths(commands))
    for path in checked:
        commands = run("/usr/bin/otool", "-l", path, capture_output=True, text=True).stdout
        for dependency in load_dependencies(commands):
            if dependency.startswith(("/usr/lib/", "/System/Library/")):
                continue
            if dependency.startswith("@rpath/"):
                candidates = [(root / dependency.removeprefix("@rpath/")).resolve() for root in rpaths]
                if not any(item.is_relative_to(app.resolve()) and item.is_file() for item in candidates):
                    raise RuntimeError(f"unresolved bundled dependency: {path}: {dependency}")
            else:
                target = bundle_loader_path(app, path, dependency)
                if not target.is_file():
                    raise RuntimeError(f"missing bundled dependency: {path}: {dependency}")
    if not checked:
        raise RuntimeError("no native executable found inside app")
    print(f"Verified {len(checked)} bundled Mach-O files: no external non-system library paths")


def clamav_bundle_arguments(work: Path) -> list[str]:
    """Bundle the project's definitions and the native engine/updater with their dependencies."""
    definitions = read_definitions(DEVELOPMENT_DATABASE, "development")
    staging = work / "clamav-definitions"
    staging.mkdir()
    for path in definitions.directory.iterdir():
        if path.is_file() and path.suffix in (".cvd", ".cld", ".sign"):
            shutil.copyfile(path, staging / path.name)
    arguments = ["--add-data", f"{staging}:clamav/definitions"]
    for binary in (library_path(), updater_path()):
        if not binary.is_file():
            raise FileNotFoundError(f"Install ClamAV before building: {binary}")
        arguments.extend(("--add-binary", f"{binary}:clamav"))
    if certs := certificates_path():
        arguments.extend(("--add-data", f"{certs}:clamav/certs"))
    return arguments


def clamav_openssl_sources() -> tuple[Path, Path]:
    """Find the OpenSSL pair against which the installed ClamAV was linked."""
    commands = run("/usr/bin/otool", "-l", library_path(), capture_output=True, text=True).stdout
    dependencies = load_dependencies(commands)
    names = ("libssl.3.dylib", "libcrypto.3.dylib")
    sources = []
    for name in names:
        matches = [Path(value) for value in dependencies if Path(value).name == name]
        if len(matches) != 1 or not matches[0].is_absolute() or not matches[0].is_file():
            raise RuntimeError(f"ClamAV does not have one installed {name} dependency")
        sources.append(matches[0])
    if sources[0].resolve().parent != sources[1].resolve().parent:
        raise RuntimeError("ClamAV's OpenSSL libraries do not come from one installation")
    return sources[0], sources[1]


def bundle_clamav_openssl(app: Path, signing_identity: str) -> None:
    """Keep ClamAV's OpenSSL ABI instead of PyInstaller's basename-deduplicated pair."""
    frameworks = app / "Contents/Frameworks"
    ssl_source, crypto_source = clamav_openssl_sources()
    ssl = frameworks / ssl_source.name
    crypto = frameworks / crypto_source.name
    for source, target in ((ssl_source, ssl), (crypto_source, crypto)):
        if target.is_symlink():
            target.unlink()
        shutil.copy2(source, target)
    commands = run("/usr/bin/otool", "-l", ssl, capture_output=True, text=True).stdout
    for dependency in load_dependencies(commands):
        if Path(dependency).name == crypto.name:
            run("/usr/bin/install_name_tool", "-change", dependency, f"@loader_path/{crypto.name}", ssl)
    options = [] if signing_identity == "-" else ["--options", "runtime", "--timestamp"]
    for library in (crypto, ssl):
        run("/usr/bin/codesign", "--force", "--sign", signing_identity, *options, library)


def build(signing_identity: str, *, gui: bool = False, log_contents: bool = False) -> Path:
    output = ROOT / "dist"
    output.mkdir(exist_ok=True)
    work_root = ROOT / ".tmp"
    work_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="dmg-build-", dir=work_root) as temporary:
        work = Path(temporary)
        bundle_output = work / "bundle"
        # Collect runtime notices, not development-only tools such as Pylint.
        notices = work / "Third Party Notices"
        notices.mkdir()
        pending = ["mailarchiver", "pyobjc-framework-Cocoa", "pyobjc-framework-WebKit"]
        visited = set()
        while pending:
            name = canonicalize_name(pending.pop())
            if name in visited:
                continue
            visited.add(name)
            package = distribution(name)
            for requirement_text in package.requires or ():
                requirement = Requirement(requirement_text)
                if requirement.marker is None or requirement.marker.evaluate():
                    pending.append(requirement.name)
            for entry in package.files or ():
                if ".dist-info/" in str(entry) and any(word in str(entry).lower() for word in ("license", "copying", "notice")):
                    target = notices / str(entry)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(str(package.locate_file(entry)), target)
        shutil.copytree(ROOT / "converters/pff", notices / "pff-converter",
                        ignore=shutil.ignore_patterns(".venv", "__pycache__", "*.egg-info", ".pytest_cache"))
        app_icon = icon(work)
        plugins = work / "plugins"
        shutil.copytree(ROOT / "src/mailarchiver/plugins", plugins, ignore=shutil.ignore_patterns("__pycache__"))
        command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--windowed", "--onedir",
                   "--name", APP_NAME, "--osx-bundle-identifier", IDENTIFIER,
                   "--target-arch", platform.machine(), "--codesign-identity", signing_identity,
                   "--icon", str(app_icon), "--distpath", str(bundle_output),
                   "--workpath", str(work / "work"), "--specpath", str(work),
                   "--copy-metadata", "mailarchiver", "--collect-data", "mailarchiver",
                   "--collect-data", "webview", "--hidden-import", "webview.platforms.cocoa",
                   "--hidden-import", "mailarchiver.sources", "--hidden-import", "mailarchiver.source_stubs",
                   "--hidden-import", "mailarchiver.pst_source", "--hidden-import", "mailarchiver.pff_source",
                   "--exclude-module", "pypff",
                   "--hidden-import", "mailarchiver.processing.builtin",
                   "--add-data", f"{plugins}:mailarchiver/plugins",
                   "--add-data", f"{ROOT / 'gui'}:gui",
                   "--add-data", f"{ROOT / 'src/mailarchiver/standalone_verify.py'}:mailarchiver",
                   "--add-data", f"{notices}:Third Party Notices",
                   *clamav_bundle_arguments(work),
                   "--add-binary", f"{ROOT / 'target/release/pst-importer'}:importers",
                   "--add-binary", f"{ROOT / 'target/release/mcti-scan'}:importers",
                   "--add-data", f"{ROOT / 'target/pff-converter'}:importers/pff-converter",
                   str(ROOT / "scripts/desktop_entry.py")]
        environment = release_safe_environment(os.environ, ("PYTHON",))
        run(*command, cwd=ROOT, env=environment)
        app = bundle_output / f"{APP_NAME}.app"
        bundle_clamav_openssl(app, signing_identity)
        configure_bundle(app, signing_identity)
        dmg = output / dmg_filename(version("mailarchiver"), platform.machine(), signing_identity)
        candidate = work / "candidate.dmg"
        create_image(app, app_icon, candidate, work)
        # Keep a previous artifact until the requested mounted tests have passed.
        test_image(candidate, gui=gui, manifest_path=dmg.with_suffix(".contents.json"),
                   log_contents=log_contents)
        sign_image(candidate, signing_identity)
        candidate.replace(dmg)
        for mode in (("self-test", "self-test-gui") if gui else ("self-test",)):
            candidate.with_suffix(f".{mode}.json").replace(dmg.with_suffix(f".{mode}.json"))
        if not gui:
            # An older GUI report must not imply this replacement image passed release checks.
            dmg.with_suffix(".self-test-gui.json").unlink(missing_ok=True)
        return dmg


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test-dmg", type=Path, help="mount and retest an existing DMG")
    parser.add_argument("--check-release", action="store_true", help="include the visible GUI self-test for release validation")
    parser.add_argument("--preview-dmg", type=Path, help="open the mounted installer in Finder until Return is pressed")
    parser.add_argument("--notarize-dmg", type=Path, help="submit, staple, and validate an existing signed DMG")
    parser.add_argument("--log-dmg-contents", action="store_true", help="list every mounted file and link before testing")
    parser.add_argument("--signing-identity", help="existing Keychain identity; otherwise import optional signing secrets; '-' forces unsigned")
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("DMG builds and native tests require macOS")
    if args.preview_dmg:
        with mounted_image(args.preview_dmg.resolve(strict=True)) as mount:
            run("/usr/bin/open", mount)
            input("Inspect the installer in Finder; press Return to eject: ")
    elif args.test_dmg:
        test_image(args.test_dmg.resolve(strict=True), gui=args.check_release,
                   log_contents=args.log_dmg_contents)
    elif args.notarize_dmg:
        notarize_image(args.notarize_dmg.resolve(strict=True),
                       NotarizationCredentials.from_environment(os.environ), ROOT / ".tmp")
    else:
        credentials = SigningSecrets.from_environment(os.environ)
        with signing_identity(credentials, ROOT / ".tmp", args.signing_identity) as identity:
            print(f"Built and tested: {build(identity, gui=args.check_release, log_contents=args.log_dmg_contents)}")


if __name__ == "__main__":
    main()
