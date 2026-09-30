# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Preserve and sign the complete pinned native Sparkle framework."""

import plistlib
import shutil
import subprocess
from pathlib import Path

from mailarchiver.update_metadata import SPARKLE_VERSION


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
