# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Coordinate worker-produced operations on the thread that owns durable state.
# Source iterators stay on their pool threads, preserving thread-bound handles.
# Workers submit typed requests and wait on a future for the owner's result.
# A notification future wakes the pool driver without polling publication locks.
# The owner consumes requests under their captured worker progress context.
# Aborting releases every waiting caller before worker-pool shutdown can wait.
"""Owner-thread completion of tracked worker requests."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future
from contextvars import Context, ContextVar, copy_context
from threading import Lock, get_ident
from typing import cast

from pydantic import BaseModel, ConfigDict

WORKER_NUMBER: ContextVar[int | None] = ContextVar("mailfile_worker_number", default=None)


class DriverCall(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    operation: Callable[[], object]
    result: Future[object]
    context: Context


class FutureCoordinator:
    """The owner alone consumes operations; workers receive values or exceptions."""

    def __init__(self) -> None:
        self.owner = get_ident()
        self._lock = Lock()
        self._pending: list[DriverCall] = []
        self._ready: Future[None] = Future()
        self._failure: BaseException | None = None

    @property
    def ready(self) -> Future[None]:
        with self._lock:
            return self._ready

    def call[T](self, operation: Callable[[], T]) -> T:
        if get_ident() == self.owner:
            return operation()
        result: Future[object] = Future()
        with self._lock:
            if self._failure is not None:
                raise RuntimeError("The publication coordinator stopped.") from self._failure
            self._pending.append(DriverCall(operation=operation, result=result, context=copy_context()))
            if not self._ready.done():
                self._ready.set_result(None)
        return cast(T, result.result())

    def drain(self) -> None:
        if get_ident() != self.owner:
            raise RuntimeError("Only the owner may publish coordinator results.")
        with self._lock:
            pending, self._pending = self._pending, []
            self._ready = Future()
        for index, request in enumerate(pending):
            try:
                request.result.set_result(request.context.run(request.operation))
            except BaseException as error:
                request.result.set_exception(error)
                for remaining in pending[index + 1:]:
                    remaining.result.set_exception(error)
                self.abort(error)
                raise

    def abort(self, error: BaseException) -> None:
        with self._lock:
            self._failure = error
            pending, self._pending = self._pending, []
        for request in pending:
            request.result.set_exception(error)
