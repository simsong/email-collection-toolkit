# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Launch the development GUI with its own macOS application identity.
# A project-local signed bundle reuses the active virtual environment and runtime.
# Its real Mach-O executable gives LaunchServices the product name instead of Python.
# Each launch gets a separate bundle so it cannot overwrite a running executable.
# CPython's launcher environment selects the venv without adding unsealable files.
# Standard streams and arguments survive exec; no installation or system setting changes.
# Other platforms keep the ordinary module launch through the same interpreter.
"""Development GUI launcher with a genuine macOS application bundle."""
from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
from importlib.metadata import version
from pathlib import Path

from mailarchiver.identity import APPLICATION_NAME, DEVELOPMENT_BUNDLE_IDENTIFIER
from mailarchiver.release_versions import release_metadata

ROOT = Path(__file__).resolve().parents[1]
BUNDLE_EXECUTABLE = "CFBundleExecutable"
BUNDLE_NAME = "CFBundleName"
BUNDLE_DISPLAY_NAME = "CFBundleDisplayName"
BUNDLE_IDENTIFIER = "CFBundleIdentifier"
BUNDLE_TYPE = "CFBundlePackageType"
BUNDLE_VERSION = "CFBundleShortVersionString"
BUNDLE_BUILD = "CFBundleVersion"
VENV_LAUNCHER = "__PYVENV_LAUNCHER__"
PYTHON_EXECUTABLE = "PYTHONEXECUTABLE"


def development_environment() -> dict[str, str]:
    """Tell CPython's macOS launcher to keep the active environment and worker executable."""
    environment = os.environ.copy()
    environment.pop(PYTHON_EXECUTABLE, None)
    environment[VENV_LAUNCHER] = sys.executable
    return environment


def development_bundle(directory: Path) -> Path:
    """Seal the native bundle after writing its identity; leave the active venv outside it."""
    contents = directory / f"{APPLICATION_NAME}.app/Contents"
    executable = contents / "MacOS/ECT"
    executable.parent.mkdir(parents=True)
    framework = Path(sys.base_prefix) / "Resources/Python.app/Contents/MacOS/Python"
    source = framework if framework.is_file() else Path(sys.executable).resolve()
    shutil.copy2(source, executable)
    _, _, build, display = release_metadata(version("mailarchiver"))
    with (contents / "Info.plist").open("wb") as handle:
        plistlib.dump({
            BUNDLE_EXECUTABLE: executable.name, BUNDLE_NAME: APPLICATION_NAME,
            BUNDLE_DISPLAY_NAME: APPLICATION_NAME, BUNDLE_IDENTIFIER: DEVELOPMENT_BUNDLE_IDENTIFIER,
            BUNDLE_TYPE: "APPL", BUNDLE_VERSION: display, BUNDLE_BUILD: str(build),
        }, handle)
    app = contents.parent
    subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", str(app)],
                   check=True, capture_output=True, text=True, timeout=20)
    subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(app)],
                   check=True, capture_output=True, text=True, timeout=20)
    return executable


def main() -> None:
    """Keep the active interpreter on other platforms; use a native bundle on macOS."""
    executable = Path(sys.executable)
    if sys.platform == "darwin":
        temporary = ROOT / ".tmp"
        temporary.mkdir(exist_ok=True)
        executable = development_bundle(Path(tempfile.mkdtemp(prefix="gui-launch-", dir=temporary)))
    os.execve(executable, [str(executable), "-m", "mailarchiver.gui_app", *sys.argv[1:]],
              development_environment() if sys.platform == "darwin" else os.environ)


if __name__ == "__main__":
    main()
