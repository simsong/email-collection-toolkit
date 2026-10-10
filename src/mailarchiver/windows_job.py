# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Bind a one-shot Windows supervisor and its descendants to a native job object.
# The job kills every member when its last handle closes, including on crashes.
# The supervisor owns the only handle and joins before it launches any utility.
# This closes the child-start race and prevents abandoned definition updaters.
# No background service, scheduled task, or machine-wide configuration is used.
"""Lifetime containment for explicitly launched Windows utilities."""
from __future__ import annotations

import ctypes as c
from ctypes import wintypes as w
import os


class BasicLimits(c.Structure):
    _fields_ = [("process_time", c.c_int64), ("job_time", c.c_int64),
               ("flags", w.DWORD), ("minimum_working_set", c.c_size_t),
               ("maximum_working_set", c.c_size_t), ("active_processes", w.DWORD),
               ("affinity", c.c_size_t), ("priority", w.DWORD), ("scheduling", w.DWORD)]


class IoCounters(c.Structure):
    _fields_ = [(name, c.c_uint64) for name in
               ("reads", "writes", "other", "read_bytes", "write_bytes", "other_bytes")]


class ExtendedLimits(c.Structure):
    _fields_ = [("basic", BasicLimits), ("io", IoCounters),
               ("process_memory", c.c_size_t), ("job_memory", c.c_size_t),
               ("peak_process_memory", c.c_size_t), ("peak_job_memory", c.c_size_t)]


def contain_current_process() -> int:
    """Return the supervisor-owned handle; process exit closes it automatically."""
    if os.name != "nt":
        raise RuntimeError("Native process containment requires Windows")
    native = c.WinDLL("kernel32", use_last_error=True)
    native.CreateJobObjectW.argtypes = [c.c_void_p, w.LPCWSTR]
    native.CreateJobObjectW.restype = w.HANDLE
    native.SetInformationJobObject.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]
    native.SetInformationJobObject.restype = w.BOOL
    native.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
    native.AssignProcessToJobObject.restype = w.BOOL
    native.GetCurrentProcess.argtypes = []
    native.GetCurrentProcess.restype = w.HANDLE
    native.CloseHandle.argtypes = [w.HANDLE]
    native.CloseHandle.restype = w.BOOL
    job = native.CreateJobObjectW(None, None)
    if not job:
        raise c.WinError(c.get_last_error())
    limits = ExtendedLimits()
    limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE.
    if not native.SetInformationJobObject(job, 9, c.byref(limits), c.sizeof(limits)):
        error = c.get_last_error()
        native.CloseHandle(job)
        raise c.WinError(error)
    if not native.AssignProcessToJobObject(job, native.GetCurrentProcess()):
        error = c.get_last_error()
        native.CloseHandle(job)
        raise c.WinError(error)
    return int(job)
