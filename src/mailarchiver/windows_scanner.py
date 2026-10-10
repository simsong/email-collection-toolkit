# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Bootstrap the native scanner before Python imports its own OpenSSL libraries.
# Windows resolves already loaded DLL basenames before private dependency paths.
# A separate interpreter therefore loads ClamAV before importing application code.
# Inherited anonymous pipes retain the existing typed scanner protocol.
# An inherited owner handle makes abrupt GUI exit terminate the worker as well.
# This module also provides a bounded version probe without loading DLLs in the GUI.
"""Private Windows scanner process and early native loader."""
from __future__ import annotations

import ctypes as c
from ctypes import wintypes as w
import os
from pathlib import Path
import subprocess
import sys
from threading import Thread
from typing import Protocol


class ScannerConnection(Protocol):
    """Common public pipe operations across Windows and POSIX transports."""

    def fileno(self) -> int: ...
    def send(self, obj: object) -> None: ...
    def recv(self) -> object: ...
    def poll(self, timeout: float = 0) -> bool: ...
    def close(self) -> None: ...


class ScannerProcess:
    """The process operations used by the shared scanner lifecycle."""

    process: subprocess.Popen[bytes]

    def __init__(self, connection: ScannerConnection, library: Path) -> None:
        if sys.platform != "win32":
            raise RuntimeError("The isolated Windows scanner requires Windows")
        native = c.WinDLL("kernel32", use_last_error=True)
        native.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        native.OpenProcess.restype = w.HANDLE
        native.CloseHandle.argtypes = [w.HANDLE]
        native.CloseHandle.restype = w.BOOL
        owner = native.OpenProcess(0x100000, True, os.getpid())  # SYNCHRONIZE only.
        if not owner:
            raise c.WinError(c.get_last_error())
        pipe = connection.fileno()
        try:
            os.set_handle_inheritable(pipe, True)
            startup = subprocess.STARTUPINFO()
            startup.lpAttributeList = {"handle_list": [pipe, int(owner)]}
            self.process = subprocess.Popen(
                [sys.executable, "-I", str(Path(__file__).absolute()), str(library.absolute()),
                 str(pipe), str(int(owner))],
                startupinfo=startup, close_fds=True, creationflags=subprocess.CREATE_NO_WINDOW,
            )
        finally:
            os.set_handle_inheritable(pipe, False)
            native.CloseHandle(owner)

    @property
    def pid(self) -> int:
        return self.process.pid

    def is_alive(self) -> bool:
        return self.process.poll() is None

    def terminate(self) -> None:
        self.process.terminate()

    def kill(self) -> None:
        self.process.kill()

    def join(self, timeout: float | None = None) -> None:
        try:
            self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            pass


def probe_version(library: Path) -> str:
    """Keep Python's already loaded SSL DLLs out of the native version probe."""
    if sys.platform != "win32":
        raise RuntimeError("The isolated Windows scanner requires Windows")
    completed = subprocess.run(
        [sys.executable, "-I", str(Path(__file__).absolute()), str(library.absolute())],
        capture_output=True, text=True, timeout=15, check=False,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if completed.returncode:
        raise OSError(completed.stderr.strip() or "ClamAV version probe failed")
    return completed.stdout.strip()


def _watch_owner(owner: int) -> None:
    if sys.platform != "win32":
        raise RuntimeError("The isolated Windows scanner requires Windows")
    native = c.WinDLL("kernel32", use_last_error=True)
    native.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
    native.WaitForSingleObject.restype = w.DWORD
    native.WaitForSingleObject(owner, 0xFFFFFFFF)
    os._exit(0)


def main() -> None:
    """Load the official dependency set before any application or SSL imports."""
    if sys.platform != "win32":
        raise RuntimeError("The isolated Windows scanner requires Windows")
    library = Path(sys.argv[1])
    if len(sys.argv) == 4:
        Thread(target=_watch_owner, args=(int(sys.argv[3]),), daemon=True).start()
    native = c.WinDLL("kernel32", use_last_error=True)
    native.SetErrorMode.argtypes = [w.UINT]
    native.SetErrorMode.restype = w.UINT
    native.SetErrorMode(0x8003)  # Native loader failures must not open modal dialogs.
    error = ""
    loaded = None
    try:
        if not library.is_file():
            raise FileNotFoundError(f"ClamAV library is missing or not a file: {library}")
        loaded = c.CDLL(str(library))
    except OSError as failure:
        if library.is_file():
            error = f"ClamAV library is present ({library.stat().st_size} bytes) but cannot be loaded: {library}: {failure}"
        else:
            error = str(failure)
    if len(sys.argv) == 2:
        if error:
            raise OSError(error)
        assert loaded is not None
        loaded.cl_retver.restype = c.c_char_p
        print(loaded.cl_retver().decode("ascii"))
        return
    # Explicit package location works with both source and isolated MSIX runtimes.
    sys.path.insert(0, str(Path(__file__).absolute().parents[1]))
    from multiprocessing.connection import PipeConnection
    from mailarchiver.scan_evidence import ScanEvidence
    from mailarchiver.scanner import engine_worker
    connection = PipeConnection(int(sys.argv[2]))
    try:
        if error:
            connection.send(ScanEvidence(status="scanner-error", detail=error))
            return
        definitions, workers, temporary_directory = connection.recv()
        engine_worker(connection, library, definitions, workers, temporary_directory)
    finally:
        connection.close()


if __name__ == "__main__":
    main()
