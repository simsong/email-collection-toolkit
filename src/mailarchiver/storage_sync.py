# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Publish already-flushed archive files using each platform's durable rename.
# POSIX additionally flushes directory metadata after namespace changes.
# Windows uses a write-through rename because directory fsync is unavailable.
# Callers still flush file contents before publication and retain recovery intent.
# Failed publication propagates; it must never be treated as a completed write.
"""File publication primitives shared by the archive writer and metadata stores."""
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
