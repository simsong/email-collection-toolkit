# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Wake blocking owner waits when the console delivers an interruption.
# Python 3.12 Windows locks cannot rely on signal interruption alone.
# An owned native handler schedules the existing Python signal handler first.
# It then completes a shared notification future so that Python can dispatch it.
# POSIX may deliver SIGINT to any thread (macOS often does); only a signal that
# lands on the owner thread interrupts its lock wait. Python's C-level handler
# always writes the signal to the wakeup fd, so a reader thread completes the
# same notification future and the owner runs the Python handler at once.
# The callback remains retained for this process's lifetime, avoiding dangling ABI pointers.
# No console is attached or machine setting changed; GUI stop events remain separate.
"""Native console notifications for timer-free completion waits."""
from __future__ import annotations

import _thread
import ctypes
import os
import signal
from threading import Lock, Thread, current_thread, main_thread
from typing import Any

from .change_signal import ChangeSignal

_changed = ChangeSignal()
_lock = Lock()
_handler: Any = None
_posix_registered = False


def console_signal() -> ChangeSignal | None:
    """Register once on the owner thread, retaining the native callback until exit."""
    if current_thread() is not main_thread():
        return None
    if os.name != "nt":
        return _posix_console_signal()
    return _windows_console_signal()


def _posix_console_signal() -> ChangeSignal | None:
    global _posix_registered  # pylint: disable=global-statement
    with _lock:
        if not _posix_registered:
            reader, writer = os.pipe()
            os.set_blocking(writer, False)
            try:
                previous = signal.set_wakeup_fd(writer, warn_on_full_buffer=False)
            except ValueError:
                previous = -2  # Not the main thread of the main interpreter.
            if previous != -1:
                # Someone else (e.g. an asyncio loop) owns the wakeup fd; keep theirs.
                if previous >= 0:
                    signal.set_wakeup_fd(previous)
                os.close(reader)
                os.close(writer)
                return None
            Thread(target=_forward_wakeups, args=(reader,), name="console-signal", daemon=True).start()
            _posix_registered = True
    return _changed


def _forward_wakeups(reader: int) -> None:
    while os.read(reader, 512):
        _changed.notify()


def _windows_console_signal() -> ChangeSignal | None:
    global _handler  # pylint: disable=global-statement
    if os.name != "nt":  # Also narrows the platform for type checkers.
        return None
    with _lock:
        if _handler is None:
            native = ctypes.WinDLL("kernel32", use_last_error=True)
            native.GetConsoleCP.restype = ctypes.c_uint32
            if native.GetConsoleCP() == 0:
                return None
            callback_type = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint32)
            native.SetConsoleCtrlHandler.argtypes = [callback_type, ctypes.c_int]
            native.SetConsoleCtrlHandler.restype = ctypes.c_int

            def interrupted(control: int) -> bool:
                if control not in (0, 1):
                    return False
                number = signal.SIGINT if control == 0 else signal.SIGBREAK
                handler = signal.getsignal(number)
                if handler == signal.SIG_IGN:
                    return True
                if handler == signal.SIG_DFL:
                    return False
                _thread.interrupt_main(number)
                _changed.notify()
                return True

            _handler = callback_type(interrupted)
            if not native.SetConsoleCtrlHandler(_handler, True):
                _handler = None
                raise ctypes.WinError(ctypes.get_last_error())
    return _changed
