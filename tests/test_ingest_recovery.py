# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Exercise recovery from saved antivirus failures without contacting a mail server.
# Typed failed-ingest roots reproduce the durable state left by an interrupted run.
# Real ClamAV scans distinguish clean messages from EICAR quarantine messages.
# Disposable canonical archives verify publication, byte retention and searchable text.
# A damaged pending object establishes that archived content recovers before ingest.
# Separate index repair must leave canonical mail and the processing queue unchanged.

from collections.abc import Iterator
import hashlib
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from mailarchiver.catalog import create_catalog, create_search
from mailarchiver.clamav_update import EICAR
from mailarchiver.layout import mbox_directory
from mailarchiver.mailsearch import read_message_bytes
from mailarchiver.message import ParsedMessage
from mailarchiver.plugin_api import MailObject, SourceReference
from mailarchiver.processing.api import ApplicationContext, ArchiveContext, InvocationResponse, ProcessingObject, RAW_MESSAGE
from mailarchiver.processing.builtin import ClamAVProcessor
from mailarchiver.processing.contracts import MailboxReference, ProcessingPolicy, SourceMetadata
from mailarchiver.processing.production import ProductionPipeline
from mailarchiver.processing.registry import fingerprint, load_processors
from mailarchiver.processing.runtime import RANK_FAILURE, run
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
    saved = ScanEvidence(status=status, detail="ClamAV worker ended: ", engine_version="prior-engine")
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
def test_cli_recovers_failed_ingest_and_rebuilds_archived_search(tmp_path: Path, infected: bool) -> None:
    """PROCESSING/SEARCH: durable retry scans retained bytes, files once and restores search."""
    archived_raw = HEADER + b"\r\narchivedrecoverytoken\r\n"
    archive, source = ingest(tmp_path, archived_raw)
    pending_raw = HEADER.replace(b"<parent@example.test>", b"<pending@example.test>") + b"\r\n" + (EICAR if infected else b"pendingrecoverytoken\r\n")
    pending = tmp_path / "pending.eml"
    pending.write_bytes(pending_raw)
    message_id = seed_failed_ingest(archive, pending)
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
        assert database.execute("SELECT scan_status FROM message_state WHERE message_id=?", (message_id,)).fetchone() == ("infected" if infected else "clean",)
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
