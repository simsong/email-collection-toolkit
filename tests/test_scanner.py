# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""Requirements: direct library scans, typed failures, bounded workers, unchanged input."""
from __future__ import annotations

import base64
import hashlib
import os
import signal
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from multiprocessing import get_context
from multiprocessing.synchronize import Event as ProcessEvent
from threading import Event
import tempfile
from pathlib import Path
from tests.process_output import assert_process_exited

import pytest

from mailarchiver.clamav_update import EICAR
from mailarchiver.libclamav import load_library
from mailarchiver.processing.contracts import ScanFailure
from mailarchiver.scanner import ClamScanner, ClamScannerStartupError, scan_message
from tests.test_quit import run_quit_probe


def test_scanner_exits_with_owner_during_blocked_native_scan(tmp_path: Path) -> None:
    """Bounded GUI Quit must not orphan the native helper or its blocked scan threads."""
    if sys.platform == "win32":
        pytest.skip("POSIX FIFO blocking; Windows inherited-owner lifecycle is tested separately")
    try:
        assert run_quit_probe(tmp_path, "scanner") < 2
        pid = int((tmp_path / "scanner-pid").read_text())
        assert_process_exited(pid)
    finally:
        if (tmp_path / "scanner-pid").exists():
            try:
                os.kill(int((tmp_path / "scanner-pid").read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.fixture
def scanner():
    with ClamScanner(workers=2) as instance:
        yield instance


def test_real_library_clean_mime_eicar_and_concurrent_verdicts(scanner: ClamScanner, tmp_path: Path) -> None:
    """SCANNING: direct and registered APIs scan real files concurrently without a database."""
    clean = tmp_path / "clean.eml"
    clean.write_bytes(b"From: sender@example.test\nSubject: clean\n\nordinary text\n")
    infected = tmp_path / "attachment.eml"
    infected.write_bytes(b"From: sender@example.test\nMIME-Version: 1.0\nContent-Type: application/octet-stream\n"
                         b"Content-Transfer-Encoding: base64\n\n" + base64.encodebytes(EICAR))
    plain_eicar = tmp_path / "eicar"
    plain_eicar.write_bytes(EICAR)
    plain_clean = tmp_path / "clean"
    plain_clean.write_bytes(b"ordinary test bytes")
    sources = (clean, infected, plain_clean, plain_eicar)
    original = tuple(hashlib.sha256(path.read_bytes()).hexdigest() for path in sources)
    paths = [clean, infected] * 3
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda path: scan_message(scanner.session, path, 30), paths))
        direct = list(pool.map(scanner.scan, sources))
    assert [result.status for result in results] == ["clean", "infected"] * 3
    assert [result.status for result in direct] == ["clean", "infected", "clean", "infected"]
    assert all(result.engine_version and "daily:" in (result.signature_version or "") for result in results + direct)
    assert tuple(hashlib.sha256(path.read_bytes()).hexdigest() for path in sources) == original
    assert scanner.scan(tmp_path / "absent.eml").status == "scanner-error"


def test_scanner_duplex_backpressure_does_not_block_result_delivery(tmp_path: Path) -> None:
    """SCANNING: saturated request/response pipes still drain tracked scan futures."""
    # An external deadline also bounds a blocked send before the per-scan timer.
    program = """from concurrent.futures import ThreadPoolExecutor
from multiprocessing import get_context
from multiprocessing.synchronize import Event as ProcessEvent
from threading import Event
import tempfile
from threading import Barrier
from mailarchiver.scanner import ClamScanner

if __name__ == "__main__":
    callers = 160
    ready = Barrier(callers)
    sample = b"From: fixture@example.test\\nSubject: pipe pressure\\n\\n" + b"x" * 60000
    with ClamScanner(workers=2) as scanner:
        def scan(_number):
            ready.wait(timeout=15)
            return scanner.scan_sample(sample).status
        with ThreadPoolExecutor(max_workers=callers) as pool:
            results = list(pool.map(scan, range(callers)))
        assert results == ["clean"] * callers, results
"""
    completed = subprocess.run([sys.executable, "-c", program], cwd=Path(__file__).parents[1],
                               capture_output=True, text=True, timeout=40, check=False)
    (tmp_path / "scanner-stress-stderr").write_text(completed.stderr)
    assert completed.returncode == 0, completed.stderr


def test_link_mismatches_are_not_detections_but_attachments_still_are(
    scanner: ClamScanner, tmp_path: Path, capfd: pytest.CaptureFixture[str],
) -> None:
    """SCANNING: disable suspicious-link checks while retaining MIME malware detection."""
    html = (b'<html><body><a href="http://go.amex/fixture">https://cdaas.americanexpress.com</a>'
            b'<a href="http://m.amex/fixture">americanexpress.com</a>'
            b'<a href="http://unrelated.example/fixture">https://www.paypal.com</a></body></html>')
    for infected in (False, True):
        raw = (b'From: sender@example.test\nMIME-Version: 1.0\nContent-Type: multipart/mixed; boundary="fixture"\n\n'
               b'--fixture\nContent-Type: text/html\n\n' + html + b'\n')
        if infected:
            raw += (b'--fixture\nContent-Type: application/octet-stream\nContent-Transfer-Encoding: base64\n\n'
                    + base64.encodebytes(EICAR))
        raw += b'--fixture--\n'
        path = tmp_path / "links.eml"
        path.write_bytes(raw)
        for result in (scanner.scan(path), scanner.scan_sample(raw)):
            assert result.status == ("infected" if infected else "clean")
            if infected:
                assert "eicar" in result.detail.lower()
        assert path.read_bytes() == raw
    assert "Suspicious link found!" not in capfd.readouterr().err


@pytest.mark.parametrize("invalid_library", [False, True])
def test_library_startup_failure_has_diagnostics_and_no_worker(tmp_path: Path, invalid_library: bool) -> None:
    library = tmp_path / "libclamav"
    if invalid_library:
        library.write_bytes(b"invalid native library")
    instance = ClamScanner(library=library)
    with pytest.raises(ClamScannerStartupError, match="libclamav") as failure:
        instance.__enter__()
    assert ("present" if invalid_library else "missing or not a file") in str(failure.value)
    assert instance.process is None
    assert instance.connection is None


def test_native_loader_distinguishes_missing_from_present_but_unloadable(tmp_path: Path) -> None:
    """A failed packaged scanner must not misreport every dlopen error as a missing dylib."""
    library = tmp_path / "libclamav.dylib"
    with pytest.raises(FileNotFoundError, match="missing or not a file"):
        load_library(library)
    library.write_bytes(b"not a dynamic library")
    with pytest.raises(OSError, match=r"present \(21 bytes\) but cannot be loaded") as failure:
        load_library(library)
    assert "libclamav.dylib" in str(failure.value)
    assert failure.value.__cause__ is not None


def test_startup_deadline_reaps_worker() -> None:
    instance = ClamScanner(startup_timeout_seconds=1e-9)
    with pytest.raises(ClamScannerStartupError, match="startup timed out"):
        instance.__enter__()
    assert instance.process is None and instance.connection is None


def test_scan_deadline_reaps_worker_and_removes_plaintext(tmp_path: Path) -> None:
    instance = ClamScanner(scan_timeout_seconds=1e-9, scan_temporary_directory=tmp_path)
    with instance:
        with pytest.raises(ScanFailure, match="scan timed out"):
            instance.infected(b"private fixture bytes")
        assert instance.process is None
    assert list(tmp_path.iterdir()) == []


def test_worker_crash_is_a_typed_failure(scanner: ClamScanner, tmp_path: Path) -> None:
    assert scanner.process is not None
    scanner.process.kill()
    scanner.process.join()
    path = tmp_path / "source.eml"
    path.write_bytes(b"retained source")
    with pytest.raises(ScanFailure):
        scanner.scan(path)
    assert path.read_bytes() == b"retained source"
    with pytest.raises(ScanFailure, match="session is not ready"):
        scan_message(scanner.session, path, 1)


@pytest.mark.parametrize("infected", [False, True])
def test_api_headers_route_without_host_scanner(tmp_path: Path, infected: bool) -> None:
    """Infected API mail carries detection headers; clean mail needs no scan provenance."""
    from mailarchiver.plugin_api import SourceReference
    from mailarchiver.processing.api import ApplicationContext, ArchiveContext, ProcessingObject, RAW_MESSAGE
    from mailarchiver.processing.builtin import ClamAVProcessor
    from mailarchiver.processing.contracts import ProcessingPolicy, SourceMetadata
    from mailarchiver.processing.store import content_reference
    from mailarchiver.scan_evidence import DETECTION_HEADER, ENGINE_HEADER, DEFINITIONS_HEADER
    path = tmp_path / "message.eml"
    headers = f"{DETECTION_HEADER}: Eicar-Test-Signature\r\n{ENGINE_HEADER}: 1.5\r\n{DEFINITIONS_HEADER}: daily:123\r\n" if infected else ""
    raw = headers.encode() + b"From: producer@example.test\r\nDate: Thu, 1 Feb 2024 12:00:00 +0000\r\n\r\nretained"
    path.write_bytes(raw)
    reference = content_reference(path)
    metadata = SourceMetadata(source=SourceReference(plugin_kind="api", source_id="test", native_id="1", display_name="test"),
                              source_file_pk=1, work_id="test", cursor="1", scan_responsibility="producer")
    item = ProcessingObject(archive=ArchiveContext(path=tmp_path), application=ApplicationContext(policy=ProcessingPolicy()),
                            message_id=reference.sha256, message_ref=reference, content_ref=reference,
                            content_type=RAW_MESSAGE, source_metadata=metadata, pipeline="ingest")
    result = ClamAVProcessor().process(item)
    assert result.scan is not None and result.scan.status == ("infected" if infected else "clean")
    if infected:
        assert result.scan.detail == "Eicar-Test-Signature"
        assert result.scan.engine_version == "1.5" and result.scan.signature_version == "daily:123"
        assert result.filing is not None and result.filing.mailbox is not None
        assert result.filing.mailbox.category == "INFECTED" and result.outcome == "abort-message"
    else:
        assert result.scan.engine_version is None and result.scan.signature_version is None
        assert result.filing is None
    assert path.read_bytes() == raw


def _hold_worker_during_teardown(ready: ProcessEvent, terminating: ProcessEvent, release: ProcessEvent) -> None:
    """Keep a real worker alive after acknowledging termination until explicitly released."""
    signal.signal(signal.SIGTERM, lambda *_: terminating.set())
    ready.set()
    assert release.wait(15), "teardown fixture did not release its worker"


@pytest.mark.skipif(os.name == "nt", reason="POSIX acknowledged SIGTERM fixture")
def test_concurrent_scanner_teardowns_retain_runtime_until_the_worker_exits(tmp_path: Path) -> None:
    """SCANNING: an empty detached process slot must not let another closer free live resources."""
    context = get_context("spawn")
    ready, terminating, release = context.Event(), context.Event(), context.Event()
    worker = context.Process(target=_hold_worker_during_teardown, args=(ready, terminating, release))
    scanner = ClamScanner()
    scanner.runtime = tempfile.TemporaryDirectory(dir=tmp_path)
    runtime = Path(scanner.runtime.name)
    scanner.process = worker
    second_started = Event()

    def close_second() -> None:
        second_started.set()
        scanner.__exit__()
        assert not worker.is_alive(), "concurrent cleanup returned while its worker survived"
        assert scanner.runtime is None and not runtime.exists()

    worker.start()
    try:
        assert ready.wait(5), "teardown worker did not start"
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(scanner.__exit__)
            try:
                assert terminating.wait(5), "first closer did not request termination"
                # Observe actual exclusive teardown ownership, not a guessed overlap duration.
                acquired = scanner.close_lock.acquire(blocking=False)
                if acquired:
                    scanner.close_lock.release()
                assert not acquired, "teardown did not retain exclusive ownership while joining"
                assert worker.is_alive() and runtime.is_dir() and scanner.runtime is not None
                second = executor.submit(close_second)
                assert second_started.wait(5), "second closer did not start"
            finally:
                release.set()
            first.result(timeout=10)
            second.result(timeout=10)
        assert scanner.process is None and scanner.runtime is None and not runtime.exists()
    finally:
        release.set()
        if worker.is_alive():
            worker.kill()
        worker.join(timeout=5)
        scanner.__exit__()
        worker.close()
