# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Verify source-worker futures against real thread-bound SQLite connections.
# Pool workers retain their own input connections while the owner publishes.
# Captured context keeps worker progress identity across the future boundary.
# An aborted publication must release both queued and later worker requests.
# These checks exercise the production driver without replacing its services.
# Real ingestion and interruption acceptance complement these focused contracts.
"""Source coordination requirements: ownership, notification and failure release."""

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
import sqlite3
from threading import Barrier, Event, get_ident

import pytest

from mailarchiver.__main__ import run_file_workers
from mailarchiver.future_coordinator import BoundedWorkers, FutureCoordinator, WORKER_NUMBER


def test_source_workers_retain_inputs_and_owner_alone_commits() -> None:
    coordinator = FutureCoordinator()
    owner = get_ident()
    database = sqlite3.connect(":memory:")
    database.execute("CREATE TABLE publications (value INTEGER, lane INTEGER)")

    def process(value: int) -> None:
        with sqlite3.connect(":memory:") as source:
            source.execute("CREATE TABLE input (value INTEGER)")
            source.execute("INSERT INTO input VALUES (?)", (value,))
            for _ in range(2):
                item = source.execute("SELECT value FROM input").fetchone()
                assert item is not None

                def publish() -> int:
                    assert get_ident() == owner
                    lane = WORKER_NUMBER.get()
                    assert lane is not None
                    with database:
                        database.execute("INSERT INTO publications VALUES (?, ?)", (item[0], lane))
                    return item[0]

                assert coordinator.call(publish) == value

    try:
        run_file_workers(range(6), 3, process, Event(), lambda: None, coordinator=coordinator)
        assert database.execute("SELECT value,count(*) FROM publications GROUP BY value ORDER BY value").fetchall() == [
            (value, 2) for value in range(6)]
        assert all(1 <= row[0] <= 3 for row in database.execute("SELECT lane FROM publications"))
    finally:
        database.close()


def test_owner_failure_releases_queued_and_future_requests() -> None:
    coordinator = FutureCoordinator()

    def fail() -> None:
        raise ValueError("publication failed")

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(coordinator.call, fail) for _ in range(3)]
        coordinator.ready.result(timeout=2)
        with pytest.raises(ValueError, match="publication failed"):
            coordinator.drain()
        for future in futures:
            with pytest.raises((ValueError, RuntimeError)):
                future.result(timeout=2)
        with pytest.raises(RuntimeError, match="coordinator stopped"):
            pool.submit(coordinator.call, lambda: 1).result(timeout=2)


def test_one_source_streams_parallel_work_with_order_and_backpressure() -> None:
    """ACQUISITION: one mailbox fills workers while its iterator stays on its owner."""
    owner = get_ident()
    barrier = Barrier(3, timeout=5)
    read: list[int] = []
    threads: set[int] = set()

    def source():
        for value in range(9):
            assert get_ident() == owner
            read.append(value)
            yield value

    def process(value: int) -> int:
        threads.add(get_ident())
        barrier.wait()
        return value

    with BoundedWorkers(3) as workers:
        results = workers.map(process, source())
        assert next(results) == 0
        assert read == [0, 1, 2]
        assert list(results) == list(range(1, 9))
    assert len(threads) == 3 and owner not in threads


def test_source_failure_drains_complete_records_before_reporting_partial_run() -> None:
    """ACQUISITION: a failed producer cannot discard its already yielded complete mail."""
    def source():
        yield from range(5)
        raise ValueError("incomplete source")

    with BoundedWorkers(3) as workers:
        results = workers.map(lambda value: value, source())
        assert [next(results) for _ in range(5)] == list(range(5))
        with pytest.raises(ValueError, match="incomplete source"):
            next(results)


def test_stop_during_source_fetch_prevents_read_ahead_of_next_message() -> None:
    """ACQUISITION: suspension during one fetch cannot start further source pulls."""
    stop = Event()
    fetched: list[int] = []

    def source():
        for value in range(6):
            fetched.append(value)
            stop.set()
            yield value

    with BoundedWorkers(3) as workers:
        assert list(workers.map(lambda value: value, source(), before_read=lambda: not stop.is_set())) == [0]
    assert fetched == [0]


def test_closing_stream_waits_for_worker_publication_before_source_driver_exits() -> None:
    """ACQUISITION: suspend cannot strand scanner workers waiting on the source owner."""
    coordinator = FutureCoordinator()
    started, closing, release = Event(), Event(), Event()

    def process(value: int) -> int:
        if value:
            started.set()
            assert release.wait(5)
            return coordinator.call(lambda: value)
        assert started.wait(5)
        return value

    with BoundedWorkers(2) as workers, ThreadPoolExecutor(max_workers=1) as sources:
        def source() -> None:
            results = workers.map(process, range(2))
            assert next(results) == 0
            closing.set()
            results.close()

        future = sources.submit(source)
        try:
            assert closing.wait(5)
            with pytest.raises(FutureTimeout):
                future.result(timeout=0.05)
        finally:
            release.set()
            coordinator.ready.result(timeout=5)
            coordinator.drain()
        future.result(timeout=5)
