# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Authenticate cached Sparkle files against the verified archive, then bundle/sign."""

import argparse
import plistlib
import shutil
import subprocess
import tarfile
from pathlib import Path

from mailarchiver.update_metadata import SPARKLE_VERSION


def verify_cache(archive: Path, tools: Path) -> None:
    """Match all consumed framework files/links, notices and tools to the verified tar."""
    expected: set[Path] = set()
    if (tools / "Sparkle.framework").is_symlink():
        raise ValueError("cached Sparkle framework must not redirect outside its cache")
    with tarfile.open(archive, "r:xz") as source:
        for member in source:
            relative = Path(member.name)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("unsafe Sparkle archive path")
            if not relative.parts or (relative.parts[0] != "Sparkle.framework" and str(relative) not in
                                      {"LICENSE", "bin/generate_keys", "bin/sign_update"}):
                continue
            if member.isdir():
                continue
            expected.add(relative)
            target = tools / relative
            if not target.resolve().is_relative_to(tools.resolve()):
                raise ValueError(f"cached Sparkle path escapes tools: {relative}")
            if member.issym():
                if not target.is_symlink() or str(target.readlink()) != member.linkname:
                    raise ValueError(f"cached Sparkle link differs from verified archive: {relative}")
            elif member.isfile() or member.islnk():
                if target.is_symlink() or not target.is_file():
                    raise ValueError(f"cached Sparkle file missing or redirected: {relative}")
                archived = source.extractfile(member)
                if archived is None:
                    raise ValueError(f"missing Sparkle archive payload: {relative}")
                with archived, target.open("rb") as cached:
                    while block := archived.read(1024 * 1024):
                        if cached.read(len(block)) != block:
                            raise ValueError(f"cached Sparkle file differs from verified archive: {relative}")
                    if cached.read(1):
                        raise ValueError(f"cached Sparkle file has extra bytes: {relative}")
            else:
                raise ValueError(f"unsupported Sparkle archive entry: {relative}")
    actual = {path.relative_to(tools) for path in (tools / "Sparkle.framework").rglob("*")
              if path.is_symlink() or path.is_file()}
    actual.update(Path(name) for name in ("LICENSE", "bin/generate_keys", "bin/sign_update") if (tools / name).exists())
    if actual != expected or not any(path.parts[0] == "Sparkle.framework" for path in expected):
        raise ValueError("cached Sparkle inventory differs from verified archive")


def bundle_sparkle(app: Path, tools: Path, identity: str) -> None:
    """Copy links as links and sign code from the innermost executables outward."""
    source = tools / "Sparkle.framework"
    with (source / "Resources/Info.plist").open("rb") as handle:
        metadata = plistlib.load(handle)
    version_key = "CFBundleShortVersionString"
    if metadata[version_key] != SPARKLE_VERSION:
        raise ValueError("Sparkle framework version does not match the pinned developer tools")
    destination = app / "Contents/Frameworks/Sparkle.framework"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination, symlinks=True)
    notice = app / "Contents/Resources/Third Party Notices/Sparkle-LICENSE.txt"
    notice.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(tools / "LICENSE", notice)
    options = ["--options", "runtime", "--timestamp"] if identity != "-" else ["--options", "0"]
    # Framework archives contain signed helper apps and XPC bundles. Re-sign each
    # executable and then its enclosing bundle, never relying on --deep signing.
    candidates = []
    for path in destination.rglob("*"):
        if path.is_symlink():
            continue
        if path.is_dir() and path.suffix in (".app", ".xpc"):
            candidates.append(path)
        elif path.is_file():
            with path.open("rb") as handle:
                magic = handle.read(4)
            if magic in (bytes.fromhex("cafebabe"), bytes.fromhex("cffaedfe"), bytes.fromhex("feedfacf")):
                candidates.append(path)
    for path in sorted(candidates, key=lambda entry: len(entry.parts), reverse=True) + [destination]:
        subprocess.run(["/usr/bin/codesign", "--force", "--sign", identity, *options, str(path)], check=True)
    subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(destination)], check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Verify Sparkle cache against an already SHA-256-verified archive")
    parser.add_argument("archive", type=Path)
    parser.add_argument("tools", type=Path)
    arguments = parser.parse_args()
    verify_cache(arguments.archive, arguments.tools)
