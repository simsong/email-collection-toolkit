# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Supply native file handles and byte-range locks for Windows archive writers.
# Lock files are opened without following reparse points and cannot be deleted.
# Directory handles pin the selected path while a writer lease is held.
# Shared locks fence application writes against exclusive update installation.
# Windows releases all handles and their locks if the owning process exits.
"""Windows storage primitives; imported only on Windows."""
from __future__ import annotations

import ctypes as c
from ctypes import wintypes as w
from contextlib import contextmanager
from collections.abc import Iterator
from importlib import import_module
import os
from pathlib import Path
from typing import BinaryIO


class FileInformation(c.Structure):
    _fields_ = [("attributes", w.DWORD), ("created", w.FILETIME),
               ("accessed", w.FILETIME), ("written", w.FILETIME),
               ("volume", w.DWORD), ("size_high", w.DWORD), ("size_low", w.DWORD),
               ("links", w.DWORD), ("index_high", w.DWORD), ("index_low", w.DWORD)]


class Overlapped(c.Structure):
    _fields_ = [("internal", c.c_size_t), ("internal_high", c.c_size_t),
               ("offset", w.DWORD), ("offset_high", w.DWORD), ("event", w.HANDLE)]


def kernel() -> c.CDLL:
    if os.name != "nt":
        raise RuntimeError("Native Windows storage requires Windows")
    native = c.WinDLL("kernel32", use_last_error=True)
    native.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, c.c_void_p, w.DWORD, w.DWORD, w.HANDLE]
    native.CreateFileW.restype = w.HANDLE
    native.CloseHandle.argtypes = [w.HANDLE]
    native.CloseHandle.restype = w.BOOL
    native.GetFileInformationByHandle.argtypes = [w.HANDLE, c.POINTER(FileInformation)]
    native.GetFileInformationByHandle.restype = w.BOOL
    native.LockFileEx.argtypes = [w.HANDLE, w.DWORD, w.DWORD, w.DWORD, w.DWORD, c.POINTER(Overlapped)]
    native.LockFileEx.restype = w.BOOL
    native.UnlockFileEx.argtypes = [w.HANDLE, w.DWORD, w.DWORD, w.DWORD, c.POINTER(Overlapped)]
    native.UnlockFileEx.restype = w.BOOL
    return native


def open_handle(path: Path, *, directory: bool) -> int:
    """Reject reparse points using the opened object, not a racy pathname check."""
    if os.name != "nt":
        raise RuntimeError("Native Windows storage requires Windows")
    native = kernel()
    # Share read/write, never delete. OPEN_REPARSE_POINT avoids following aliases.
    handle = native.CreateFileW(str(path), 0 if directory else 0xC0000000, 3, None,
                                3 if directory else 4, 0x02200000, None)
    if handle == c.c_void_p(-1).value:
        raise c.WinError(c.get_last_error())
    try:
        info = FileInformation()
        if not native.GetFileInformationByHandle(handle, c.byref(info)):
            raise c.WinError(c.get_last_error())
        if info.attributes & 0x400 or bool(info.attributes & 0x10) != directory:
            raise ValueError(f"Unsafe Windows archive path: {path}")
        if not directory and info.links != 1:
            raise ValueError(f"Archive lock must have one link: {path}")
        return int(handle)
    except BaseException:
        native.CloseHandle(handle)
        raise


@contextmanager
def pin_directories(path: Path) -> Iterator[None]:
    """Keep every ancestor from being renamed or replaced by a junction."""
    absolute = Path(os.path.abspath(path))
    handles: list[int] = []
    try:
        for parent in (*reversed(absolute.parents), absolute):
            handles.append(open_handle(parent, directory=True))
        yield
    finally:
        for handle in reversed(handles):
            kernel().CloseHandle(handle)


def open_lock(path: Path) -> BinaryIO:
    """Transfer one native lock handle to Python's binary file owner."""
    if os.name != "nt":
        raise RuntimeError("Native Windows storage requires Windows")
    handle = open_handle(path, directory=False)
    try:
        descriptor = import_module("msvcrt").open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
    except BaseException:
        kernel().CloseHandle(handle)
        raise
    return os.fdopen(descriptor, "r+b", buffering=0)


def lock(handle: BinaryIO, *, shared: bool = False, blocking: bool = False) -> None:
    """Lock beyond metadata bytes so diagnostics remain readable by contenders."""
    if os.name != "nt":
        raise RuntimeError("Native Windows storage requires Windows")
    overlap = Overlapped(offset=0x7FFFFFFF)
    flags = (0 if shared else 2) | (0 if blocking else 1)
    raw = import_module("msvcrt").get_osfhandle(handle.fileno())
    if not kernel().LockFileEx(raw, flags, 0, 1, 0, c.byref(overlap)):
        error = c.get_last_error()
        if error in (32, 33):
            raise BlockingIOError("Another process holds the file lock")
        raise c.WinError(error)


def unlock(handle: BinaryIO) -> None:
    if os.name != "nt":
        raise RuntimeError("Native Windows storage requires Windows")
    overlap = Overlapped(offset=0x7FFFFFFF)
    raw = import_module("msvcrt").get_osfhandle(handle.fileno())
    if not kernel().UnlockFileEx(raw, 0, 1, 0, c.byref(overlap)):
        raise c.WinError(c.get_last_error())
