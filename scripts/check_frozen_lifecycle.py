# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Verify desktop lifecycle adapter discovery in a real frozen macOS executable.
# Reuse the production builder's Python collection flags without extra imports.
# The probe imports only the common controller, then installs native events.
# Synthetic Workspace notifications exercise session expiry, wake and cleanup.
# Run outside the checkout without a Python source-path environment fallback.
# This component check does not sign, package or publish a DMG.
"""Freeze and execute the common controller's native lifecycle boundary."""

import subprocess
import sys
import tempfile
from pathlib import Path

from build_macos import ROOT, freezer_import_arguments
from macos_signing import release_safe_environment
import os


PROBE = '''# Frozen lifecycle probe for native adapter discovery.
# Import the common controller without naming either native adapter.
# Native installation must resolve from the frozen module archive.
# Workspace notifications change only this process's observers.
# Disposal must remove native subscriptions before a late wake arrives.
import sys
from importlib import import_module
from mailarchiver.change_signal import ChangeEvent
from mailarchiver.desktop_lifecycle import DesktopLifecycle
assert getattr(sys, "frozen", False)
refreshed = []
lifecycle = DesktopLifecycle(lambda: refreshed.append(True))
lifecycle.start("darwin")
adapter = lifecycle._subscription
assert adapter is not None, "Frozen application omitted its native lifecycle adapter"
source = adapter._source
assert source is not None, "Frozen application did not install network notifications"
core = import_module("CoreFoundation")
assert core.CFRunLoopSourceIsValid(source)
workspace = import_module("AppKit").NSWorkspace.sharedWorkspace()
center = workspace.notificationCenter()
appkit = import_module("AppKit")
refreshed.clear()
session = lifecycle.transport.session(ChangeEvent())
assert session is not None and not session.done()
try:
    center.postNotificationName_object_(appkit.NSWorkspaceWillSleepNotification, workspace)
    assert session.done()
    center.postNotificationName_object_(appkit.NSWorkspaceDidWakeNotification, workspace)
    assert refreshed == [True]
    replacement = lifecycle.transport.session(ChangeEvent())
    assert replacement is not None and not replacement.done()
finally:
    lifecycle.close()
assert not core.CFRunLoopSourceIsValid(source)
center.postNotificationName_object_(appkit.NSWorkspaceDidWakeNotification, workspace)
assert refreshed == [True]
print("Frozen native lifecycle installation, sleep/wake and cleanup passed.")
'''


def main() -> None:
    """Build and run outside the checkout, using the actual production import flags."""
    if sys.platform != "darwin":
        raise SystemExit("Frozen macOS lifecycle validation requires macOS")
    with tempfile.TemporaryDirectory(prefix="frozen-lifecycle-") as temporary:
        work = Path(temporary)
        entry = work / "entry.py"
        entry.write_text(PROBE, encoding="utf-8")
        subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
            "--name", "lifecycle-probe", "--distpath", str(work / "dist"),
            "--workpath", str(work / "build"), "--specpath", str(work),
            *freezer_import_arguments(), str(entry)], cwd=ROOT, check=True, timeout=300)
        executable = work / "dist/lifecycle-probe/lifecycle-probe"
        subprocess.run([str(executable)], cwd=work, check=True, timeout=30,
                       env=release_safe_environment(os.environ, ("PYTHON",)))


if __name__ == "__main__":
    main()
