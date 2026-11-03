# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Locate the external read-only Outlook importers without loading them in Python.
# Frozen macOS builds and private Windows Python packages have different roots.
# Both layouts retain the converter's own directory and native dependencies.
# Development checkouts use the built Rust tools and separately locked converter.
# Explicit curator configuration takes precedence; PATH remains the last fallback.
# Callers supervise these executables and retain their source/evidence guarantees.
"""Shared executable discovery for source and packaged Outlook acquisition."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys


def importer_executable(name: str, configured: str | None = None) -> Path:
    """Resolve the same helper names in frozen, private-Python and source layouts."""
    if configured:
        return Path(configured).resolve(strict=True)
    suffix = ".exe" if os.name == "nt" else ""
    relative = Path(name) / (name + suffix) if name == "pff-converter" else Path(name + suffix)
    root = Path(__file__).resolve().parents[2]
    packaged = (Path(getattr(sys, "_MEIPASS")) / "importers" if getattr(sys, "frozen", False)
                else Path(sys.executable).resolve().parent / "importers")
    development = (root / "converters/pff/.venv" / ("Scripts" if os.name == "nt" else "bin") / (name + suffix)
                   if name == "pff-converter" else root / "target/release" / (name + suffix))
    for candidate in (packaged / relative, development):
        if candidate.is_file():
            return candidate.resolve()
    if found := shutil.which(name):
        return Path(found).resolve()
    raise FileNotFoundError(f"{name} unavailable; build the importer tools or configure its executable")
