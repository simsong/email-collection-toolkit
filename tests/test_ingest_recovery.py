# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Exercise recovery from saved antivirus failures without contacting a mail server.
# Typed failed-ingest roots reproduce the durable state left by an interrupted run.
# Real ClamAV scans distinguish clean messages from EICAR quarantine messages.
# Disposable canonical archives verify publication, byte retention and searchable text.
# A damaged pending object establishes that archived content recovers before ingest.
# Separate index repair must leave canonical mail and the processing queue unchanged.

from collections.abc import Iterator
import base64
import hashlib
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from mailarchiver.catalog import create_catalog, create_search
from mailarchiver.clamav_update import EICAR
from mailarchiver.layout import mbox_directory
from mailarchiver.mailsearch import read_message_bytes
from mailarchiver.message import ParsedMessage
from mailarchiver.plugin_api import MailObject, SourceReference
from mailarchiver.processing.api import ApplicationContext, ArchiveContext, InvocationResponse, ProcessingObject, ProcessingResult, RAW_MESSAGE
from mailarchiver.processing.builtin import ClamAVProcessor, FilingProcessor
from mailarchiver.processing.contracts import MailboxReference, ProcessingPolicy, SourceMetadata
from mailarchiver.processing.production import ProductionPipeline
from mailarchiver.processing.registry import fingerprint, load_processors
from mailarchiver.processing.runtime import RANK_FAILURE, invoke, run
from mailarchiver.processing.store import connect, content_reference, enqueue, snapshot, submit
from mailarchiver.scan_evidence import ScanEvidence, ScanFailure, ScanStatus
from mailarchiver.scanner import ClamScanner
from mailarchiver.standalone_verify import verify_archive
from tests.test_cli_processing import HEADER, cli, configure, ingest
from tests.test_processing import plugin


@pytest.fixture(scope="module")
def scanner() -> Iterator[ClamScanner]:
    with ClamScanner() as instance:
        yield instance


def encrypted_message(*, infected: bool = False) -> bytes:
    """Authored empty PDF encrypted by qpdf with fixture-user/fixture-owner passwords."""
    pdf = (Path(__file__).parent / "data/encrypted-fixture.pdf").read_bytes()
    raw = HEADER + (b'Content-Type: multipart/mixed; boundary="encrypted"\r\n\r\n'
        b'--encrypted\r\nContent-Type: text/plain\r\n\r\nencryptedfixturetoken\r\n'
        b'--encrypted\r\nContent-Type: application/pdf\r\nContent-Disposition: attachment; filename="encrypted.pdf"\r\n'
        b'Content-Transfer-Encoding: base64\r\n\r\n') + base64.encodebytes(pdf)
    if infected:
        raw += (b'\r\n--encrypted\r\nContent-Type: application/octet-stream\r\n'
                b'Content-Transfer-Encoding: base64\r\n\r\n' + base64.encodebytes(EICAR))
    return raw + b'\r\n--encrypted--\r\n'


@pytest.mark.parametrize("status,detail", [
    ("unscannable", "Heuristics.Encrypted.PDF"),
    ("unscannable", "Heuristics.Encrypted.Zip"),
    ("unscannable", "Heuristics.Limits.Exceeded.MaxFileSize"),
    ("unscannable", "unrecognized incomplete scan"),
    ("scanner-error", "worker ended"),
])
def test_incomplete_scan_results_record_evidence_and_allow_filing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: ScanStatus, detail: str,
) -> None:
    """SCANNING: recorded incomplete/error evidence never introduces a manual-review barrier."""
    from mailarchiver import scanner as scanner_module
    path = tmp_path / "message.eml"
    raw = HEADER + b"\r\nunchanged fixture\r\n"
    path.write_bytes(raw)
    reference = content_reference(path)
    item = ProcessingObject(archive=ArchiveContext(path=tmp_path), application=ApplicationContext(policy=ProcessingPolicy()),
        message_id=reference.sha256, message_ref=reference, content_ref=reference, content_type=RAW_MESSAGE, pipeline="ingest")
    evidence = ScanEvidence(status=status, detail=detail, engine_version="recorded-engine", signature_version="recorded-definitions")
    monkeypatch.setattr(scanner_module, "scan_message", lambda *_args: evidence)
    result = ClamAVProcessor().process(item)
    assert result.scan == evidence and result.outcome == "continue"
    filing = FilingProcessor().process(item.model_copy(update={"scan_provenance": status})).filing
    assert filing is not None and filing.mailbox is not None and filing.mailbox.category == "Archive"
    assert any(defect.field == "antivirus" and status in defect.detail for defect in filing.parsed.defects)
    assert path.read_bytes() == raw


def test_unavailable_scanner_retains_all_mail_without_approval(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """SCANNING: real native-library startup failure records errors and automatically preserves later mail."""
    monkeypatch.setenv("MAILARCHIVER_CLAMAV_LIBRARY", str(tmp_path / "missing-library"))
    source = tmp_path / "source"
    source.mkdir()
    originals = [HEADER.replace(b"<parent@example.test>", f"<startup-{index}@example.test>".encode()) + b"\r\nbody\r\n"
                 for index in range(2)]
    for index, raw in enumerate(originals):
        (source / f"{index}.eml").write_bytes(raw)
    owners = tmp_path / "owners.txt"
    owners.write_text("owner@example.test\n", encoding="utf-8")
    archive = tmp_path / "archive"
    cli(archive, "ingest", "--clamav", "--workers", "1", "--owner-names-file", str(owners), str(source))
    with sqlite3.connect(archive / "archive.sqlite3") as catalog:
        rows = catalog.execute("SELECT message_pk FROM messages ORDER BY message_pk").fetchall()
        assert [read_message_bytes(archive, row[0]) for row in rows] == originals
        assert catalog.execute("SELECT count(*) FROM metadata_defects WHERE field='antivirus' AND detail LIKE 'scanner-error:%'").fetchone() == (2,)
    with sqlite3.connect(archive / "processing.sqlite3") as database:
        assert database.execute("SELECT scan_status FROM message_state").fetchall() == [("scanner-error",), ("scanner-error",)]
        payloads = database.execute("SELECT result_json FROM invocations WHERE kind='clamav'").fetchall()
        assert len(payloads) == 2
        for payload, in payloads:
            scan = ProcessingResult.model_validate_json(payload).scan
            assert scan is not None and "startup failed" in scan.detail and "missing-library" in scan.detail
        assert database.execute("SELECT count(*) FROM jobs WHERE status='failed'").fetchone() == (0,)
    assert all((source / f"{index}.eml").read_bytes() == raw for index, raw in enumerate(originals))
    assert not verify_archive(archive)


def test_fresh_worker_failure_is_reused_only_in_the_current_session(tmp_path: Path) -> None:
    """SCANNING: a fresh failed source scan is recorded once instead of immediately scanning again."""
    path = tmp_path / "source.eml"
    path.write_bytes(HEADER + b"\r\nfresh failure\r\n")
    reference = content_reference(path)
    evidence = ScanEvidence(status="scanner-error", detail="original worker timeout", engine_version="recorded-engine")
    metadata = SourceMetadata(source=SourceReference(plugin_kind="files", source_id="fixture", native_id="message", display_name="fixture"),
        source_file_pk=1, work_id="fixture", cursor="0", scan_evidence=evidence, scan_session="current-session")
    item = ProcessingObject(archive=ArchiveContext(path=tmp_path), message_id=reference.sha256, message_ref=reference, content_ref=reference,
        content_type=RAW_MESSAGE, pipeline="ingest", source_metadata=metadata,
        application=ApplicationContext(policy=ProcessingPolicy(scanner_session="current-session")))
    assert ClamAVProcessor().process(item).scan == evidence
    stale = item.model_copy(update={"application": ApplicationContext(policy=ProcessingPolicy(scanner_session="next-session"))})
    retried = ClamAVProcessor().process(stale).scan
    assert retried is not None and retried.status == "scanner-error" and retried.detail != evidence.detail


def test_processor_scan_deadline_is_recorded_without_blocking_filing(tmp_path: Path) -> None:
    """SCANNING: a real dispatcher deadline retains its failed audit but completes the ingest job."""
    raw = HEADER + b"\r\ndeadline fixture\r\n"
    archive, _source = ingest(tmp_path, raw)
    catalog = sqlite3.connect(archive / "archive.sqlite3", check_same_thread=False)
    search = sqlite3.connect(archive / "search.sqlite3", check_same_thread=False)

    def no_publication(_source: MailObject, _parsed: ParsedMessage, _mailbox: MailboxReference, _file: int) -> int:
        raise AssertionError("An already preserved message must not be republished")

    pipeline = ProductionPipeline(archive, catalog, search, ProcessingPolicy(), no_publication)
    try:
        pipeline.plugins = tuple(plugin.model_copy(update={"manifest": plugin.manifest.model_copy(update={"timeout_seconds": 1e-9})})
                                 if plugin.manifest.kind == "clamav" else plugin for plugin in pipeline.plugins)
        pipeline.registry_hash = fingerprint(pipeline.plugins)
        root = ProcessingObject.model_validate_json(pipeline.database.execute("SELECT root_item_json FROM message_state").fetchone()[0])
        detection = ScanEvidence(status="infected", detail="recorded EICAR detection")
        deadline = InvocationResponse.failure(TimeoutError("processor timeout")).model_copy(update={"result": ProcessingResult(scan=detection)})
        accepted = pipeline.failed(root, next(plugin for plugin in pipeline.plugins if plugin.manifest.kind == "clamav"), deadline)
        assert accepted is not None and accepted.scan == detection and accepted.outcome == "abort-message"
        assert accepted.filing is not None and accepted.filing.mailbox is not None and accepted.filing.mailbox.category == "INFECTED"
        with pipeline.database:
            enqueue(pipeline.database, root.model_copy(update={"generation": "deadline-fixture", "pipeline": "ingest"}), pipeline.registry_hash)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(pipeline.resume_ingest).result(timeout=10)
        response = pipeline.database.execute("SELECT result_json FROM invocations WHERE kind='clamav' AND status='failed'").fetchone()
        assert response is not None and InvocationResponse.model_validate_json(response[0]).timed_out
        assert pipeline.database.execute("SELECT scan_status FROM message_state").fetchone() == ("scanner-error",)
        assert pipeline.database.execute("SELECT count(*) FROM jobs WHERE status='failed'").fetchone() == (0,)
        assert read_message_bytes(archive, 1) == raw
    finally:
        pipeline.close()
        catalog.close()
        search.close()


def test_scan_result_survives_a_post_result_deadline(tmp_path: Path) -> None:
    """SCANNING: a deadline after a known positive cannot erase the detection and allow ordinary filing."""
    plugin(tmp_path, "clamav", 'from mailarchiver.scan_evidence import ScanEvidence\n'
           'time.sleep(0.03)\nreturn ProcessingResult(scan=ScanEvidence(status="infected", detail="recorded detection"))', timeout=0.005)
    spec, = load_processors((tmp_path,))
    reference = content_reference(Path(__file__).parent / "data/credible_date_with_quoted_body.eml")
    item = ProcessingObject(archive=ArchiveContext(path=tmp_path), message_id=reference.sha256,
                            message_ref=reference, content_ref=reference, content_type=RAW_MESSAGE, pipeline="ingest")
    with ThreadPoolExecutor(max_workers=1) as pool:
        response = pool.submit(invoke, spec, item).result(timeout=5)
    assert response.timed_out and response.error is not None
    assert response.result is not None and response.result.scan is not None
    assert response.result.scan.status == "infected"


@pytest.mark.parametrize("infected", [False, True])
def test_real_encrypted_pdf_is_preserved_and_later_mail_continues(tmp_path: Path, scanner: ClamScanner, infected: bool) -> None:
    """SCANNING: native encrypted-PDF warnings retain evidence/bytes; EICAR still quarantines."""
    raw = encrypted_message(infected=infected)
    evidence = scanner.scan_sample(raw)
    assert evidence.status == ("infected" if infected else "unscannable")
    if not infected:
        assert evidence.detail == "Heuristics.Encrypted.PDF"
    source = tmp_path / "input"
    source.mkdir()
    (source / "01-encrypted.eml").write_bytes(raw)
    later = HEADER.replace(b"<parent@example.test>", b"<later@example.test>") + b"\r\nlaterfixturetoken\r\n"
    (source / "02-later.eml").write_bytes(later)
    owners = tmp_path / "owners.txt"
    owners.write_text("owner@example.test\n", encoding="utf-8")
    archive = tmp_path / "archive"
    cli(archive, "ingest", "--clamav", "--workers", "1", "--owner-names-file", str(owners), str(source))
    with sqlite3.connect(archive / "archive.sqlite3") as catalog:
        rows = catalog.execute("SELECT message_pk,sha256,category FROM messages").fetchall()
        assert len(rows) == 2
        encrypted = next(row for row in rows if row[1] == hashlib.sha256(raw).hexdigest())
        assert encrypted[2] == ("INFECTED" if infected else "Archive")
        assert read_message_bytes(archive, encrypted[0]) == raw
        if not infected:
            assert catalog.execute("SELECT detail FROM metadata_defects WHERE message_pk=? AND field='antivirus'",
                                   (encrypted[0],)).fetchone()[0].startswith("unscannable:")
    with sqlite3.connect(archive / "processing.sqlite3") as database:
        scan, payload = database.execute("SELECT s.scan_status,i.result_json FROM message_state s JOIN jobs j "
            "ON json_extract(j.item_json,'$.message_id')=s.message_id JOIN invocations i USING(job_id) "
            "WHERE s.message_id=? AND i.kind='clamav' AND i.status='completed'", (hashlib.sha256(raw).hexdigest(),)).fetchone()
        assert scan == evidence.status
        saved = ProcessingResult.model_validate_json(payload)
        assert saved.scan is not None
        assert saved.scan.status == scan and saved.scan.engine_version and saved.scan.signature_version
        assert saved.scan.detail == evidence.detail
    with sqlite3.connect(archive / "search.sqlite3") as search:
        assert search.execute("SELECT count(*) FROM message_fts WHERE message_fts MATCH 'laterfixturetoken'").fetchone() == (1,)
    assert (source / "01-encrypted.eml").read_bytes() == raw
    assert (source / "02-later.eml").read_bytes() == later
    assert not verify_archive(archive)


@pytest.mark.parametrize("status", ["scanner-error", "unscannable", "not-scanned"])
@pytest.mark.parametrize("infected", [False, True])
def test_saved_unsuccessful_scans_are_retried_with_the_current_engine(
    tmp_path: Path, scanner: ClamScanner, status: ScanStatus, infected: bool,
) -> None:
    """PROCESSING recovery: unsuccessful saved evidence cannot substitute for a fresh scan."""
    raw = HEADER + b"\r\n" + (EICAR if infected else b"recoverable clean message")
    path = tmp_path / "message.eml"
    path.write_bytes(raw)
    reference = content_reference(path)
    metadata = SourceMetadata(source=SourceReference(plugin_kind="files", source_id="fixture", native_id="message",
        display_name="recovery fixture"), source_file_pk=1, work_id="fixture", cursor="0",
        scan_evidence=ScanEvidence(status=status, detail="saved failure"))
    item = ProcessingObject(archive=ArchiveContext(path=tmp_path),
        application=ApplicationContext(policy=ProcessingPolicy(scanner_session=scanner.session)),
        message_id=reference.sha256, message_ref=reference, content_ref=reference,
        content_type=RAW_MESSAGE, pipeline="ingest", source_metadata=metadata)
    result = ClamAVProcessor().process(item)
    assert result.scan is not None and result.scan.status == ("infected" if infected else "clean")
    assert result.scan.engine_version and result.scan.signature_version
    assert (result.filing is not None) == infected
    assert path.read_bytes() == raw


def seed_failed_ingest(archive: Path, pending: Path, status: ScanStatus = "scanner-error") -> str:
    """Create the typed, retained failure state of a prior run, without publishing mail."""
    catalog = create_catalog(archive / "archive.sqlite3")
    search = create_search(archive / "search.sqlite3")
    with sqlite3.connect(archive / "processing.sqlite3") as database:
        original = ProcessingObject.model_validate_json(database.execute("SELECT root_item_json FROM message_state LIMIT 1").fetchone()[0])
        policy = ProcessingPolicy.model_validate_json(database.execute("SELECT value FROM processing_settings WHERE name='policy'").fetchone()[0])
    assert original.source_metadata is not None
    saved = ScanEvidence(status=status, detail="Heuristics.Encrypted.PDF" if status == "unscannable" else "ClamAV worker ended: ",
                         engine_version="prior-engine")
    metadata = original.source_metadata.model_copy(update={"cursor": "pending", "scan_evidence": saved})
    def no_publication(_source: MailObject, _parsed: ParsedMessage, _mailbox: MailboxReference, _file: int) -> int:
        raise AssertionError("seeding durable work must not publish canonical mail")
    pipeline = ProductionPipeline(archive, catalog, search, policy.model_copy(update={"scan_policy": "clamav"}), no_publication)
    try:
        reference = snapshot(archive, pending)
        with pipeline.database:
            item = pipeline._admit(reference, metadata)
            enqueue(pipeline.database, item, pipeline.registry_hash)
            job_id = pipeline.database.execute("SELECT job_id FROM jobs WHERE json_extract(item_json,'$.message_id')=?", (item.message_id,)).fetchone()[0]
            pipeline.database.execute("UPDATE jobs SET status='failed',detail=? WHERE job_id=?", (RANK_FAILURE, job_id))
            failure = InvocationResponse.failure(ScanFailure(saved))
            pipeline.database.execute("INSERT INTO invocations(job_id,kind,version,rank,status,result_json,error) "
                "VALUES(?,'clamav','1',1,'failed',?,?)", (job_id, failure.model_dump_json(), failure.error))
        return item.message_id
    finally:
        pipeline.close()
        catalog.close()
        search.close()


@pytest.mark.parametrize("infected", [False, True])
def test_accepted_scan_failure_replays_after_interruption(tmp_path: Path, infected: bool) -> None:
    """SCANNING recovery: accepted failures replay unchanged before quarantine and after ordinary filing."""
    archive, _source = ingest(tmp_path, HEADER + b"\r\ninitial fixture\r\n")
    pending = tmp_path / "pending.eml"
    raw = HEADER.replace(b"<parent@example.test>", b"<interrupted@example.test>") + b"\r\n" + EICAR
    pending.write_bytes(raw)
    message_id = seed_failed_ingest(archive, pending)
    catalog = create_catalog(archive / "archive.sqlite3")
    search = create_search(archive / "search.sqlite3")

    def no_publication(_source: MailObject, _parsed: ParsedMessage, _mailbox: MailboxReference, _file: int) -> int:
        raise AssertionError("Seeding an interrupted checkpoint must not publish mail")

    with sqlite3.connect(archive / "processing.sqlite3") as database:
        policy = ProcessingPolicy.model_validate_json(database.execute(
            "SELECT value FROM processing_settings WHERE name='policy'").fetchone()[0])
    pipeline = ProductionPipeline(archive, catalog, search, policy, no_publication)
    try:
        job_id, payload = pipeline.database.execute("SELECT job_id,item_json FROM jobs "
            "WHERE json_extract(item_json,'$.message_id')=?", (message_id,)).fetchone()
        root = ProcessingObject.model_validate_json(payload)
        response = InvocationResponse.failure(TimeoutError("recorded scanner deadline"))
        if infected:
            response = response.model_copy(update={"result": ProcessingResult(
                scan=ScanEvidence(status="infected", detail="recorded EICAR detection"))})
        accepted = pipeline.failed(root, next(spec for spec in pipeline.plugins if spec.manifest.kind == "clamav"), response)
        assert accepted is not None
        with pipeline.database:
            pipeline.database.execute("UPDATE invocations SET result_json=?,error=? WHERE job_id=? AND kind='clamav'",
                                      (response.model_dump_json(), response.error, job_id))
            pipeline.database.execute("INSERT INTO accepted_invocations SELECT invocation_id,? FROM invocations "
                "WHERE job_id=? AND kind='clamav'", (accepted.model_dump_json(), job_id))
    finally:
        pipeline.close()
        catalog.close()
        search.close()
    for attempt in range(2):
        cli(archive, "process", "--phase", "all")
        with sqlite3.connect(archive / "archive.sqlite3") as catalog:
            rows = catalog.execute("SELECT message_pk,category FROM messages WHERE sha256=?", (message_id,)).fetchall()
            assert len(rows) == 1 and rows[0][1] == ("INFECTED" if infected else "Archive")
            assert read_message_bytes(archive, rows[0][0]) == raw
        with sqlite3.connect(archive / "processing.sqlite3") as database:
            assert database.execute("SELECT scan_status FROM message_state WHERE message_id=?", (message_id,)).fetchone() == (
                "infected" if infected else "scanner-error",)
            assert database.execute("SELECT status FROM invocations WHERE job_id=? AND kind='clamav'", (job_id,)).fetchall() == [("failed",)]
            if attempt == 0:
                # Reproduce an interruption after canonical publication but before root completion.
                database.execute("UPDATE jobs SET status='running' WHERE job_id=?", (job_id,))
        assert not verify_archive(archive)


@pytest.mark.parametrize("content", ["clean", "infected", "encrypted"])
def test_cli_recovers_failed_ingest_and_rebuilds_archived_search(tmp_path: Path, content: str) -> None:
    """PROCESSING/SEARCH: durable retry scans retained bytes, files once and restores search."""
    archived_raw = HEADER + b"\r\narchivedrecoverytoken\r\n"
    archive, source = ingest(tmp_path, archived_raw)
    infected = content == "infected"
    pending_raw = encrypted_message() if content == "encrypted" else HEADER + b"\r\n" + (EICAR if infected else b"pendingrecoverytoken\r\n")
    pending_raw = pending_raw.replace(b"<parent@example.test>", b"<pending@example.test>")
    pending = tmp_path / "pending.eml"
    pending.write_bytes(pending_raw)
    message_id = seed_failed_ingest(archive, pending, status="unscannable" if content == "encrypted" else "scanner-error")
    with sqlite3.connect(archive / "search.sqlite3") as search:
        assert search.execute("SELECT count(*) FROM message_metadata").fetchone()[0] == 0
    for _ in range(2):
        cli(archive, "process", "--phase", "all")
        with sqlite3.connect(archive / "archive.sqlite3") as catalog:
            rows = catalog.execute("SELECT message_pk,sha256,category FROM messages ORDER BY message_pk").fetchall()
            assert len(rows) == 2
            assert rows[-1][1:] == (message_id, "INFECTED" if infected else "Archive")
        assert read_message_bytes(archive, rows[-1][0]) == pending_raw
        with sqlite3.connect(archive / "search.sqlite3") as search:
            assert search.execute("SELECT count(*) FROM message_fts WHERE message_fts MATCH 'archivedrecoverytoken'").fetchone() == (1,)
            assert search.execute("SELECT count(*) FROM message_metadata").fetchone() == (1 if infected else 2,)
        assert not verify_archive(archive)
    with sqlite3.connect(archive / "processing.sqlite3") as database:
        expected_scan = "unscannable" if content == "encrypted" else "infected" if infected else "clean"
        assert database.execute("SELECT scan_status FROM message_state WHERE message_id=?", (message_id,)).fetchone() == (expected_scan,)
        assert database.execute("SELECT status FROM invocations WHERE kind='clamav' AND job_id IN "
            "(SELECT job_id FROM jobs WHERE json_extract(item_json,'$.message_id')=?) ORDER BY invocation_id", (message_id,)).fetchall() == [("failed",), ("completed",)]
    assert source.read_bytes() == archived_raw and pending.read_bytes() == pending_raw


def test_pending_ingest_failure_does_not_starve_archived_search_recovery(tmp_path: Path) -> None:
    """SEARCH: rebuild eligible archived content before an unrelated pending ingest fails."""
    raw = HEADER + b"\r\narchivedsurvivortoken\r\n"
    archive, _source = ingest(tmp_path, raw)
    pending = tmp_path / "pending.eml"
    pending.write_bytes(HEADER + b"\r\npending content\r\n")
    message_id = seed_failed_ingest(archive, pending)
    (archive / "objects" / message_id).write_bytes(b"damaged retained input")
    result = subprocess.run([sys.executable, "-m", "mailarchiver", "--archive", str(archive), "process", "--phase", "all"],
                            capture_output=True, text=True, check=False, timeout=60)
    assert result.returncode != 0 and "input digest mismatch" in result.stderr
    with sqlite3.connect(archive / "search.sqlite3") as search:
        assert search.execute("SELECT count(*) FROM message_fts WHERE message_fts MATCH 'archivedsurvivortoken'").fetchone() == (1,)
    with sqlite3.connect(archive / "processing.sqlite3") as database:
        assert database.execute("SELECT status,detail FROM jobs WHERE json_extract(item_json,'$.message_id')=?", (message_id,)).fetchone() == ("failed", "input digest mismatch")
    assert read_message_bytes(archive, 1) == raw


def test_index_repair_does_not_resume_pending_ingest(tmp_path: Path) -> None:
    """SEARCH: atomic refresh-index repairs searchable canonical mail without touching the queue."""
    raw = HEADER + b"\r\nindependentrecoverytoken\r\n"
    archive, _source = ingest(tmp_path, raw)
    pending = tmp_path / "pending.eml"
    pending.write_bytes(HEADER + b"\r\nnot yet archived\r\n")
    seed_failed_ingest(archive, pending)
    processing = (archive / "processing.sqlite3").read_bytes()
    mailbox = next(mbox_directory(archive).glob("*.mbox"))
    digest = hashlib.sha256(mailbox.read_bytes()).hexdigest()
    cli(archive, "refresh-index", "--index-attachments")
    with sqlite3.connect(archive / "search.sqlite3") as search:
        assert search.execute("SELECT count(*) FROM message_fts WHERE message_fts MATCH 'independentrecoverytoken'").fetchone() == (1,)
        assert search.execute("SELECT count(*) FROM message_metadata").fetchone() == (1,)
    assert (archive / "processing.sqlite3").read_bytes() == processing
    assert hashlib.sha256(mailbox.read_bytes()).hexdigest() == digest


def test_failed_archived_extraction_does_not_block_new_mail(tmp_path: Path) -> None:
    """PROCESSING: failed derived work stays reportable without blocking canonical ingestion."""
    raw = HEADER + b"Content-Type: text/plain\r\n\r\n" + b"x" * 128
    archive, source = ingest(tmp_path, raw, "--defer-content")
    configure(archive, "text-index", {"max_text_bytes": 32})
    new_raw = HEADER.replace(b"<parent@example.test>", b"<new@example.test>") + b"\r\nnew mail\r\n"
    incoming = tmp_path / "incoming.eml"
    incoming.write_bytes(new_raw)
    result = subprocess.run([sys.executable, "-m", "mailarchiver", "--archive", str(archive), "ingest",
                             "--no-scan", str(incoming)], capture_output=True, text=True, check=False, timeout=60)
    assert result.returncode != 0 and "original content retained" in result.stderr
    with sqlite3.connect(archive / "archive.sqlite3") as catalog:
        assert catalog.execute("SELECT count(*) FROM messages").fetchone() == (2,)
    with sqlite3.connect(archive / "processing.sqlite3") as database:
        assert database.execute("SELECT count(*) FROM invocations WHERE kind='text-index' AND status='failed'").fetchone()[0] > 0
        assert database.execute("SELECT count(*) FROM jobs WHERE status='failed'").fetchone()[0] > 0
    assert read_message_bytes(archive, 1) == raw and read_message_bytes(archive, 2) == new_raw
    assert source.read_bytes() == raw and incoming.read_bytes() == new_raw
    assert not verify_archive(archive)


def test_healthy_archived_content_recovers_past_an_unrelated_failure(tmp_path: Path) -> None:
    """SEARCH: failed extraction and failed pending ingest must not starve healthy archived mail."""
    oversized = HEADER + b"Content-Type: text/plain\r\n\r\n" + b"x" * 128
    archive, _source = ingest(tmp_path, oversized, "--defer-content")
    healthy = tmp_path / "healthy.eml"
    healthy.write_bytes(HEADER.replace(b"<parent@example.test>", b"<healthy@example.test>") + b"\r\nhealthytoken\r\n")
    cli(archive, "ingest", "--no-scan", "--defer-content", str(healthy))
    pending = tmp_path / "pending.eml"
    pending.write_bytes(HEADER + b"\r\npending mail\r\n")
    message_id = seed_failed_ingest(archive, pending)
    configure(archive, "text-index", {"max_text_bytes": 32})
    (archive / "objects" / message_id).write_bytes(b"damaged pending input")
    result = subprocess.run([sys.executable, "-m", "mailarchiver", "--archive", str(archive), "process", "--phase", "all"],
                            capture_output=True, text=True, check=False, timeout=60)
    assert result.returncode != 0 and "input digest mismatch" in result.stderr
    with sqlite3.connect(archive / "search.sqlite3") as search:
        assert search.execute("SELECT count(*) FROM message_fts WHERE message_fts MATCH 'healthytoken'").fetchone() == (1,)
    with sqlite3.connect(archive / "processing.sqlite3") as database:
        assert database.execute("SELECT count(*) FROM jobs WHERE status='failed'").fetchone()[0] >= 2
    assert read_message_bytes(archive, 1) == oversized


@pytest.mark.parametrize("explicit_stop", [False, True])
def test_recovery_preserves_failed_dependencies_and_explicit_stop(tmp_path: Path, explicit_stop: bool) -> None:
    """PROCESSING: independent recovery cannot run descendants of a failed prerequisite or ignore fail-import."""
    plugin(tmp_path, "emit", 'return ProcessingResult(emissions=(Emission(content_ref=item.content_ref, content_type="text/plain"),))', emits='"text/plain"')
    failure = 'return ProcessingResult(outcome="fail-import")' if explicit_stop else 'raise ValueError("blocked root")'
    plugin(tmp_path, "gate", 'with item.content_ref.open() as source:\n    blocked = source.read(7) == b"blocked"\n'
           f'if blocked:\n    {failure}\nreturn ProcessingResult()', rank=2)
    plugin(tmp_path, "child", 'return ProcessingResult()', subscribes="text/plain")
    plugins = load_processors((tmp_path,))
    archive = tmp_path / "archive"
    for name in ("blocked", "healthy"):
        (tmp_path / name).write_bytes(name.encode())
    with connect(archive, create=True) as database:
        for name in ("blocked", "healthy"):
            submit(database, archive, tmp_path / name, fingerprint(plugins))
        if explicit_stop:
            with pytest.raises(RuntimeError, match="processor requested fail-import"):
                run(database, plugins, continue_after_failure=True)
            assert database.execute("SELECT count(*) FROM invocations WHERE kind='child'").fetchone() == (0,)
        else:
            result = run(database, plugins, continue_after_failure=True)
            assert (result.failed, result.pending, result.completed) == (1, 1, 2)
            assert next(stat for stat in result.statistics if stat.kind == "child").invocations == 1
