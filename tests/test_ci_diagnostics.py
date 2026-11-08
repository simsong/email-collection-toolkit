# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Verify that CI diagnostics measure actual threaded storage work and failures.
# Use disposable files, SQLite transactions and fsync rather than fake timings.
# Read the emitted per-thread JSON evidence and check profiler cleanup afterward.
# A separate real pytest process exercises plugin selection and failure handling.
# These checks validate diagnostic collection, not Windows ingest performance.
# Windows additionally checks real process I/O accounting through the native API.
"""VALIDATION: slow CI evidence must cover workers and preserve test outcomes."""
from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from threading import Event, Thread
import threading

import pytest

from scripts.ci_diagnostics import DiagnosticReport, ProfileSession, phase
from mailarchiver.storage_sync import replace_file


def test_threaded_storage_profile_and_stack_evidence(tmp_path: Path) -> None:
    """VALIDATION: capture a real worker through an explicit sampling request, independent of storage duration."""
    session = ProfileSession(tmp_path, "synthetic-storage")
    payload = tmp_path / "payload"
    stored, release = Event(), Event()

    def durable_write() -> None:
        with payload.open("wb") as stream:
            stream.write(b"synthetic data" * 4096)
            stream.flush()
            os.fsync(stream.fileno())
        with sqlite3.connect(tmp_path / "storage.sqlite3") as database:
            database.execute("CREATE TABLE measurements(value INTEGER)")
            database.execute("INSERT INTO measurements VALUES (1)")
            database.commit()
        stored.set()
        assert release.wait(10), "stack evidence collection did not release the worker"

    with session.recording(), phase("durable-storage"):
        worker = Thread(target=durable_write, name="storage-worker")
        worker.start()
        try:
            assert stored.wait(5), "storage worker did not finish its writes"
            sample = session.wait_for_stack_sample(timeout=5)
            assert sample.finished_seconds >= sample.started_seconds and sample.threads >= 2
        finally:
            release.set()
            worker.join(timeout=5)
        assert not worker.is_alive()
    report = DiagnosticReport.model_validate_json((session.directory / "report.json").read_text())
    assert report.state == "passed" and report.current_phase is None
    assert report.stack_sampler_stopped
    assert report.total.wall_seconds > 0 and report.total.cpu_seconds >= 0
    assert report.phases[0].name == "durable-storage" and report.phases[0].wall_seconds > 0
    assert report.stack_samples and report.stack_sampler_error is None
    thread, = [entry for entry in report.threads if entry.name == "storage-worker"]
    assert any(entry.operation == "os.fsync" and entry.calls == 1 for entry in thread.operations)
    assert any(entry.operation == "sqlite.commit" for entry in thread.operations)
    assert any(entry.caller.endswith(".durable_write") for entry in thread.operations)
    assert "durable_write" in (session.directory / "threads.txt").read_text()
    assert payload.read_bytes() == b"synthetic data" * 4096
    assert sys.getprofile() is None and threading.getprofile() is None
    if os.name == "nt":
        assert report.total.io is not None
        assert report.total.io.write_operations > 0 and report.total.io.write_bytes >= payload.stat().st_size
    else:
        assert report.total.io is None


@pytest.mark.parametrize("fail,native_timeout", [(False, False), (True, False), (False, True)])
def test_real_pytest_plugin_preserves_outcomes(tmp_path: Path, fail: bool, native_timeout: bool) -> None:
    fixture = tmp_path / "test_profile_fixture.py"
    fixture.write_text(
        "# This generated test exercises the real opt-in diagnostic plugin.\n"
        "# Its synthetic file belongs exclusively to this temporary directory.\n"
        "# A named phase must appear in the emitted evidence.\n"
        "# The parameterized deliberate failure must remain a pytest failure.\n"
        "# No real archive, mailbox or machine settings are involved.\n"
        "from scripts.ci_diagnostics import phase\n"
        "def test_synthetic(tmp_path):\n"
        "    with phase('fixture-write'):\n"
        "        (tmp_path / 'synthetic').write_bytes(b'fixture')\n"
        f"        assert not {fail!r}\n", encoding="utf-8")
    directory = tmp_path / "evidence"
    options = ["-o", "faulthandler_timeout=30"] if native_timeout else []
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "scripts.ci_diagnostics",
        f"--ci-diagnostics-dir={directory}", "--ci-diagnostics-test=test_synthetic", *options, str(fixture)],
        capture_output=True, text=True, check=False, timeout=30)
    assert result.returncode == (1 if fail or native_timeout else 0), result.stdout + result.stderr
    if native_timeout:
        assert "cannot run with native timed traceback dumps" in result.stdout + result.stderr
        assert not list(directory.glob("*/report.json"))
        return
    report_path, = directory.glob("*/report.json")
    report = DiagnosticReport.model_validate_json(report_path.read_text())
    assert report.state == ("failed" if fail else "passed")
    assert report.phases[0].name == "fixture-write"
    assert (report_path.parent / "calls.txt").stat().st_size > 0


def test_existing_profiler_is_not_replaced(tmp_path: Path) -> None:
    def existing(_frame: object, _event: str, _arg: object) -> None:
        pass
    sys.setprofile(existing)
    try:
        with pytest.raises(RuntimeError, match="existing profiler"), ProfileSession(tmp_path, "conflict").recording():
            pytest.fail("A nested profiler must not start")
        assert sys.getprofile() is existing
    finally:
        sys.setprofile(None)


@pytest.mark.parametrize("primary_failure", [False, True])
def test_sampler_failure_is_reported_without_hiding_a_test_failure(tmp_path: Path, primary_failure: bool) -> None:
    """VALIDATION: real output obstruction fails sampling and cannot replace an existing assertion failure."""
    session = ProfileSession(tmp_path, "blocked-stack-output")
    expected = AssertionError if primary_failure else RuntimeError
    message = "original test failure" if primary_failure else "CI stack sampler failed"
    with pytest.raises(expected, match=message), session.recording():
        (session.directory / "threads.txt").unlink()
        (session.directory / "threads.txt").mkdir()
        if primary_failure:
            try:
                session.wait_for_stack_sample(timeout=5)
            except RuntimeError:
                raise AssertionError("original test failure") from None
            pytest.fail("the obstructed output must fail sampling")
        session.wait_for_stack_sample(timeout=5)
    report = DiagnosticReport.model_validate_json((session.directory / "report.json").read_text())
    assert report.state == "failed" and report.stack_sampler_stopped
    assert report.stack_sampler_error is not None


def test_stack_sample_timeout_is_a_failure(tmp_path: Path) -> None:
    """VALIDATION: waiting for evidence cannot succeed on elapsed time alone."""
    session = ProfileSession(tmp_path, "sample-deadline")
    with session.recording(), pytest.raises(TimeoutError, match="did not publish"):
        session.wait_for_stack_sample(timeout=0)


def test_late_worker_preserves_final_failure(tmp_path: Path) -> None:
    entered, release = Event(), Event()
    session = ProfileSession(tmp_path, "surviving-worker")

    def unfinished() -> None:
        with phase("unfinished"):
            entered.set()
            assert release.wait(5)

    worker = Thread(target=unfinished)
    try:
        with pytest.raises(AssertionError, match="deliberate"), session.recording():
            worker.start()
            assert entered.wait(5)
            raise AssertionError("deliberate failure while worker lives")
        final = (session.directory / "report.json").read_bytes()
        assert DiagnosticReport.model_validate_json(final).state == "failed"
    finally:
        release.set()
        if worker.ident is not None:
            worker.join(timeout=5)
    assert not worker.is_alive()
    assert (session.directory / "report.json").read_bytes() == final


def test_python_operations_keep_distinct_callers(tmp_path: Path) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    first.write_bytes(b"first")
    second.write_bytes(b"second")

    def first_publication() -> None:
        replace_file(first, tmp_path / "published-first")

    def second_publication() -> None:
        replace_file(second, tmp_path / "published-second")

    session = ProfileSession(tmp_path, "callers[not-recorded]")
    with session.recording():
        first_publication()
        second_publication()
    report = DiagnosticReport.model_validate_json((session.directory / "report.json").read_text())
    assert report.test == "callers"
    callers = {entry.caller for thread in report.threads for entry in thread.operations
               if entry.operation == "mailarchiver.storage_sync.replace_file"}
    assert callers == {f"{__name__}.first_publication", f"{__name__}.second_publication"}


def test_independently_profiled_worker_keeps_its_hook(tmp_path: Path) -> None:
    ready, probe, checked, stop = Event(), Event(), Event(), Event()
    observed: list[bool] = []

    def existing(_frame: object, _event: str, _arg: object) -> None:
        pass

    def owned_worker() -> None:
        sys.setprofile(existing)
        try:
            ready.set()
            while not stop.is_set():
                assert probe.wait(5), "profiler ownership probe was not released"
                if stop.is_set():
                    break
                probe.clear()
                observed.append(sys.getprofile() is existing)
                checked.set()
        finally:
            sys.setprofile(None)

    worker = Thread(target=owned_worker)
    worker.start()
    try:
        assert ready.wait(5)
        with ProfileSession(tmp_path, "external-owner").recording():
            probe.set()
            assert checked.wait(5)
            checked.clear()
        probe.set()
        assert checked.wait(5)
        assert observed == [True, True]
    finally:
        stop.set()
        probe.set()
        worker.join(timeout=5)
    assert not worker.is_alive()


def test_existing_native_watchdog_keeps_running(tmp_path: Path) -> None:
    code = '''# This disposable child checks ownership of an existing native watchdog.
# Its timer is scheduled to fire after the short diagnostic session ends.
# Repeated output proves collector setup and teardown leave it registered.
# The parent enforces a deadline because native traceback code can crash or hang.
# Only synthetic paths and frame locations are written; no real mail is used.
import faulthandler
import os
from pathlib import Path
import sys
from threading import Event, Thread
from scripts.ci_diagnostics import ProfileSession
root = Path(sys.argv[1])
read_fd, write_fd = os.pipe()
observed = Event()
closed = Event()
lines = []
def receive():
    with os.fdopen(read_fd, 'rb') as reader:
        count = 0
        for line in reader:
            lines.append(line)
            if line.startswith(b'Timeout '):
                assert closed.is_set(), 'watchdog fired during the diagnostic session'
                count += 1
                if count == 2:
                    observed.set()
receiver = Thread(target=receive, daemon=True)
receiver.start()
with os.fdopen(write_fd, 'wb', buffering=0) as output:
    faulthandler.dump_traceback_later(3, repeat=True, file=output)
    try:
        with ProfileSession(root, 'native-watchdog').recording():
            pass
        closed.set()
        assert observed.wait(9), 'existing watchdog was lost during collector teardown'
    finally:
        faulthandler.cancel_dump_traceback_later()
receiver.join(timeout=2)
assert not receiver.is_alive()
(root / 'external-watchdog.txt').write_bytes(b''.join(lines))
'''
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path)],
                            capture_output=True, text=True, check=False, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("output", ["report", "calls"])
@pytest.mark.parametrize("primary_failure", [False, True])
def test_diagnostic_publication_cannot_mask_the_source_failure(
    tmp_path: Path, output: str, primary_failure: bool,
) -> None:
    """VALIDATION: real obstructed report/summary paths fail without replacing a source assertion."""
    session = ProfileSession(tmp_path, "obstructed-report")
    expected = AssertionError if primary_failure else OSError
    with pytest.raises(expected, match="original source failure" if primary_failure else None), session.recording(), phase("publication"):
        target = session.directory / ("calls.txt" if output == "calls" else ".report.json.tmp")
        with session.report_lock:
            target.mkdir()
        if primary_failure:
            raise AssertionError("original source failure")
    assert sys.getprofile() is None and threading.getprofile() is None
