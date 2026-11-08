# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Wait for filesystem state changes through native kernel notifications.
# Register watches before reading a predicate so publication cannot be missed.
# Windows change handles, BSD kqueue and Linux inotify provide blocking wakeups.
# A separate cancellation handle releases an owned waiter during teardown.
# Notification means recheck state; only the predicate establishes completion.
# Deadlines raise errors and are never periodic polling or scheduling ticks.
# Application observers and portable regression fixtures share this boundary.
"""Owned, cancellable filesystem notifications without polling timers."""
from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager
from concurrent.futures import CancelledError
import ctypes
from importlib import import_module
import math
import os
from pathlib import Path
from selectors import DefaultSelector, EVENT_READ
import sys
from threading import Event, Lock, Thread
from time import monotonic
from typing import Any, Self
import subprocess
from weakref import ref

from .change_signal import ChangeEvent


def _existing_parent(path: Path) -> Path:
    parent = path.parent
    while not parent.is_dir():
        if parent == parent.parent:
            raise FileNotFoundError(path)
        parent = parent.parent
    return parent


def _readable(descriptors: Sequence[int], timeout: float | None) -> bool:
    """Wait without select's descriptor-number limit; expiry is never readiness."""
    with DefaultSelector() as selector:
        for descriptor in descriptors:
            selector.register(descriptor, EVENT_READ)
        return bool(selector.select(timeout))


class PathChanges(AbstractContextManager["PathChanges"]):
    """Own native subscriptions; wake cancellation before closing a blocked waiter."""

    def __init__(self, paths: Sequence[Path]) -> None:
        if not paths:
            raise ValueError("At least one path is required")
        self.paths = tuple(path.absolute() for path in paths)
        self.cancelled = Event()
        self._wait_lock = Lock()
        self._cancel_lock = Lock()
        self._closed = False
        self._fds: list[int] = []
        self._handles: list[int] = []
        self._queue: Any = None
        self._native: Any = None
        self._read = self._write = -1
        self._stop_handle = 0
        try:
            if os.name == "nt":
                self._initialize_windows()
            else:
                self._read, self._write = os.pipe()
                os.set_blocking(self._read, False)
                os.set_blocking(self._write, False)
            self._subscribe()
        except BaseException:
            self.close()
            raise

    def __enter__(self) -> Self:
        return self

    def _initialize_windows(self) -> None:
        if os.name != "nt":
            raise RuntimeError("Windows notifications require Windows")
        native = self._native = import_module("ctypes").WinDLL("kernel32", use_last_error=True)
        native.CreateEventW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_wchar_p]
        native.CreateEventW.restype = ctypes.c_void_p
        native.SetEvent.argtypes = [ctypes.c_void_p]
        native.SetEvent.restype = ctypes.c_int
        native.CloseHandle.argtypes = [ctypes.c_void_p]
        native.CloseHandle.restype = ctypes.c_int
        native.FindFirstChangeNotificationW.argtypes = [ctypes.c_wchar_p, ctypes.c_int, ctypes.c_uint32]
        native.FindFirstChangeNotificationW.restype = ctypes.c_void_p
        native.FindNextChangeNotification.argtypes = [ctypes.c_void_p]
        native.FindNextChangeNotification.restype = ctypes.c_int
        native.FindCloseChangeNotification.argtypes = [ctypes.c_void_p]
        native.FindCloseChangeNotification.restype = ctypes.c_int
        native.WaitForMultipleObjects.argtypes = [ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p), ctypes.c_int, ctypes.c_uint32]
        native.WaitForMultipleObjects.restype = ctypes.c_uint32
        self._stop_handle = native.CreateEventW(None, True, False, None)
        if not self._stop_handle:
            raise import_module("ctypes").WinError(import_module("ctypes").get_last_error())

    def _release_subscriptions(self) -> None:
        if self._queue is not None:
            self._queue.close()
            self._queue = None
        for descriptor in self._fds:
            os.close(descriptor)
        self._fds.clear()
        for handle in self._handles:
            self._native.FindCloseChangeNotification(handle)
        self._handles.clear()

    def _subscribe(self) -> None:
        self._release_subscriptions()
        parents = sorted({_existing_parent(path) for path in self.paths})

        def watched_paths() -> Iterator[Path]:
            seen: set[Path] = set()
            for parent in parents:
                seen.add(parent)
                yield parent
            for path in self.paths:
                # Ancestors may have appeared while the original parent was
                # being registered. Discover them only after that subscription.
                for candidate in (_existing_parent(path), path):
                    if candidate not in seen:
                        seen.add(candidate)
                        yield candidate
        if os.name == "nt":
            if len(parents) > 63:
                raise ValueError("Too many Windows notification roots")
            for parent in parents:
                name = str(parent.resolve())
                if not name.startswith("\\\\?\\"):
                    name = "\\\\?\\UNC\\" + name[2:] if name.startswith("\\\\") else "\\\\?\\" + name
                handle = self._native.FindFirstChangeNotificationW(name, True, 0x1F)
                if handle in (None, ctypes.c_void_p(-1).value):
                    raise import_module("ctypes").WinError(import_module("ctypes").get_last_error())
                self._handles.append(handle)
        elif sys.platform.startswith("linux"):
            native = self._native = ctypes.CDLL(None, use_errno=True)
            native.inotify_init1.argtypes = [ctypes.c_int]
            native.inotify_init1.restype = ctypes.c_int
            native.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
            native.inotify_add_watch.restype = ctypes.c_int
            descriptor = native.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
            if descriptor < 0:
                raise OSError(ctypes.get_errno(), "inotify initialization failed")
            self._fds.append(descriptor)
            for path in watched_paths():
                if native.inotify_add_watch(descriptor, os.fsencode(path), 0xFC6) < 0:
                    if ctypes.get_errno() == 2:
                        continue  # The already-registered parent observes later creation.
                    raise OSError(ctypes.get_errno(), f"Cannot watch {path}")
        else:
            api = import_module("select")
            self._queue = api.kqueue()
            self._queue.control([api.kevent(self._read, filter=api.KQ_FILTER_READ, flags=api.KQ_EV_ADD)], 0)
            # Register each parent before inspecting/opening its targets. A batch
            # built from an earlier exists() snapshot can miss a newly created WAL.
            for path in watched_paths():
                try:
                    descriptor = os.open(path, getattr(os, "O_EVTONLY", os.O_RDONLY) | os.O_NONBLOCK)
                except FileNotFoundError:
                    continue
                self._fds.append(descriptor)
                self._queue.control([api.kevent(descriptor, filter=api.KQ_FILTER_VNODE,
                    flags=api.KQ_EV_ADD | api.KQ_EV_CLEAR,
                    fflags=api.KQ_NOTE_WRITE | api.KQ_NOTE_DELETE | api.KQ_NOTE_RENAME)], 0)

    def wait(self, timeout: float | None = None) -> bool:
        """Return on a native change, False on cancellation, or raise on deadline expiry."""
        if timeout is not None and (timeout < 0 or not math.isfinite(timeout)):
            raise ValueError("A notification timeout must be finite and nonnegative")
        with self._wait_lock:
            if self.cancelled.is_set():
                return False
            if os.name == "nt":
                handles = (ctypes.c_void_p * (len(self._handles) + 1))(self._stop_handle, *self._handles)
                milliseconds = 0xFFFFFFFF if timeout is None else min(math.ceil(timeout * 1000), 0xFFFFFFFE)
                result = self._native.WaitForMultipleObjects(len(handles), handles, False, milliseconds)
                if result == 0xFFFFFFFF:
                    raise import_module("ctypes").WinError(import_module("ctypes").get_last_error())
                changed = 0 < result <= len(self._handles)
                if changed and not self._native.FindNextChangeNotification(self._handles[result - 1]):
                    raise import_module("ctypes").WinError(import_module("ctypes").get_last_error())
            elif self._queue is not None:
                changed = bool(self._queue.control(None, 64, timeout))
            else:
                changed = _readable((self._read, self._fds[0]), timeout)
            if self.cancelled.is_set():
                return False
            if not changed:
                raise TimeoutError("Filesystem change deadline expired")
            self._subscribe()
            return True

    def cancel(self) -> None:
        """Release a blocked owner without waiting for a scheduling tick."""
        with self._cancel_lock:
            if self.cancelled.is_set():
                return
            self.cancelled.set()
            if os.name == "nt":
                if self._stop_handle:
                    self._native.SetEvent(self._stop_handle)
            elif self._write >= 0:
                try:
                    os.write(self._write, b"x")
                except BlockingIOError:
                    pass

    def close(self) -> None:
        self.cancel()
        if not self._wait_lock.acquire(timeout=5):
            raise TimeoutError("Filesystem notification waiter did not stop")
        try:
            if self._closed:
                return
            self._closed = True
            self._release_subscriptions()
            if self._stop_handle:
                self._native.CloseHandle(self._stop_handle)
                self._stop_handle = 0
            for descriptor in (self._read, self._write):
                if descriptor >= 0:
                    os.close(descriptor)
            self._read = self._write = -1
        finally:
            self._wait_lock.release()

    def __exit__(self, *_: object) -> None:
        self.close()


def wait_for_path_state[T](paths: Sequence[Path], condition: Callable[[], T], timeout: float = 10) -> T:
    """Subscribe before checking; only the caller's actual state predicate can succeed."""
    deadline = monotonic() + timeout
    with PathChanges(paths) as changes:
        while not (result := condition()):
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError("Filesystem predicate deadline expired")
            changes.wait(remaining)
        return result


class PathObserver:
    """Deliver native change events on one owned worker, retaining callback failures."""

    def __init__(self, paths: Sequence[Path], changed: Callable[[], None]) -> None:
        self.changes = PathChanges(paths)
        self.changed = changed
        self.failure: Exception | None = None
        self.thread = Thread(target=self._run, name="filesystem-changes", daemon=True)
        try:
            self.thread.start()
        except BaseException:
            self.changes.close()
            raise

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _run(self) -> None:
        try:
            while self.changes.wait():
                self.changed()
        except Exception as error:  # pylint: disable=broad-exception-caught
            self.failure = error

    def close(self) -> None:
        self.changes.cancel()
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            raise TimeoutError("Filesystem observer did not stop")
        self.changes.close()
        if self.failure is not None:
            raise RuntimeError(f"Filesystem observer failed: {self.failure}") from self.failure


class ProcessActivity(AbstractContextManager["ProcessActivity"]):
    """Wake on actual output changes or child exit, and reap the owned child on failure."""

    def __init__(self, process: subprocess.Popen[bytes], paths: Sequence[Path], cancellation: ChangeEvent | None = None) -> None:
        self.process = process
        self.changes = PathChanges(paths)
        self.cancellation = cancellation
        if cancellation is not None:
            changes = ref(self.changes)

            def cancelled(_future: object) -> None:
                if current := changes():
                    current.cancel()

            cancellation.changed.ready.add_done_callback(cancelled)
            if cancellation.is_set():
                self.changes.cancel()
        self.failure: Exception | None = None
        self.thread = Thread(target=self._exited, name="process-exit", daemon=True)
        try:
            self.thread.start()
        except BaseException:
            self.changes.close()
            raise

    def _exited(self) -> None:
        try:
            self.process.wait()
        except Exception as error:  # pylint: disable=broad-exception-caught
            self.failure = error
        finally:
            self.changes.cancel()

    def __enter__(self) -> Self:
        return self

    def wait(self, timeout: float) -> bool:
        if self.cancellation is not None and self.cancellation.is_set():
            raise CancelledError("Importer cancelled")
        changed = self.changes.wait(timeout)
        if self.cancellation is not None and self.cancellation.is_set():
            raise CancelledError("Importer cancelled")
        if self.failure is not None:
            raise RuntimeError("Child exit observation failed") from self.failure
        return changed

    def __exit__(self, *_: object) -> None:
        try:
            if self.process.poll() is None:
                self.process.kill()
            self.process.wait(timeout=5)
        finally:
            self.changes.cancel()
            self.thread.join(timeout=5)
            if self.thread.is_alive():
                raise TimeoutError("Child exit observer did not stop")
            self.changes.close()


def process_alive(pid: int) -> bool:
    """Read actual process liveness, without sending a destructive Windows signal."""
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    native = ctypes.WinDLL("kernel32", use_last_error=True)
    native.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    native.OpenProcess.restype = ctypes.c_void_p
    native.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    native.WaitForSingleObject.restype = ctypes.c_uint32
    native.CloseHandle.argtypes = [ctypes.c_void_p]
    native.CloseHandle.restype = ctypes.c_int
    handle = native.OpenProcess(0x100000, False, pid)
    if not handle:
        error = ctypes.get_last_error()
        if error in {5, 87}:
            return error == 5
        raise ctypes.WinError(error)
    try:
        state = native.WaitForSingleObject(handle, 0)
        if state not in {0, 258}:
            raise import_module("ctypes").WinError(import_module("ctypes").get_last_error())
        return state == 258
    finally:
        native.CloseHandle(handle)


def wait_process_exit(pid: int, timeout: float = 5) -> None:
    """Wait on the kernel's process-exit event, including children owned elsewhere."""
    if not process_alive(pid):
        return
    if os.name == "nt":
        native = ctypes.WinDLL("kernel32", use_last_error=True)
        native.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        native.OpenProcess.restype = ctypes.c_void_p
        native.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        native.WaitForSingleObject.restype = ctypes.c_uint32
        native.CloseHandle.argtypes = [ctypes.c_void_p]
        native.CloseHandle.restype = ctypes.c_int
        handle = native.OpenProcess(0x100000, False, pid)
        if not handle:
            if ctypes.get_last_error() == 87:
                return
            raise import_module("ctypes").WinError(import_module("ctypes").get_last_error())
        try:
            if native.WaitForSingleObject(handle, math.ceil(timeout * 1000)) != 0:
                raise TimeoutError(f"Process {pid} did not exit")
        finally:
            native.CloseHandle(handle)
    elif sys.platform.startswith("linux"):
        try:
            descriptor = getattr(os, "pidfd_open")(pid)
        except ProcessLookupError:
            return
        try:
            if not _readable((descriptor,), timeout):
                raise TimeoutError(f"Process {pid} did not exit")
        finally:
            os.close(descriptor)
    else:
        api = import_module("select")
        queue = api.kqueue()
        try:
            try:
                queue.control([api.kevent(pid, filter=api.KQ_FILTER_PROC, flags=api.KQ_EV_ADD,
                                           fflags=api.KQ_NOTE_EXIT)], 0)
            except ProcessLookupError:
                return
            if not queue.control(None, 1, timeout):
                raise TimeoutError(f"Process {pid} did not exit")
        finally:
            queue.close()
