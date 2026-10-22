# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Validate dynamic acquisition imports in a disposable frozen executable.
# Use the production builder's manifest-derived hidden imports and plugin data.
# Freeze a minimal headless CLI without signing or publishing an application.
# Ingest only synthetic EML from a private temporary directory.
# Read the resulting canonical MBOX to verify exact message-byte preservation.
# This catches packaging omissions that source-checkout tests cannot detect.
"""Frozen-plugin packaging requirement: local ingest loads every built-in adapter."""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from mailarchiver.mailsearch import read_message_bytes
from build_macos import ROOT, plugin_import_arguments


def main() -> None:
    """Build and execute actual PyInstaller output without a source-path fallback."""
    with tempfile.TemporaryDirectory(prefix="frozen-source-plugins-") as temporary:
        work = Path(temporary)
        entry = work / "entry.py"
        entry.write_text(
            "# Synthetic frozen CLI entry for acquisition packaging validation.\n"
            "# Require the PyInstaller runtime and dispatch the production CLI.\n"
            "# Builtin manifests are loaded from bundled trusted resources.\n"
            "# Arguments select a disposable archive and read-only synthetic input.\n"
            "# No GUI, live provider credentials or user archive is accessed.\n"
            "import sys\nassert getattr(sys, 'frozen', False)\n"
            "from mailarchiver.__main__ import main\nraise SystemExit(main())\n", encoding="utf-8")
        subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
            "--name", "source-probe", "--distpath", str(work / "dist"), "--workpath", str(work / "build"),
            "--specpath", str(work), "--copy-metadata", "mailarchiver", "--collect-data", "mailarchiver",
            *plugin_import_arguments(), "--hidden-import", "mailarchiver.processing.builtin",
            "--exclude-module", "pypff", "--add-data", f"{ROOT / 'src/mailarchiver/plugins'}:mailarchiver/plugins",
            str(entry)], cwd=ROOT, check=True)
        raw = (b"From: fixture@example.test\nDate: Mon, 01 Jan 2024 12:00:00 +0000\n"
               b"Subject: Frozen source registry\n\nSynthetic preserved bytes.\n")
        source = work / "source.eml"
        source.write_bytes(raw)
        owners = work / "owners.txt"
        owners.write_text("owner@example.test\n", encoding="utf-8")
        archive = work / "archive"
        executable = work / "dist/source-probe" / ("source-probe.exe" if sys.platform == "win32" else "source-probe")
        subprocess.run([str(executable), "--archive", str(archive), "ingest", "--no-scan",
                        "--owner-names-file", str(owners), str(source)], cwd=work, check=True)
        if source.read_bytes() != raw or read_message_bytes(archive, 1) != raw:
            raise AssertionError("frozen ingest changed source or canonical message bytes")
        print("Frozen source-plugin ingest preserved fixture bytes.")


if __name__ == "__main__":
    main()
