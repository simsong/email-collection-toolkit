# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""File publication primitives shared by the archive writer and metadata stores.

Purpose: Publish flushed files with platform-appropriate rename durability.

How it works:
Replace files atomically, exclusively rename new files/directories, flush POSIX directories
and use Windows write-through replacement with explicit reader sharing. On macOS,
``fsync`` only hands data to the drive; ``durable_sync`` uses ``F_FULLFSYNC`` so
the drive also flushes its write cache, and SQLite connections enable the same.

Fundamental dependents:

- ``mbox``: canonical journal/publication steps depend on these storage guarantees.
- ``bagit``: checkpoint files must use the same durable replacement boundary.
- ``clamav_update`` and ``pdf_mail``: new outputs must refuse competing destinations.
"""
from __future__ import annotations

import ctypes
import errno
import os
import sqlite3
import sys
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None  # type: ignore[assignment]

# Only macOS needs (and defines) F_FULLFSYNC; elsewhere fsync reaches stable storage.
_FULLFSYNC: int | None = getattr(fcntl, "F_FULLFSYNC", None) if sys.platform == "darwin" else None


def durable_sync(descriptor: int) -> None:
    """Flush a file or directory to stable storage, including the drive's write cache.

    Falls back to ``fsync`` where ``F_FULLFSYNC`` is refused (some network and
    FUSE file systems), as SQLite does.
    """
    if _FULLFSYNC is not None and fcntl is not None:
        try:
            fcntl.fcntl(descriptor, _FULLFSYNC)
            return
        except OSError:
            pass
    os.fsync(descriptor)


def durable_sqlite(database: sqlite3.Connection) -> sqlite3.Connection:
    """Make SQLite commits use F_FULLFSYNC on macOS (no effect elsewhere)."""
    if _FULLFSYNC is not None:
        database.execute("PRAGMA fullfsync=ON")
        database.execute("PRAGMA checkpoint_fullfsync=ON")
    return database


def publish_exclusive(source: Path, destination: Path) -> None:
    """Atomically rename without replacing a file, symlink or even an empty directory."""
    if os.name == "nt":
        source.rename(destination)
        return
    native = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        rename = native.renamex_np
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        result = rename(os.fsencode(source), os.fsencode(destination), 4)  # RENAME_EXCL
    elif sys.platform.startswith("linux"):
        rename = native.renameat2
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        result = rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1)  # AT_FDCWD, RENAME_NOREPLACE
    else:
        raise OSError(errno.ENOTSUP, "Exclusive publication is unsupported")
    if result:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(destination))


def sync_directory(path: Path) -> None:
    """Flush POSIX directory metadata; Windows publication uses MoveFileExW."""
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        durable_sync(descriptor)
    finally:
        os.close(descriptor)


def replace_file(source: Path, destination: Path) -> None:
    """Replace on the same volume, requesting Windows write-through completion."""
    if os.name == "nt":
        native = ctypes.WinDLL("kernel32", use_last_error=True)
        native.MoveFileExW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
        native.MoveFileExW.restype = ctypes.c_int
        if not native.MoveFileExW(str(source), str(destination), 0x9):
            raise ctypes.WinError(ctypes.get_last_error())
    else:
        source.replace(destination)
        sync_directory(destination.parent)
