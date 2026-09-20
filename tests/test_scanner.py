# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""Requirements: direct library scans, typed failures, bounded workers, unchanged input."""
from __future__ import annotations

import base64
import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from mailarchiver.clamav_update import EICAR
from mailarchiver.processing.contracts import ScanFailure
from mailarchiver.scanner import ClamScanner, ClamScannerStartupError, scan_message


@pytest.fixture
def scanner():
    with ClamScanner() as instance:
        yield instance


def test_real_library_clean_mime_eicar_and_concurrent_verdicts(scanner: ClamScanner, tmp_path: Path) -> None:
    clean = tmp_path / "clean.eml"
    clean.write_bytes(b"From: sender@example.test\nSubject: clean\n\nordinary text\n")
    infected = tmp_path / "attachment.eml"
    infected.write_bytes(b"From: sender@example.test\nMIME-Version: 1.0\nContent-Type: application/octet-stream\n"
                         b"Content-Transfer-Encoding: base64\n\n" + base64.encodebytes(EICAR))
    original = tuple(hashlib.sha256(path.read_bytes()).hexdigest() for path in (clean, infected))
    paths = [clean, infected] * 3
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda path: scan_message(scanner.session, path, 30), paths))
    assert [result.status for result in results] == ["clean", "infected"] * 3
    assert all(result.engine_version and "daily:" in (result.signature_version or "") for result in results)
    assert tuple(hashlib.sha256(path.read_bytes()).hexdigest() for path in (clean, infected)) == original
    assert scanner.scan(tmp_path / "absent.eml").status == "scanner-error"


@pytest.mark.parametrize("invalid_library", [False, True])
def test_library_startup_failure_has_diagnostics_and_no_worker(tmp_path: Path, invalid_library: bool) -> None:
    library = tmp_path / "libclamav"
    if invalid_library:
        library.write_bytes(b"invalid native library")
    instance = ClamScanner(library=library)
    with pytest.raises(ClamScannerStartupError, match="libclamav"):
        instance.__enter__()
    assert instance.process is None
    assert instance.connection is None


def test_startup_deadline_reaps_worker() -> None:
    instance = ClamScanner(startup_timeout_seconds=1e-9)
    with pytest.raises(ClamScannerStartupError, match="startup timed out"):
        instance.__enter__()
    assert instance.process is None


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


def test_direct_scans_need_no_database(scanner: ClamScanner, tmp_path: Path) -> None:
    """The native scanner returns results without a database or caller-supplied hashes."""
    clean = tmp_path / "clean"
    infected = tmp_path / "eicar"
    clean.write_bytes(b"ordinary test bytes")
    infected.write_bytes(EICAR)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(scanner.scan, [clean, infected]))
    assert {result.status for result in results} == {"clean", "infected"}


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
