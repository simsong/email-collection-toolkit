# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""OST requirements: real reader, cache evidence, bounded failures and exact dedup."""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import subprocess
import sys
from concurrent.futures import CancelledError, ThreadPoolExecutor
from email import policy
from email.parser import BytesParser
from pathlib import Path
from threading import Event, Timer

import pytest

from mailarchiver.pff_source import PffReceipt
from mailarchiver.change_signal import ChangeEvent
from mailarchiver.file_events import ProcessActivity, wait_for_path_state
from mailarchiver.plugin_api import MailContainer, MailObject, SourceSpec
from mailarchiver.plugin_loader import load_plugins
from mailarchiver.pst_source import validate_record
from mailarchiver.standalone_verify import verify_archive
from tests.test_cli_processing import cli, configure
from scripts.ci_diagnostics import phase

ROOT = Path(__file__).resolve().parents[1]
OST = ROOT / "tests/fixtures/pff/sample.ost"
PST = ROOT / "rust/mct-importer/tests/fixtures/mail.pst"
EMPTY_PST = ROOT / "rust/mct-importer/tests/fixtures/empty.pst"


@pytest.mark.parametrize("kind", ["pst", "pff", "pst-ingest"])
def test_cancellation_reaps_silent_acknowledged_importer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    """ACQUISITION: a real stop wakes silent extraction and reaps its child before the failure watchdog."""
    import mailarchiver.pff_source as pff_module
    import mailarchiver.pst_source as pst_module
    from mailarchiver.__main__ import IngestInterrupted, IngestRequest, run_ingest
    from mailarchiver.owner_rules import OwnerRules

    cancellation, entered, expired = ChangeEvent(), Event(), Event()
    children: list[subprocess.Popen[bytes]] = []

    class ObservedActivity(ProcessActivity):
        def __init__(self, child: subprocess.Popen[bytes], paths: list[Path], stop: ChangeEvent | None = None) -> None:
            children.append(child)  # Observe real ownership, not a fake child or readiness result.
            super().__init__(child, paths, stop)

        def wait(self, timeout: float) -> bool:
            entered.set()
            return super().wait(timeout)

    def contain_failure() -> None:
        expired.set()
        for child in children:
            if child.poll() is None:
                child.kill()

    watchdog = Timer(10, contain_failure)
    ready, producer = tmp_path / "ready", tmp_path / "silent.pst"
    producer.write_text(
        "# Run a real silent extraction fixture until its owner cancels.\n"
        "# Publish readiness once, before waiting on an actual flag.\n"
        "# This script never reads or rewrites a real mailbox.\n"
        "# The parent checks child ownership and source byte preservation.\n"
        "# Only a separate failing watchdog bounds broken cleanup.\n"
        "import os\nfrom pathlib import Path\nfrom threading import Event\n"
        f"Path({str(ready)!r}).write_text(str(os.getpid()))\nEvent().wait()\n", encoding="utf-8")
    original = producer.read_bytes()
    archive = tmp_path / "archive"
    archive.mkdir()
    configure(archive, "pst", pst_module.PstSettings(executable=sys.executable).model_dump(mode="json"))
    monkeypatch.setattr(pst_module if kind != "pff" else pff_module, "ProcessActivity", ObservedActivity)

    def extract() -> None:
        if kind == "pst-ingest":
            run_ingest(IngestRequest(archive=archive, roots=[str(producer)], scan_policy="not-scanned",
                owner_rules=OwnerRules(include=["owner@example.test"])), stop_event=cancellation, terminal=False)
        elif kind == "pff":
            list(pff_module.run_converter([sys.executable, str(producer)], tmp_path / "output", tmp_path / "stderr",
                pff_module.PffSettings(), "fixture", os.environ.copy(), cancellation=cancellation))
        else:
            registry = load_plugins(archive=archive, scan_policy="not-scanned", cancellation=cancellation)
            source = next(item.implementation for item in registry.sources if item.manifest.kind == "file-folder")
            container, = (item for item in source.discover(SourceSpec(locator=str(producer))) if isinstance(item, MailContainer))
            list(source.messages(container, None))

    with ThreadPoolExecutor(max_workers=1) as pool:
        watchdog.start()
        future = pool.submit(extract)
        try:
            wait_for_path_state([ready], lambda: ready.exists() and ready.read_text(), timeout=5)
            assert entered.wait(5), "Importer did not enter its actual activity wait"
            cancellation.set()
            with pytest.raises(IngestInterrupted if kind == "pst-ingest" else CancelledError):
                future.result(timeout=5)
            assert len(children) == 1 and children[0].poll() is not None
            assert children[0].pid == int(ready.read_text())
            assert not expired.is_set(), "Cancellation required the containment deadline"
            assert producer.read_bytes() == original
        finally:
            cancellation.set()
            for child in children:
                if child.poll() is None:
                    child.kill()
                child.wait(timeout=5)
            watchdog.cancel()
            watchdog.join(timeout=5)


@pytest.mark.parametrize("kind", ["pst", "pff"])
def test_failed_activity_initialization_kills_and_reaps_owned_importer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    """ACQUISITION: unavailable notification resources cannot bypass child ownership cleanup."""
    import mailarchiver.pff_source as pff_module
    import mailarchiver.pst_source as pst_module
    children: list[subprocess.Popen[bytes]] = []
    expired = Event()

    def contain_failure() -> None:
        expired.set()
        for child in children:
            if child.poll() is None:
                child.kill()

    watchdog = Timer(5, contain_failure)

    def unavailable(child: subprocess.Popen[bytes], _paths: object, _cancellation: object = None) -> None:
        # Only native resource acquisition is faulted; execution and cleanup remain real.
        children.append(child)
        watchdog.start()
        raise OSError("notification resource unavailable")

    monkeypatch.setattr(pff_module if kind == "pff" else pst_module, "ProcessActivity", unavailable)
    try:
        with pytest.raises(OSError, match="notification resource unavailable"):
            if kind == "pff":
                list(pff_module.run_converter([sys.executable, "-c", "from threading import Event; Event().wait()"],
                    tmp_path / "output", tmp_path / "stderr", pff_module.PffSettings(), "fixture", os.environ.copy()))
            else:
                producer = tmp_path / "blocked.pst"
                producer.write_text(
                    "# Run only in this fixture's private importer child.\n"
                    "# The host must reap it if native event initialization fails.\n"
                    "# No source archive or user mailbox is read or changed.\n"
                    "# This wait requires parent cancellation, never elapsed-time success.\n"
                    "# A separate failure watchdog contains broken cleanup during regression.\n"
                    "from threading import Event\nEvent().wait()\n", encoding="utf-8")
                archive = tmp_path / "archive"
                archive.mkdir()
                configure(archive, "pst", pst_module.PstSettings(executable=sys.executable).model_dump(mode="json"))
                registry = load_plugins(archive=archive, scan_policy="not-scanned")
                source = next(item.implementation for item in registry.sources if item.manifest.kind == "file-folder")
                container, = (item for item in source.discover(SourceSpec(locator=str(producer))) if isinstance(item, MailContainer))
                list(source.messages(container, None))
        assert len(children) == 1 and children[0].poll() is not None
        assert not expired.is_set(), "Failed ownership cleanup reached its containment deadline"
    finally:
        watchdog.cancel()
        if watchdog.ident is not None:
            watchdog.join(timeout=5)
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)


@pytest.mark.skipif(os.name == "nt", reason="POSIX process liveness uses kill(pid, 0)")
def test_host_kills_and_reaps_stuck_converter(tmp_path: Path) -> None:
    """A blocked native child cannot outlive the host's extraction deadline."""
    from mailarchiver.pff_source import PffSettings, run_converter
    output = tmp_path / "output"
    command = [sys.executable, "-c", "import os,time; print(os.getpid(), flush=True); __import__('threading').Event().wait()"]
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
    # Hosted Windows durable writes can exceed two minutes for this real 80-message cache.
    timeout = 600 if os.name == "nt" else 120
    with phase("ost-cli-acquisition"):
        result = subprocess.run([sys.executable, "-m", "mailarchiver", "--archive", str(archive), "ingest", "--no-scan",
                                 "--owner-names-file", str(owners), str(directory)],
                                capture_output=True, text=True, check=False, timeout=timeout)
    assert result.returncode != 0 and "libpff extraction incomplete" in result.stderr
    with sqlite3.connect(archive / "archive.sqlite3") as db:
        assert db.execute("SELECT count(*) FROM messages").fetchone() == (80,)
    with phase("ost-cli-content-processing"):
        cli(archive, "process", "--phase", "content", timeout=timeout)
    search = subprocess.run([sys.executable, "-m", "mailarchiver.mailsearch", "--archive", str(archive), "subject:newsletter"],
                            capture_output=True, text=True, check=False, timeout=30)
    assert search.returncode == 0 and "newsletter copy" in search.stdout
    assert not verify_archive(archive)
