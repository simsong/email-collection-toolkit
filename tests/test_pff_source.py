# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""OST requirements: real reader, cache evidence, bounded failures and exact dedup."""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import subprocess
import sys
from email import policy
from email.parser import BytesParser
from pathlib import Path

import pytest

from mailarchiver.pff_source import PffReceipt
from mailarchiver.plugin_api import MailContainer, MailObject, SourceSpec
from mailarchiver.plugin_loader import load_plugins
from mailarchiver.pst_source import validate_record
from mailarchiver.standalone_verify import verify_archive
from tests.test_cli_processing import cli, configure

ROOT = Path(__file__).resolve().parents[1]
OST = ROOT / "tests/fixtures/pff/sample.ost"
PST = ROOT / "rust/mct-importer/tests/fixtures/mail.pst"
EMPTY_PST = ROOT / "rust/mct-importer/tests/fixtures/empty.pst"


@pytest.mark.skipif(os.name == "nt", reason="POSIX process liveness uses kill(pid, 0)")
def test_host_kills_and_reaps_stuck_converter(tmp_path: Path) -> None:
    """A blocked native child cannot outlive the host's extraction deadline."""
    from mailarchiver.pff_source import PffSettings, run_converter
    output = tmp_path / "output"
    command = [sys.executable, "-c", "import os,time; print(os.getpid(), flush=True); time.sleep(60)"]
    with pytest.raises(TimeoutError):
        list(run_converter(command, output, tmp_path / "stderr", PffSettings(timeout_seconds=1),
                           "fixture", os.environ.copy()))
    pid = int(output.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_host_bounds_fast_converter_output(tmp_path: Path) -> None:
    """A producer that exits before polling still cannot retain unbounded output."""
    from mailarchiver.pff_source import PffSettings, run_converter
    output = tmp_path / "output"
    command = [sys.executable, "-c", "import sys; sys.stdout.write('x' * 100000)"]
    with pytest.raises(ValueError, match="output limit"):
        list(run_converter(command, output, tmp_path / "stderr", PffSettings(max_output_bytes=32),
                           "fixture", os.environ.copy()))
    assert output.stat().st_size == 32


def test_reader_is_not_loaded_in_the_host() -> None:
    """Loading every built-in plugin must not load or require the libpff extension."""
    result = subprocess.run([sys.executable, "-c",
        "from mailarchiver.plugin_loader import load_plugins; load_plugins(); "
        "import sys, importlib.util; assert 'pypff' not in sys.modules; "
        "assert importlib.util.find_spec('pypff') is None"], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_real_ost_external_scan_stage(tmp_path: Path) -> None:
    """Both external stages run on a real OST; clean records have no scan provenance headers."""
    registry = load_plugins(archive=tmp_path, scan_policy="clamav")
    source = next(item.implementation for item in registry.sources if item.manifest.kind == "file-folder")
    container = next(item for item in source.discover(SourceSpec(locator=str(OST))) if isinstance(item, MailContainer))
    records: list[MailObject] = []
    with pytest.raises(RuntimeError, match="libpff extraction incomplete"):
        for event in source.messages(container, None):
            if isinstance(event, MailObject):
                records.append(event)
    assert len(records) == 80
    assert all(item.scan_responsibility == "producer" for item in records)
    assert all(b"X-ClamAV-Engine-Version:" not in item.raw for item in records)
    assert list(tmp_path.glob("processing-libpff/*/scanner-stderr.txt"))


def test_external_scan_adds_only_infected_headers(tmp_path: Path) -> None:
    """Real external scanner preserves clean bytes and marks EICAR before API admission."""
    from mailarchiver.clamav_definitions import library_path, selected_definitions, certificates_path
    from mailarchiver.clamav_update import EICAR
    from mailarchiver.pff_source import executable
    prefix = b"From fixture Thu Jan  1 00:00:00 1970\nX-Imported-URI: file:///fixture\r\nX-Importer-Name: fixture\r\nX-Importer-Version: 1\r\nFrom: fixture@example.test\r\nContent-Type: application/octet-stream\r\n\r\n"
    clean = prefix + b"ordinary body\r\n>From quoted body line\r\n\n"
    infected = prefix + EICAR + b"\r\n\n"
    path = tmp_path / "input.mboxrd"
    path.write_bytes(clean + infected)
    environment = {**os.environ, "MAILARCHIVER_SCAN": "1",
        "MAILARCHIVER_CLAMAV_LIBRARY": str(library_path()),
        "MAILARCHIVER_CLAMAV_DATABASE": str(selected_definitions().directory)}
    if certs := certificates_path():
        environment["MAILARCHIVER_CLAMAV_CERTIFICATES"] = str(certs)
    result = subprocess.run([str(executable("mcti-scan")), str(path)], env=environment,
                            capture_output=True, check=False, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith(clean)
    assert result.stdout.count(b"X-ClamAV-Detection:") == 1
    assert result.stdout.count(b"X-ClamAV-Engine-Version:") == 1
    assert result.stdout.count(b"X-ClamAV-Definitions-Version:") == 1
    assert result.stdout.endswith(EICAR + b"\r\n\n")
    assert path.read_bytes() == clean + infected


def test_real_ost_external_preserves_cache_and_attachment_bytes(tmp_path: Path) -> None:
    """Genuine SO/version-23 OST emits stable MIME and omits unreadable items."""
    fixture = tmp_path / "cache with spaces.ost"
    shutil.copyfile(OST, fixture)
    before = fixture.read_bytes()
    assert before[8:12] == b"SO\x17\x00"
    registry = load_plugins(archive=tmp_path)
    source = next(item.implementation for item in registry.sources if item.manifest.kind == "file-folder")
    container, = [item for item in source.discover(SourceSpec(locator=str(fixture))) if isinstance(item, MailContainer)]
    assert container.parser_kind == "ost" and container.source.relationship.role == "cache"
    runs: list[list[MailObject]] = []
    for _attempt in range(2):
        records: list[MailObject] = []
        with pytest.raises(RuntimeError, match="libpff extraction incomplete"):
            for event in source.messages(container, None):
                if isinstance(event, MailObject):
                    validate_record(event.raw)
                    records.append(event)
        runs.append(records)
    assert runs[0] == runs[1]
    assert len(runs[0]) == 80
    for record in runs[0]:
        assert all(not part.defects for part in BytesParser(policy=policy.default).parsebytes(record.raw).walk())
    selected = next(item for item in runs[0] if item.cursor == "item:2182532")
    message = BytesParser(policy=policy.default).parsebytes(selected.raw)
    assert str(message["Subject"]) == "newsletter copy"
    assert not message.defects
    attachments = [item for item in message.iter_attachments() if item.get_filename() != "original-transport-headers.txt"]
    assert len(attachments) == 11
    image = attachments[0].get_payload(decode=True)
    assert isinstance(image, bytes) and len(image) == 20833
    assert hashlib.sha256(image).hexdigest() == "0f4ff85802be13ad86f82b7e6e886e5a9a6a77ddb84995bc5a7657a3d40c8f9a"
    html = next(part.get_payload(decode=True) for part in message.walk() if part.get_content_type() == "text/html")
    assert isinstance(html, bytes)
    assert hashlib.sha256(html).hexdigest() == "6d52c74376341391bbe58f07ea0fc880578a7fb4160c31eff6b6a2c19017da91"
    rtf_message = BytesParser(policy=policy.default).parsebytes(next(item.raw for item in runs[0] if item.cursor == "item:2182916"))
    rtf = next(part.get_payload(decode=True) for part in rtf_message.walk() if part.get_content_type() == "application/rtf")
    assert isinstance(rtf, bytes)
    assert hashlib.sha256(rtf).hexdigest() == "e2822fc116bc97613129eec5833d5728a347683452a4951c1ca3c8b4ddd39355"
    assert all(b"X-Mailarchiver-Extraction-Incomplete:" not in item.raw for item in runs[0])
    for path in tmp_path.glob("processing-libpff/*/receipt.json"):
        receipt = PffReceipt.model_validate_json(path.read_text())
        assert receipt.process_id != os.getpid() and receipt.process_id > 0
        assert (receipt.encountered, receipt.emitted, receipt.non_mail, receipt.errors) == (92, 80, 11, 1)
        assert not receipt.complete and receipt.content_type == 111
        assert receipt.source_sha256 == hashlib.sha256(before).hexdigest()
        assert "attachment method 5" in path.with_name("diagnostics.jsonl").read_text()
    assert fixture.read_bytes() == before


@pytest.mark.parametrize(("fixture", "extension", "kind"), [(OST, ".pst", "ost"), (EMPTY_PST, ".ost", "pst")])
def test_outlook_header_takes_precedence_over_extension(tmp_path: Path, fixture: Path, extension: str, kind: str) -> None:
    """Renaming a cache or store cannot switch the reader selected by its internal format."""
    path = tmp_path / ("renamed" + extension)
    shutil.copyfile(fixture, path)
    registry = load_plugins(archive=tmp_path)
    source = next(item.implementation for item in registry.sources if item.manifest.kind == "file-folder")
    container, = [item for item in source.discover(SourceSpec(locator=str(path))) if isinstance(item, MailContainer)]
    assert container.parser_kind == kind


@pytest.mark.parametrize("setting", ["timeout_seconds", "max_message_bytes"])
def test_libpff_limits_leave_retryable_evidence(tmp_path: Path, setting: str) -> None:
    """A deadline or byte cap cannot silently mark an unprocessed cache complete."""
    configure(tmp_path, "ost", {setting: 0.000000001 if setting == "timeout_seconds" else 64})
    registry = load_plugins(archive=tmp_path)
    source = next(item.implementation for item in registry.sources if item.manifest.kind == "file-folder")
    container = next(item for item in source.discover(SourceSpec(locator=str(OST))) if isinstance(item, MailContainer))
    with pytest.raises((RuntimeError, TimeoutError)):
        list(source.messages(container, None))
    path, = tmp_path.glob("processing-libpff/*/receipt.json")
    receipt = PffReceipt.model_validate_json(path.read_text())
    assert not receipt.complete and receipt.emitted == 0 and receipt.failure


def test_ost_cli_directory_ingest_and_search(tmp_path: Path) -> None:
    """Documented directory ingest and search recover OST subjects through the real CLI pipeline."""
    directory = tmp_path / "source"
    directory.mkdir()
    shutil.copyfile(OST, directory / "mail.ost")
    owners = tmp_path / "owners.txt"
    owners.write_text("owner@example.test\n")
    archive = tmp_path / "archive"
    result = subprocess.run([sys.executable, "-m", "mailarchiver", "--archive", str(archive), "ingest", "--no-scan",
                             "--owner-names-file", str(owners), str(directory)],
                            capture_output=True, text=True, check=False, timeout=120)
    assert result.returncode != 0 and "libpff extraction incomplete" in result.stderr
    with sqlite3.connect(archive / "archive.sqlite3") as db:
        assert db.execute("SELECT count(*) FROM messages").fetchone() == (80,)
    cli(archive, "process", "--phase", "content")
    search = subprocess.run([sys.executable, "-m", "mailarchiver.mailsearch", "--archive", str(archive), "subject:newsletter"],
                            capture_output=True, text=True, check=False, timeout=30)
    assert search.returncode == 0 and "newsletter copy" in search.stdout
    assert not verify_archive(archive)
