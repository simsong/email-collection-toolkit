# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Start the packaged Python webview reader without a Rust host or helper protocol.
# MSIX invokes this module with its isolated private CPython interpreter.
# Package checks generate only new synthetic collections and durable reports.
# Normal startup shares the exact GUI entry point used by the macOS application.
# Windows activation arguments are preserved for archive and native smoke checks.
"""Isolated Python desktop activation for the Windows package."""
from __future__ import annotations

import multiprocessing
from pathlib import Path
import sys


def main() -> int:
    """Dispatch explicit package checks before loading the native GUI."""
    multiprocessing.freeze_support()
    if len(sys.argv) == 3 and sys.argv[1] == "--msix-test":
        from .reader_fixture import check_reader
        check_reader(Path(sys.argv[2]), installed=True)
        return 0
    from .gui_app import main as gui_main
    return gui_main()


if __name__ == "__main__":
    raise SystemExit(main())
