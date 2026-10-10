# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Construct SQLite filenames for local, mapped-drive and UNC archive paths.
# Keep a network server inside the URI path instead of the URI authority.
# Python's bundled SQLite rejects non-local URI authorities by default.
# Preserve percent escaping for spaces, Unicode, hashes and query delimiters.
# Read-only callers retain mode=ro; recovery may explicitly request mode=rw.
# Do not disable SQLite locking or change detection for network collections.
"""Portable SQLite URI filenames for the shared Python application."""
from pathlib import Path, PurePath, PureWindowsPath
from typing import Literal
from urllib.parse import quote


def sqlite_uri(path: PurePath, *, mode: Literal["ro", "rw"] = "ro") -> str:
    """Encode an absolute filename without using a non-local URI authority."""
    absolute = path if path.is_absolute() else Path(path).absolute()
    filename = absolute.as_posix()
    if isinstance(absolute, PureWindowsPath) and not filename.startswith("/"):
        filename = "/" + filename
    # Always use an empty authority. A UNC path's leading // remains part of
    # the filename passed to the Windows VFS, retaining normal SQLite locks.
    return f"file://{quote(filename, safe='/:')}?mode={mode}"
