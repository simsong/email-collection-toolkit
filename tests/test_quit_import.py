# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Verify Quit's message boundary for native and API acquisition.
# Both cases use the production ingest engine and canonical archive writer.
# A real processor pauses the first message before publication.
# The API fixture retrieves message bytes from a local HTTP server.
# Stop requests must finish that message without fetching the next one.
# Reimport then verifies byte preservation, recovery and deduplication.

from __future__ import annotations

import hashlib
import sqlite3
import subprocess
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread
from time import monotonic, sleep

import pytest

from mailarchiver.__main__ import IngestInterrupted, IngestRequest, run_ingest
from mailarchiver.ingest_status import read_ingest_history
from mailarchiver.owner_rules import OwnerRules
from mailarchiver.standalone_verify import verify_archive

GUIDE = """# Synthetic acquisition/processor fixture for bounded Quit tests.
# Uses only temporary files and loopback HTTP resources.
# Synchronization files expose deterministic message boundaries.
# The host retains responsibility for canonical publication and hashes.
# No source messages or external accounts are modified.
"""
PROCESSOR = '''from pathlib import Path
from time import sleep
from mailarchiver.processing.api import ProcessingResult

class Processor:
    def process(self, item):
        root = Path(__file__).parents[3]
        (root / "entered").touch()
        while not (root / "release").exists():
            item.check_cancelled()
            sleep(0.01)
        return ProcessingResult()
'''
API_SOURCE = '''from pathlib import Path
from urllib.request import urlopen
from mailarchiver.plugin_api import (
    IntegrityDecision, IntegrityEvidence, MailContainer, MailObject, PluginCapabilities, SourceReference,
)

class Controls:
    control_id = "quit-fixture-v1"
    def plan(self, container, prior):
        yield IntegrityDecision(action="read", reason="read synthetic API messages")
    def complete(self, container, planned):
        yield IntegrityEvidence(control_id=self.control_id, subject_id=container.work_id,
            evidence_kind="version-token", value="fixture-1")

class Source:
    kind = "quit-api"
    capabilities = PluginCapabilities(network_access=True, max_concurrency=1)
    integrity_controls = Controls()
    def recognizes(self, source):
        return source.locator.startswith("http://127.0.0.1:")
    def discover(self, source):
        reference = SourceReference(plugin_kind=self.kind, source_id=source.locator,
            native_id="mail", display_name="Synthetic API mail")
        yield MailContainer(work_id="mail", source=reference, concurrency_key=source.locator)
    def messages(self, container, resume_cursor):
        for number in range(2):
            (Path(__file__).parents[3] / f"requested-{number}").touch()
            with urlopen(container.source.source_id + f"/{number}.eml", timeout=5) as response:
                raw = response.read()
            yield MailObject(work_id=container.work_id, source=container.source,
                cursor=str(number), raw=raw)

def create_plugin():
    return Source()
'''


@pytest.mark.parametrize("source_kind", ["native", "api"])
@pytest.mark.parametrize("replay", [False, True])
def test_stop_finishes_current_message_before_fetching_next(tmp_path: Path, source_kind: str, replay: bool) -> None:
    """Issue #150: cooperative stop preserves the current message for both acquisition paths."""
    plugins = tmp_path / "plugins"
    processor = plugins / "processors" / "quit-boundary"
    processor.mkdir(parents=True)
    (processor / "plugin.toml").write_text('''api_version=2
plugin_type="processor"
kind="quit-boundary"
name="Quit boundary fixture"
implementation_version="1"
entrypoint="plugin:Processor"
pipeline="ingest"
rank=1
timeout_seconds=10
subscribes=["application/x-mailarchiver-raw-message"]
''')
    (processor / "plugin.py").write_text(GUIDE + PROCESSOR)
    provider = plugins / "sources" / "quit-api"
    provider.mkdir(parents=True)
    (provider / "plugin.toml").write_text('''api_version=1
plugin_type="source"
kind="quit-api"
name="Loopback API fixture"
implementation_version="1"
priority=1
entrypoint="plugin:create_plugin"
''')
    (provider / "plugin.py").write_text(GUIDE + API_SOURCE)
    messages = [
        (f"Message-ID: <quit-{number}@example.test>\nFrom: owner@example.test\n"
         "Date: Thu, 01 Oct 2026 12:00:00 +0000\nSubject: Quit fixture\n\nBody\n").encode()
        for number in range(2)
    ]
    source = tmp_path / "source.mbox"
    source.write_bytes(b"".join(b"From owner@example.test Thu Oct  1 12:00:00 2026\n" + raw + b"\n" for raw in messages))
    for number, raw in enumerate(messages):
        (tmp_path / f"{number}.eml").write_bytes(raw)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(tmp_path)))
    server_thread = Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    root = str(source) if source_kind == "native" else f"http://127.0.0.1:{server.server_port}"
    archive = tmp_path / "archive"
    request = IngestRequest(archive=archive, roots=[root], plugin_dir=[plugins], scan_policy="not-scanned",
        continue_content=False, owner_rules=OwnerRules(include=["owner@example.test"]))
    stop = Event()
    errors: list[BaseException] = []

    def ingest() -> None:
        try:
            run_ingest(request, stop_event=stop, terminal=False)
        except BaseException as error:
            errors.append(error)

    worker = Thread(target=ingest, daemon=True)
    try:
        if replay:
            # Crash with durable pending ingest work, then stop its replay while
            # the same processor is actively checking cancellation again.
            (tmp_path / "request.json").write_text(request.model_dump_json())
            with subprocess.Popen([sys.executable, "-m", "tests.quit_probe", "replay", str(tmp_path)]) as child:
                try:
                    deadline = monotonic() + 20
                    while not (tmp_path / "entered").exists() and child.poll() is None and monotonic() < deadline:
                        sleep(0.01)
                    assert (tmp_path / "entered").exists()
                finally:
                    child.kill()
                    child.wait(5)
            (tmp_path / "entered").unlink()
        worker.start()
        deadline = monotonic() + 15
        while not (tmp_path / "entered").exists() and worker.is_alive() and monotonic() < deadline:
            sleep(0.01)
        assert (tmp_path / "entered").exists(), errors
        stop.set()
        sleep(0.1)  # The processor actively checks cancellation during this interval.
        (tmp_path / "release").touch()
        if worker.ident is not None:
            worker.join(20)
        assert not worker.is_alive()
        assert len(errors) == 1 and isinstance(errors[0], IngestInterrupted), errors
        assert read_ingest_history(archive).statuses[0].state == "interrupted"
        with sqlite3.connect(archive / "archive.sqlite3") as catalog:
            assert catalog.execute("SELECT sha256 FROM messages").fetchall() == [(hashlib.sha256(messages[0]).hexdigest(),)]
            assert catalog.execute("SELECT COUNT(*) FROM source_files WHERE completed_run IS NOT NULL").fetchone() == (0,)
        if source_kind == "api":
            assert (tmp_path / "requested-0").exists()
            assert not (tmp_path / "requested-1").exists()
        assert not verify_archive(archive)
        run_ingest(request, terminal=False)
        with sqlite3.connect(archive / "archive.sqlite3") as catalog:
            assert {row[0] for row in catalog.execute("SELECT sha256 FROM messages")} == {
                hashlib.sha256(raw).hexdigest() for raw in messages}
            assert catalog.execute("SELECT COUNT(*) FROM messages").fetchone() == (2,)
        assert not verify_archive(archive)
        for number, raw in enumerate(messages):
            assert (tmp_path / f"{number}.eml").read_bytes() == raw
    finally:
        stop.set()
        (tmp_path / "release").touch()
        if worker.ident is not None:
            worker.join(20)
        server.shutdown()
        server.server_close()
        server_thread.join(5)
