# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Isolated Python desktop activation for the Windows package.

Purpose: Activate the isolated Python desktop in Windows packages.

How it works:
Dispatch installed self-tests or normal startup through the shared Python GUI entry point.

Fundamental dependents:

- ``windows_self_test``: installed activation must reach its package acceptance mode.
- ``gui_app``: Windows package launches route into its normal desktop lifecycle.
"""
from __future__ import annotations

import multiprocessing
from pathlib import Path
import sys


def main() -> int:
    """Dispatch explicit package checks before loading the native GUI."""
    multiprocessing.freeze_support()
    if len(sys.argv) == 3 and sys.argv[1] == "--msix-test":
        from .windows_self_test import exercise
        exercise(Path(sys.argv[2]), installed=True)
        return 0
    from .gui_app import main as gui_main
    return gui_main()


if __name__ == "__main__":
    raise SystemExit(main())
