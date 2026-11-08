# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Wake blocking owner waits when Windows delivers a console interruption.
# Python 3.12 Windows locks cannot rely on signal interruption alone.
# An owned native handler schedules the existing Python signal handler first.
# It then completes a shared notification future so that Python can dispatch it.
# The callback remains retained for this process's lifetime, avoiding dangling ABI pointers.
# No console is attached or machine setting changed; GUI stop events remain separate.
"""Native console notifications for timer-free Windows completion waits."""
from __future__ import annotations

import _thread
import ctypes
import os
import signal
from threading import Lock, current_thread, main_thread
from typing import Any

from .change_signal import ChangeSignal

_changed = ChangeSignal()
_lock = Lock()
_handler: Any = None


def console_signal() -> ChangeSignal | None:
    """Register once on the owner thread, retaining the native callback until exit."""
    global _handler  # pylint: disable=global-statement
    if os.name != "nt" or current_thread() is not main_thread():
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
