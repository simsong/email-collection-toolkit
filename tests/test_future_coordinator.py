# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Verify source-worker futures against real thread-bound SQLite connections.
# Pool workers retain their own input connections while the owner publishes.
# Captured context keeps worker progress identity across the future boundary.
# An aborted publication must release both queued and later worker requests.
# These checks exercise the production driver without replacing its services.
# Real ingestion and interruption acceptance complement these focused contracts.
"""Source coordination requirements: ownership, notification and failure release."""

from concurrent.futures import ThreadPoolExecutor
import sqlite3
from threading import Event, get_ident

import pytest

from mailarchiver.__main__ import run_file_workers
from mailarchiver.future_coordinator import FutureCoordinator, WORKER_NUMBER


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
