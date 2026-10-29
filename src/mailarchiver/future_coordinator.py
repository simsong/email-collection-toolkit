# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Coordinate worker-produced operations on the thread that owns durable state.
# Source iterators stay on their pool threads, preserving thread-bound handles.
# Workers submit typed requests and wait on a future for the owner's result.
# A notification future wakes the pool driver without polling publication locks.
# The owner consumes requests under their captured worker progress context.
# Aborting releases every waiting caller before worker-pool shutdown can wait.
"""Owner-thread completion of tracked worker requests."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Generator, Iterable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from contextvars import Context, ContextVar, copy_context
from threading import BoundedSemaphore, Lock, get_ident
from typing import Self, cast

from pydantic import BaseModel, ConfigDict

WORKER_NUMBER: ContextVar[int | None] = ContextVar("mailfile_worker_number", default=None)


class BoundedWorkers:
    """Stream ordered results with one shared bound across all source generators."""

    def __init__(self, workers: int) -> None:
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="mailfile")
        self.capacity = BoundedSemaphore(workers)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        self.pool.shutdown(wait=True, cancel_futures=True)

    def map[T, R](self, process: Callable[[T], R], items: Iterable[T], *,
                  before_read: Callable[[], bool] | None = None) -> Generator[R, None, None]:
        iterator = iter(items)
        pending: deque[Future[R]] = deque()
        exhausted = False
        discovery_error: BaseException | None = None
        try:
            while pending or not exhausted:
                while not exhausted and self.capacity.acquire(blocking=not pending):
                    try:
                        if before_read is not None and not before_read():
                            self.capacity.release()
                            exhausted = True
                            break
                        item = next(iterator)
                    except StopIteration:
                        self.capacity.release()
                        exhausted = True
                        break
                    except Exception as error:
                        self.capacity.release()
                        discovery_error = error
                        exhausted = True
                        break
                    except BaseException:
                        self.capacity.release()
                        raise
                    try:
                        pending.append(self.pool.submit(process, item))
                    except BaseException:
                        self.capacity.release()
                        raise
                if pending:
                    future = pending.popleft()
                    try:
                        yield future.result()
                    finally:
                        self.capacity.release()
            if discovery_error is not None:
                raise discovery_error
        finally:
            for future in pending:
                future.cancel()
                future.add_done_callback(lambda _future: self.capacity.release())
            # Keep the source future alive while outstanding workers may still
            # require its owner's publication driver during cooperative stop.
            wait(pending)


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
