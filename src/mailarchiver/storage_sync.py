# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""File publication primitives shared by the archive writer and metadata stores.

Purpose: Publish flushed files with platform-appropriate rename durability.

How it works:
Replace files atomically, flush POSIX directories and use Windows write-through publication
with bounded retries.

Fundamental dependents:

- ``mbox``: canonical journal/publication steps depend on these storage guarantees.
- ``bagit``: checkpoint files must use the same durable replacement boundary.
"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
from time import monotonic, sleep


def sync_directory(path: Path) -> None:
    """Flush POSIX directory metadata; Windows publication uses MoveFileExW."""
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def replace_file(source: Path, destination: Path) -> None:
    """Replace on the same volume, requesting Windows write-through completion."""
    if os.name == "nt":
        native = ctypes.WinDLL("kernel32", use_last_error=True)
        native.MoveFileExW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
        native.MoveFileExW.restype = ctypes.c_int
        deadline = monotonic() + 2
        while not native.MoveFileExW(str(source), str(destination), 0x9):
            error = ctypes.get_last_error()
            # Windows readers and antivirus may briefly deny DELETE sharing.
            # Keep the complete temporary file and retry only these native errors.
            if error not in {5, 32, 33} or monotonic() >= deadline:
                raise ctypes.WinError(error)
            sleep(0.01)
    else:
        source.replace(destination)
        sync_directory(destination.parent)
