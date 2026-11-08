# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Supervise disposable CLI fixtures with bounded, portable output capture.
# Reader threads consume pipes on Windows as well as POSIX without select().
# Queues expose readiness while retained chunks preserve complete diagnostics.
# The context owns process termination and joins readers on every exit path.
# Interruptible Python children map Windows console Break to KeyboardInterrupt.
# All commands and source mail used by these tests are purpose-made fixtures.
"""Real subprocess capture and interruption for preservation regressions."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from queue import Queue
import os
import signal
import subprocess
import sys
from threading import Thread
from concurrent.futures import Future
from mailarchiver.file_events import wait_process_exit
from typing import BinaryIO, Self

from pydantic import BaseModel

INTERRUPT_SETUP = "import signal\nif hasattr(signal, 'SIGBREAK'): signal.signal(signal.SIGBREAK, signal.default_int_handler)\n"


def open_fifo_writer(path: Path, timeout: float = 10) -> int:
    """A blocking FIFO open acknowledges its reader; expiry unblocks and joins the opener."""
    result: Future[int] = Future()

    def open_writer() -> None:
        try:
            result.set_result(os.open(path, os.O_WRONLY))
        except BaseException as error:
            result.set_exception(error)

    worker = Thread(target=open_writer, daemon=True)
    worker.start()
    try:
        descriptor = result.result(timeout=timeout)
    except TimeoutError:
        release = os.open(path, os.O_RDWR | os.O_NONBLOCK)
        try:
            os.close(result.result(timeout=5))
        finally:
            os.close(release)
        raise
    finally:
        worker.join(timeout=5)
    if worker.is_alive():
        os.close(descriptor)
        raise TimeoutError("FIFO opener did not finish")
    return descriptor


def assert_process_exited(pid: int, timeout: float = 5) -> None:
    """Observe real process termination, including a reparented POSIX zombie."""
    wait_process_exit(pid, timeout)


def python_module(module: str, arguments: Sequence[str]) -> list[str]:
    """Run the real module with a graceful Windows console-interrupt handler."""
    code = INTERRUPT_SETUP + "import runpy,sys\nmodule=sys.argv.pop(1)\nsys.argv[0]=module\nrunpy.run_module(module,run_name='__main__',alter_sys=True)\n"
    return [sys.executable, "-c", code, module, *arguments]


class CommandResult(BaseModel):
    returncode: int
    stdout: bytes
    stderr: bytes


class CapturedCommand:
    """Own actual pipes/processes; no background fixture reader may wait indefinitely."""

    def __init__(self, arguments: Sequence[str], *, cwd: Path | None = None,
                 env: Mapping[str, str] | None = None) -> None:
        self.process = subprocess.Popen(arguments, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
        assert self.process.stdout is not None and self.process.stderr is not None
        self.stdout: Queue[bytes] = Queue()
        self.stderr: Queue[bytes] = Queue()
        self._stdout: list[bytes] = []
        self._stderr: list[bytes] = []
        self._readers = [Thread(target=self._read, args=(stream, queue, chunks), daemon=True)
            for stream, queue, chunks in ((self.process.stdout, self.stdout, self._stdout),
                                          (self.process.stderr, self.stderr, self._stderr))]
        for reader in self._readers:
            reader.start()

    @staticmethod
    def _read(stream: BinaryIO, queue: Queue[bytes], chunks: list[bytes]) -> None:
        try:
            while chunk := stream.readline(65536):
                chunks.append(chunk)
                queue.put(chunk)
        finally:
            stream.close()

    def __enter__(self) -> Self:
        return self

    def interrupt(self) -> None:
        self.process.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)

    def finish(self, timeout: float) -> CommandResult:
        code = self.process.wait(timeout=timeout)
        for reader in self._readers:
            reader.join(timeout=3)
            if reader.is_alive():
                raise TimeoutError("fixture output reader did not finish after process exit")
        return CommandResult(returncode=code, stdout=b"".join(self._stdout), stderr=b"".join(self._stderr))

    def __exit__(self, *_: object) -> None:
        if self.process.poll() is None:
            self.process.kill()
        self.finish(timeout=5)
