# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Purpose: Contain the lifetime and output of an explicitly launched external command.

How it works:
Use a private supervisor/process group or Windows job and reap descendants on timeout or owner
death.

Fundamental dependents:

- ``clamav_update``: definition refresh depends on FreshClam cleanup during cancellation or Quit.
"""
from __future__ import annotations

import multiprocessing
import os
import signal
import subprocess
import time
from collections.abc import Callable
from multiprocessing.connection import Connection, wait
from threading import Thread

from pydantic import BaseModel


class CommandResult(BaseModel):
    returncode: int = -1
    stdout: str = ""
    stderr: str = ""
    error: str = ""


def _run(connection: Connection, command: list[str], timeout: float) -> None:
    if os.name == "nt":
        from .windows_job import contain_current_process
        contain_current_process()
    else:
        os.setsid()
    parent = multiprocessing.parent_process()
    assert parent is not None

    def watch_owner() -> None:
        wait([parent.sentinel])
        _terminate_group()

    Thread(target=watch_owner, name="command-owner", daemon=True).start()
    try:
        try:
            completed = subprocess.run(command, capture_output=True, text=True, check=False, timeout=timeout)
            result = CommandResult(returncode=completed.returncode, stdout=completed.stdout, stderr=completed.stderr)
        except (OSError, subprocess.TimeoutExpired) as error:
            result = CommandResult(error=str(error))
        connection.send(result)
    finally:
        connection.close()
        # Also reap any utility descendants after errors or normal completion.
        _terminate_group()


def _terminate_group() -> None:
    if os.name == "nt":
        os._exit(0)  # Closing the supervisor's sole job handle reaps descendants.
    else:
        os.killpg(os.getpid(), signal.SIGKILL)


def run_owned_command(command: list[str], *, timeout: float,
                      status_callback: Callable[[], None] | None = None) -> CommandResult:
    """Run a one-shot POSIX utility, including cleanup if its supervisor fails."""
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    worker = context.Process(target=_run, args=(sender, command, timeout), daemon=True)
    try:
        worker.start()
        sender.close()
        deadline = time.monotonic() + timeout + 5
        while not receiver.poll(min(0.25, max(0, deadline - time.monotonic()))):
            if time.monotonic() >= deadline:
                raise TimeoutError("command supervisor timed out")
            if status_callback is not None:
                status_callback()
        result = receiver.recv()
        if not isinstance(result, CommandResult):
            raise RuntimeError("invalid command supervisor response")
        if result.error:
            raise RuntimeError(result.error)
        return result
    finally:
        sender.close()
        receiver.close()
        if worker.pid is not None:
            worker.join(timeout=1)
            if worker.is_alive():
                try:
                    if os.name == "nt":
                        worker.kill()
                    else:
                        os.killpg(worker.pid, signal.SIGKILL)
                except ProcessLookupError:
                    worker.kill()
                worker.join(timeout=1)
