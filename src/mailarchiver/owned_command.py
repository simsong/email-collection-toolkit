# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Run a bounded external command whose lifetime belongs to its caller.
# A spawned supervisor creates a private POSIX process group for the command.
# Its parent-sentinel watcher kills that group if the caller exits abruptly.
# This closes the startup race between launching a child and recording its PID.
# Ordinary completion returns captured output; failures retain diagnostic text.
# Definition updates use this path so forced GUI Quit cannot orphan FreshClam.

from __future__ import annotations

import multiprocessing
import os
import signal
import subprocess
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


def run_owned_command(command: list[str], *, timeout: float) -> CommandResult:
    """Run a one-shot POSIX utility, including cleanup if its supervisor fails."""
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    worker = context.Process(target=_run, args=(sender, command, timeout), daemon=True)
    try:
        worker.start()
        sender.close()
        if not receiver.poll(timeout + 5):
            raise TimeoutError("command supervisor timed out")
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
