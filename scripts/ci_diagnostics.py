# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Profile selected synthetic ingest tests without changing archive operations.
# Pytest enables this plugin explicitly; ordinary tests keep profiling disabled.
# Python profiling hooks time selected storage/SQLite/wait operations per thread.
# Named phases record elapsed/CPU time and Windows process I/O accounting.
# An owned sampler thread retains all-thread stacks and live progress snapshots.
# JSON and readable call summaries are CI artifacts, never archive content.
# Profiles contain call locations and counts, not message bodies or local values.
"""Opt-in Windows CI ingest diagnostics, also runnable against local fixtures."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import ctypes
import hashlib
import io
import os
from pathlib import Path
import platform
import sqlite3
import sys
import threading
import time
from time import perf_counter, process_time
from types import FrameType
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field
import pytest

PROFILE_TESTS = ("test_gui_stop_checkpoints_partial_import_and_reimport_has_no_duplicates",
                 "test_ost_cli_directory_ingest_and_search")
DIRECTORY_OPTION = "--ci-diagnostics-dir"
TEST_OPTION = "--ci-diagnostics-test"
PYTHON_NAME = "__name__"
LOCK_TYPES = (type(threading.Lock()), type(threading.RLock()))


class ProcessIo(BaseModel):
    read_operations: int
    write_operations: int
    other_operations: int
    read_bytes: int
    write_bytes: int
    other_bytes: int

    def since(self, prior: ProcessIo) -> ProcessIo:
        return ProcessIo(read_operations=self.read_operations - prior.read_operations,
            write_operations=self.write_operations - prior.write_operations,
            other_operations=self.other_operations - prior.other_operations,
            read_bytes=self.read_bytes - prior.read_bytes, write_bytes=self.write_bytes - prior.write_bytes,
            other_bytes=self.other_bytes - prior.other_bytes)


class _IoCounters(ctypes.Structure):
    ReadOperationCount: int
    WriteOperationCount: int
    OtherOperationCount: int
    ReadTransferCount: int
    WriteTransferCount: int
    OtherTransferCount: int
    _fields_ = [(name, ctypes.c_ulonglong) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


def process_io() -> ProcessIo | None:
    """Windows counters cover this process's file/device/network I/O, not physical disk latency."""
    if os.name != "nt":
        return None
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.GetProcessIoCounters.argtypes = [ctypes.c_void_p, ctypes.POINTER(_IoCounters)]
    kernel.GetProcessIoCounters.restype = ctypes.c_int
    counters = _IoCounters()
    if not kernel.GetProcessIoCounters(kernel.GetCurrentProcess(), ctypes.byref(counters)):
        raise ctypes.WinError(ctypes.get_last_error())
    return ProcessIo(read_operations=counters.ReadOperationCount, write_operations=counters.WriteOperationCount,
        other_operations=counters.OtherOperationCount, read_bytes=counters.ReadTransferCount,
        write_bytes=counters.WriteTransferCount, other_bytes=counters.OtherTransferCount)


class Measurement(BaseModel):
    name: str
    wall_seconds: float
    cpu_seconds: float
    io: ProcessIo | None


class OperationTiming(BaseModel):
    operation: str
    caller: str
    calls: int = 0
    exceptions: int = 0
    total_seconds: float = 0
    max_seconds: float = 0


class ThreadTimings(BaseModel):
    name: str
    operations: list[OperationTiming] = []


class _ThreadState(threading.local):
    def __init__(self) -> None:
        self.stack: list[tuple[int, int, str, str, float]] = []
        self.timings: ThreadTimings | None = None


class StackSample(BaseModel):
    """Completed stack-file publication, ordered independently of wall-clock time."""

    number: int
    started_seconds: float
    finished_seconds: float
    threads: int


class DiagnosticReport(BaseModel):
    test: str
    process_id: int
    python: str
    platform: str
    cpus: int | None
    state: Literal["running", "passed", "failed"]
    current_phase: str | None
    total: Measurement
    phases: list[Measurement]
    threads: list[ThreadTimings]
    stack_sampler_stopped: bool
    stack_samples: list[StackSample] = Field(default_factory=list)
    stack_sampler_error: str | None = None
    limitation: str = ("Profiles include overlapping/nested wall time; do not sum cumulative entries. "
                      "CPU/I/O counters exclude child processes; profiling adds overhead. "
                      "Only the caller and subsequently started threading workers are timed. "
                      "Stack sampling requires Python thread scheduling; native watchdogs are untouched. "
                      "I/O counts do not identify disk latency or antivirus causation.")


_active: list[ProfileSession] = []


class ProfileSession:
    """Own and restore diagnostic hooks for one explicitly selected test call."""

    def __init__(self, directory: Path, nodeid: str) -> None:
        name = hashlib.sha256(nodeid.encode()).hexdigest()[:12]
        self.directory = directory / f"{os.getpid()}-{name}-{uuid4().hex[:8]}"
        self.nodeid = nodeid
        self.stop = threading.Event()
        self.sampler: threading.Thread | None = None
        self.threads: list[ThreadTimings] = []
        self.local = _ThreadState()
        self.lock = threading.RLock()
        self.report_lock = threading.Lock()
        self.sample_changed = threading.Condition(self.lock)
        self.samples_started = 0
        self.samples_requested = 0
        self.stack_samples: list[StackSample] = []
        self.stack_sampler_error: str | None = None
        self.closed = False
        self.phases: list[Measurement] = []
        self.current_phase: str | None = None
        self.started = perf_counter()
        self.cpu_started = process_time()
        self.io_started = process_io()

    @staticmethod
    def _operation(frame: FrameType, event: str, argument: object) -> str | None:
        module = frame.f_globals.get(PYTHON_NAME, "")
        if module == __name__:
            return None
        if event in {"call", "return"}:
            if module == "mailarchiver.storage_sync" or (
                    module == "mailarchiver.processing.store" and frame.f_code.co_name in {"connect", "snapshot"}) or (
                    module in {"threading", "subprocess"} and frame.f_code.co_name in {"wait", "join", "communicate"}):
                return f"{module}.{frame.f_code.co_name}"
        elif event in {"c_call", "c_return", "c_exception"}:
            if argument is os.fsync:
                return "os.fsync"
            if argument is time.sleep:
                return "time.sleep"
            owner = getattr(argument, "__self__", None)
            if isinstance(owner, (sqlite3.Connection, sqlite3.Cursor)):
                return f"sqlite.{getattr(argument, PYTHON_NAME, 'call')}"
            if isinstance(owner, io.IOBase):
                return f"file.{getattr(argument, PYTHON_NAME, 'call')}"
            if isinstance(owner, LOCK_TYPES):
                return f"lock.{getattr(argument, PYTHON_NAME, 'call')}"
        return None

    def _profile(self, frame: FrameType, event: str, argument: object) -> None:
        if self.closed:
            sys.setprofile(None)
            return
        event_at = perf_counter()
        operation = self._operation(frame, event, argument)
        if operation is None:
            return
        with self.lock:
            if not self.closed:
                self._record_operation(frame, event, operation, event_at)

    def _record_operation(self, frame: FrameType, event: str, operation: str, event_at: float) -> None:
        kind = int(event.startswith("c_"))
        state = self.local
        if event in {"call", "c_call"}:
            caller_frame = frame.f_back if event == "call" else frame
            caller = (f"{caller_frame.f_globals.get(PYTHON_NAME, '')}.{caller_frame.f_code.co_name}"
                      if caller_frame is not None else "<entry>")
            if state.timings is None:
                state.timings = ThreadTimings(name=threading.current_thread().name)
                self.threads.append(state.timings)
            state.stack.append((kind, id(frame), operation, caller, perf_counter()))
        elif state.stack and state.stack[-1][:3] == (kind, id(frame), operation):
            _, _, _, caller, started = state.stack.pop()
            elapsed = event_at - started
            assert state.timings is not None
            timing = next((entry for entry in state.timings.operations
                           if entry.operation == operation and entry.caller == caller), None)
            if timing is None:
                timing = OperationTiming(operation=operation, caller=caller)
                state.timings.operations.append(timing)
            timing.calls += 1
            timing.exceptions += int(event == "c_exception")
            timing.total_seconds += elapsed
            timing.max_seconds = max(timing.max_seconds, elapsed)

    def measurement(self, name: str, wall: float, cpu: float, prior_io: ProcessIo | None) -> Measurement:
        current = process_io()
        return Measurement(name=name, wall_seconds=perf_counter() - wall, cpu_seconds=process_time() - cpu,
                           io=current.since(prior_io) if current is not None and prior_io is not None else None)

    def write_report(self, state: Literal["running", "passed", "failed"]) -> None:
        # Serialize publishers without holding the profiler's data lock during disk I/O.
        if not self.report_lock.acquire(timeout=5):
            raise TimeoutError("CI diagnostic report publication did not finish")
        try:
            with self.lock:
                if self.closed and state == "running":
                    return
                report = DiagnosticReport(test=self.nodeid.split("[", 1)[0], process_id=os.getpid(), python=platform.python_version(),
                    platform=platform.platform(), cpus=os.cpu_count(), state=state, current_phase=self.current_phase,
                    total=self.measurement("test", self.started, self.cpu_started, self.io_started),
                    phases=self.phases, threads=self.threads, stack_samples=self.stack_samples,
                    stack_sampler_error=self.stack_sampler_error,
                    stack_sampler_stopped=self.sampler is None or not self.sampler.is_alive()).model_copy(deep=True)
            temporary = self.directory / ".report.json.tmp"
            temporary.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
            temporary.replace(self.directory / "report.json")
        finally:
            self.report_lock.release()

    def wait_for_stack_sample(self, timeout: float) -> StackSample:
        """Request a subsequent sample and acknowledge its publication; expiry is a failure."""
        with self.sample_changed:
            target = max(self.samples_started, self.samples_requested) + 1
            self.samples_requested = target
            self.sample_changed.notify_all()
            ready = self.sample_changed.wait_for(
                lambda: len(self.stack_samples) >= target or self.stack_sampler_error is not None or self.closed,
                timeout=timeout,
            )
            if not ready:
                raise TimeoutError("CI stack sampler did not publish the requested sample")
            if self.stack_sampler_error is not None:
                raise RuntimeError(f"CI stack sampler failed: {self.stack_sampler_error}")
            if len(self.stack_samples) < target:
                raise RuntimeError("CI diagnostics closed before publishing the requested sample")
            return self.stack_samples[target - 1].model_copy(deep=True)

    def _sample_stacks(self) -> None:
        if sys.getprofile() == self._profile:
            sys.setprofile(None)
        try:
            while True:
                with self.sample_changed:
                    self.sample_changed.wait_for(lambda: self.stop.is_set() or self.samples_requested > self.samples_started)
                    if self.stop.is_set():
                        return
                    self.samples_started += 1
                    number = self.samples_started
                started = perf_counter() - self.started
                # Avoid concurrent native traceback dumping with an external watchdog.
                # Serialize code locations only, never frame locals or source lines.
                frames = sys._current_frames()
                try:
                    with (self.directory / "threads.txt").open("a", encoding="utf-8") as stacks:
                        stacks.write(f"Sample {number} started at {started:.6f}s:\n")
                        for identity, frame in frames.items():
                            stacks.write(f"Thread 0x{identity:x} (most recent call first):\n")
                            current: FrameType | None = frame
                            depth = 0
                            while current is not None and depth < 256:
                                stacks.write(f"  File {current.f_code.co_filename!r}, line {current.f_lineno}, "
                                             f"in {current.f_code.co_name}\n")
                                current = current.f_back
                                depth += 1
                            if current is not None:
                                stacks.write("  ... stack truncated at 256 frames\n")
                    sample = StackSample(number=number, started_seconds=started,
                                         finished_seconds=perf_counter() - self.started, threads=len(frames))
                finally:
                    del frames
                with self.sample_changed:
                    self.stack_samples.append(sample)
                    self.sample_changed.notify_all()
                self.write_report("running")
        except Exception as error:  # pylint: disable=broad-exception-caught
            with self.sample_changed:
                self.stack_sampler_error = f"{type(error).__name__}: {error}"
                self.sample_changed.notify_all()

    @contextmanager
    def recording(self) -> Iterator[None]:
        if (_active or sys.getprofile() is not None or threading.getprofile() is not None
                or sys.monitoring.get_tool(sys.monitoring.PROFILER_ID) is not None):
            raise RuntimeError("CI diagnostics cannot replace an existing profiler")
        self.directory.mkdir(parents=True)
        self.write_report("running")
        state: Literal["passed", "failed"] = "failed"
        (self.directory / "threads.txt").touch()
        _active.append(self)
        try:
            threading.setprofile(self._profile)
            sys.setprofile(self._profile)
            self.sampler = threading.Thread(target=self._sample_stacks, name="ci-stack-sampler", daemon=True)
            self.sampler.start()
            yield
            state = "passed"
        finally:
            primary_error = sys.exc_info()[0] is not None
            with self.sample_changed:
                target = self.samples_requested
                if not self.sample_changed.wait_for(
                    lambda: len(self.stack_samples) >= target or self.stack_sampler_error is not None, timeout=5,
                ):
                    self.stack_sampler_error = "requested stack evidence did not finish before shutdown"
                self.closed = True
                self.sample_changed.notify_all()
            if sys.getprofile() == self._profile:
                sys.setprofile(None)
            if threading.getprofile() == self._profile:
                threading.setprofile(None)
            with self.sample_changed:
                self.stop.set()
                self.sample_changed.notify_all()
            if self.sampler is not None and self.sampler.ident is not None:
                self.sampler.join(timeout=1)
                if self.sampler.is_alive() and self.stack_sampler_error is None:
                    self.stack_sampler_error = "sampler did not stop before its shutdown deadline"
            _active.pop()
            if self.stack_sampler_error is not None:
                state = "failed"
            try:
                lines = ["Completed operation wall time; nested/parallel entries overlap.",
                         "seconds  maximum  calls  exceptions  thread  operation  caller"]
                entries = [(thread.name, entry) for thread in self.threads for entry in thread.operations]
                for name, entry in sorted(entries, key=lambda pair: pair[1].total_seconds, reverse=True):
                    lines.append(f"{entry.total_seconds:.6f} {entry.max_seconds:.6f} {entry.calls} "
                                 f"{entry.exceptions} {name} {entry.operation} {entry.caller}")
                (self.directory / "calls.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
                self.write_report(state)
            except Exception:  # pylint: disable=broad-exception-caught
                try:
                    self.write_report("failed")
                except Exception:  # pylint: disable=broad-exception-caught
                    print("CI diagnostics could not publish the failed report", file=sys.stderr)
                if not primary_error:
                    raise
                print("CI diagnostics could not publish final evidence; retaining the original failure", file=sys.stderr)
            if self.stack_sampler_error is not None and not primary_error:
                raise RuntimeError(f"CI stack sampler failed: {self.stack_sampler_error}")


@contextmanager
def phase(name: str) -> Iterator[None]:
    """Keep ordinary tests unchanged; record explicit boundaries only during diagnostics."""
    session = _active[-1] if _active else None
    if session is None:
        yield
        return
    with session.lock:
        prior = session.current_phase
        if not session.closed:
            session.current_phase = name
    wall, cpu, counters = perf_counter(), process_time(), process_io()
    with session.sample_changed:
        session.samples_requested = max(session.samples_requested, session.samples_started) + 1
        session.sample_changed.notify_all()
    session.write_report("running")
    print(f"CI phase started: {name}", flush=True)
    try:
        yield
    finally:
        primary_error = sys.exc_info()[0] is not None
        measured = session.measurement(name, wall, cpu, counters)
        with session.lock:
            if not session.closed:
                session.phases.append(measured)
                session.current_phase = prior
        try:
            session.write_report("running")
        except Exception:  # pylint: disable=broad-exception-caught
            if not primary_error:
                raise
            print("CI diagnostics could not publish phase evidence; retaining the original failure", file=sys.stderr)
        print(f"CI phase finished: {name}: wall={measured.wall_seconds:.3f}s CPU={measured.cpu_seconds:.3f}s", flush=True)


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("ci-diagnostics")
    group.addoption(DIRECTORY_OPTION, type=Path, help="Write selected synthetic-test diagnostics here")
    group.addoption(TEST_OPTION, action="append", default=[], help="Profile this test function (repeatable)")


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Iterator[None]:
    directory = item.config.getoption(DIRECTORY_OPTION)
    selected = item.config.getoption(TEST_OPTION) or PROFILE_TESTS
    if directory is None or item.name.split("[", 1)[0] not in selected:
        yield
        return
    if item.config.getini("faulthandler_timeout"):
        raise pytest.UsageError("CI profiling cannot run with native timed traceback dumps; "
                                "use the owned Python stack sampler instead")
    with ProfileSession(directory, item.nodeid).recording():
        yield
