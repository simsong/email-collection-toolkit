# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

# Purpose: Construct portable SQLite URI filenames without weakening access modes.
# How it works:
# Escape literal path characters and retain UNC servers in the path rather than the URI
# authority.
# Fundamental dependents:
# - application: archive validation must open the intended local/network database.
# - mailsearch: read-only queries require the same exact path and locking semantics.

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
