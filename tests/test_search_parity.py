# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Compare Python GUI services with the compiled Rust dispatcher on real fixtures.
# The shared optimizer matrix supplies selector, attachment and folder searches.
# Additional fixtures exercise parser normalization, completion and live identities.
# Requests use the production stdin/stdout protocol without simulated replies.
# Native windows and private collections never participate in these comparisons.
# Canonical archive inventories must remain unchanged after both implementations.
"""Acceptance contract for the common Python/Rust search operations."""
from __future__ import annotations

from contextlib import closing, contextmanager
from collections.abc import Iterator
from hashlib import sha256
from os import environ
from pathlib import Path
from queue import Queue
import sqlite3
import subprocess
from threading import Thread
import time

from pydantic import BaseModel, JsonValue, TypeAdapter
import pytest
from mailarchiver.gui_service import SearchCount, SearchPage, search_count, search_page
from mailarchiver.mailsearch import MessageHeader
from mailarchiver.mailbox_tree import MailboxTreeNode, mailbox_tree
from mailarchiver.search_completion import SearchSuggestions, search_suggestions
from mailarchiver.gui_app import IdentityPickerApi
from mailarchiver.gui_processing import PickerPage, resume_request
from mailarchiver.__main__ import run_ingest
from mailarchiver.gui_provenance import ATTACHED_MESSAGES_SQL, attached_messages
from mailarchiver.processing.store import connect as processing_connect
from test_mailsearch import SearchPlanCase, make_archive, make_sparse_search_archive
from test_search_completion import completion_archive
from test_gui_service import make_gui_archive, add_tree_source
from test_gui_processing import build_deferred_archive


class Request(BaseModel):
    id: int
    method: str
    args: list[JsonValue]


class Reply(BaseModel):
    id: int
    result: JsonValue = None
    error: str | None = None


class Generation(BaseModel):
    generation: int


class Status(BaseModel):
    count: int
    window: int
    complete: bool
    error: str | None = None
    stale: bool = False


class DisplayPage(BaseModel):
    results: list[MessageHeader]


class Suggestions(SearchSuggestions):
    incomplete: bool = False


class Rpc:
    """Bounded real subprocess transport shared by these acceptance operations."""
    def __init__(self, binary: str, archive: Path) -> None:
        self.process = subprocess.Popen([binary, "--rpc", str(archive)], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                        env={**environ, "ECT_RUST_ENGINE_PYTHON": str(archive / "missing-helper")})
        self.replies: Queue[str] = Queue()
        self.number = 0
        assert self.process.stdout is not None
        stream = self.process.stdout

        def receive() -> None:
            for line in stream:
                self.replies.put(line)
        Thread(target=receive, daemon=True).start()

    def call(self, method: str, *args: JsonValue) -> JsonValue:
        self.number += 1
        assert self.process.stdin is not None
        self.process.stdin.write(Request(id=self.number, method=method, args=list(args)).model_dump_json() + "\n")
        self.process.stdin.flush()
        reply = Reply.model_validate_json(self.replies.get(timeout=30))
        assert reply.id == self.number
        if reply.error:
            raise ValueError(reply.error)
        return reply.result

    def close(self) -> None:
        assert self.process.stdin is not None
        self.process.stdin.close()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        assert self.process.returncode == 0


def inventory(archive: Path) -> list[tuple[Path, str]]:
    return [(p.relative_to(archive), sha256(p.read_bytes()).hexdigest()) for p in sorted(archive.rglob("*")) if p.is_file()]


@contextmanager
def reader(archive: Path) -> Iterator[Rpc]:
    binary = environ.get("RUST_WEBVIEW_BINARY")
    if not binary:
        pytest.skip("Run make test-search-parity to build the real Rust dispatcher")
    before = inventory(archive)
    rpc = Rpc(binary, archive)
    try:
        yield rpc
    finally:
        rpc.close()
        assert inventory(archive) == before


def complete(rpc: Rpc, case: SearchPlanCase, sort: str, direction: str) -> list[int]:
    tokens: list[JsonValue] = [s.token() for s in case.selections]
    generation = Generation.model_validate(rpc.call("search_start", case.query, sort, direction, case.attachments, tokens)).generation
    deadline = time.monotonic() + 30
    while True:
        status = Status.model_validate(rpc.call("search_status", generation))
        assert not status.error and not status.stale, status
        if status.complete:
            break
        if status.window:
            rpc.call("search_advance", generation, status.window)
        assert time.monotonic() < deadline
        time.sleep(0.001)
    result: list[int] = []
    while len(result) < status.count:
        page = DisplayPage.model_validate(rpc.call("search_page", generation, len(result), 512))
        assert page.results
        result.extend(r.message_pk for r in page.results)
    return result


def test_shared_matrix_through_real_python_and_rust_services(tmp_path: Path) -> None:
    """Every common optimizer case must agree through direct, count and worker APIs."""
    sparse_search_archive = make_sparse_search_archive(tmp_path)
    cases = TypeAdapter(list[SearchPlanCase]).validate_json((Path(__file__).parent / "fixtures/search-contract.json").read_text())
    with reader(sparse_search_archive) as rpc:
        for case in cases:
            tokens = [s.token() for s in case.selections]
            wire_tokens: list[JsonValue] = list(tokens)
            rust_count = SearchCount.model_validate(rpc.call("search_count", case.query, case.attachments, wire_tokens))
            assert rust_count == search_count(sparse_search_archive, case.query, case.attachments, tokens)
            for sort in ("date", "subject", "sender"):
                for direction in ("ascending", "descending"):
                    expected = search_page(sparse_search_archive, case.query, limit=0, sort_by=sort, direction=direction,
                                           search_attachments=case.attachments, mailbox_selections=tokens)
                    actual = SearchPage.model_validate(rpc.call("search", case.query, 0, sort, direction, case.attachments, wire_tokens, 0))
                    assert actual == expected, (case, sort, direction, actual, expected)
                    assert complete(rpc, case, sort, direction) == [r.message_pk for r in expected.results]


def test_direct_paging_counts_and_recipient_aggregation(tmp_path: Path) -> None:
    """Direct pages and threshold counts preserve limits, offsets and unique addresses."""
    archive = make_sparse_search_archive(tmp_path)
    with sqlite3.connect(archive / "archive.sqlite3") as db:
        db.execute("INSERT INTO recipients VALUES(1,2,'cc')")
    with reader(archive) as rpc:
        assert SearchCount.model_validate(rpc.call("search_count", "")) == search_count(archive, "")
        for offset, limit in [(0, 1), (1, 2), (19_999, 0)]:
            expected = search_page(archive, "", offset=offset, limit=limit, sort_by="subject", direction="ascending")
            actual = SearchPage.model_validate(rpc.call("search", "", offset, "subject", "ascending", False, [], limit))
            assert actual == expected
        actual = SearchPage.model_validate(rpc.call("search", "subject:planning", 0, "date", "descending", False, [], 0))
        assert actual == search_page(archive, "subject:planning", limit=0)


def test_parser_results_and_highlights_match(tmp_path: Path) -> None:
    """Quoting, selector normalization, date recognition and highlights are shared."""
    archive, _ = make_archive(tmp_path)
    queries = ["agenda", '"meeting agenda"', "meeting agenda", "subject:PLANNING", 'subject:" planning "',
               "subject:planning subject:planning", '""', "''", "from:sender", "any:copy", "to:copy", "cc:copy", "bcc:blind",
               "subject:planning agenda", "ticket:123", "date:2024-01-03", "date:1/3/2024", 'date:"January 3, 2024"',
               "date:2024-1-3", "date:0000-01-01", "date:2024-02-31", "from:", '"unfinished', 'subject:"say \\"hello\\""',
               "agenda agenda", 'subject:"STRASSE"', 'subject:"Straße"']
    with reader(archive) as rpc:
        for query in queries:
            expected = search_page(archive, query, limit=0)
            if expected.error:
                with pytest.raises(ValueError):
                    rpc.call("search", query)
            else:
                actual = SearchPage.model_validate(rpc.call("search", query, 0, "date", "descending", False, [], 0))
                assert actual == expected, (query, actual, expected)


def test_body_attachment_intersection_folder_trees_and_child_badges(tmp_path: Path) -> None:
    """Cross-index terms, loose-message collapse and real processed-child tags agree."""
    archive = make_gui_archive(tmp_path / "mime")
    add_tree_source(archive, '{"stable_id":"backup-1"}', "Backup 1", "Professional/Inbox/work.mbox", "mbox", [1, 2])
    add_tree_source(archive, '{"stable_id":"backup-2"}', "Backup 2", "Professional/Inbox/work.mbox", "mbox", [1])
    add_tree_source(archive, '{"stable_id":"backup-1b"}', "Backup 1", "Personal/Loose/001.eml", "message", [1])
    with reader(archive) as rpc:
        for query in ["Plain", "Appendixquartz", "Plain Appendixquartz", '"Plain version"']:
            for attachments in (False, True):
                expected = search_page(archive, query, limit=0, search_attachments=attachments)
                actual = SearchPage.model_validate(rpc.call("search", query, 0, "date", "descending", attachments, [], 0))
                assert actual == expected
        for volumes in (False, True):
            assert TypeAdapter(list[MailboxTreeNode]).validate_python(rpc.call("mailbox_tree", volumes)) == mailbox_tree(archive, volumes)
    child_root = tmp_path / "child"
    child_root.mkdir()
    archive = build_deferred_archive(child_root)
    run_ingest(resume_request(archive, False, True), terminal=False)
    with reader(archive) as rpc:
        expected = search_page(archive, "subject:GUI", limit=0)
        actual = SearchPage.model_validate(rpc.call("search", "subject:GUI", 0, "date", "descending", False, [], 0))
        assert actual == expected
        assert any(r.attached_message for r in actual.results)


def test_completion_and_live_name_edits_match(tmp_path: Path) -> None:
    """Completion counts, date values, role choices and authoritative names agree."""
    archive = completion_archive(tmp_path)
    with sqlite3.connect(archive / "archive.sqlite3") as catalog:
        catalog.execute("UPDATE messages SET subject='Straße Simson' WHERE message_pk=1")
    with reader(archive) as rpc:
        for query in ["si", "from:si", "simson", "from:simson", "to:simson", "cc:simson", "bcc:simson", "Hidden",
                      "subject:simson", "2020-01-05", "1/5/2020", "January 5, 2020", "Jan 5, 2020", "date:1/5/2020",
                      'subject:Simson from:"Simson Header"', 'subject:" Simson "', 'subject:"Simson "', "subject:' Simson'",
                      "subject:Straße", "subject:STRASSE"]:
            actual = Suggestions.model_validate(rpc.call("suggestions", query))
            assert not actual.incomplete
            assert SearchSuggestions.model_validate(actual.model_dump(exclude={"incomplete"})) == search_suggestions(archive, query), query
    picker = IdentityPickerApi(archive, "name")
    people = PickerPage.model_validate(picker.query({"mailbox": "alpha"}))
    picker.update({"operation": "rename-person", "subject": people.groups[0].id, "name": "Authoritative Person"})
    with sqlite3.connect(archive / "processing.sqlite3") as database:
        database.execute("INSERT INTO organizations(name,manual) VALUES('Parity Institution',1)")
        row = database.execute("SELECT last_insert_rowid()").fetchone()
        assert row is not None
        database.execute("INSERT INTO organization_domains(domain,organization_id) VALUES('example.test',?) "
                         "ON CONFLICT(domain) DO UPDATE SET organization_id=excluded.organization_id", (row[0],))
    with reader(archive) as rpc:
        for query in ['any:"Authoritative Person"', "from:Simson", "any:alpha", 'any:"Parity Institution"']:
            expected = search_page(archive, query, limit=0)
            actual = SearchPage.model_validate(rpc.call("search", query, 0, "date", "descending", False, [], 0))
            assert actual == expected


def test_attached_badges_are_selective_with_populated_processing(tmp_path: Path) -> None:
    """Child badges probe canonical hash indexes rather than every processor state."""
    archive = make_sparse_search_archive(tmp_path)
    with closing(sqlite3.connect(archive / "archive.sqlite3")) as catalog:
        with closing(processing_connect(archive, create=True, production=True)) as processing, processing:
            processing.executemany("INSERT INTO messages VALUES(?,?,?,'')",
                                   ((digest, normalized, digest) for normalized, digest in
                                    catalog.execute("SELECT message_id_normalized,sha256 FROM messages")))
            processing.executemany("INSERT INTO message_state(message_id,root_item_json,catalog_message_pk) VALUES(?,'{}',?)",
                                   ((digest, pk) for pk, digest in catalog.execute("SELECT message_pk,sha256 FROM messages")))
            processing.executemany("INSERT INTO message_tags VALUES(?,1)",
                                   catalog.execute("SELECT sha256 FROM messages WHERE message_pk IN(1,20001)"))
        catalog.execute("ATTACH DATABASE ? AS identities", (f"{(archive / 'processing.sqlite3').as_uri()}?mode=ro",))
        for ids in ([1, 2, 20_001], list(range(1, 501))):
            sql = ATTACHED_MESSAGES_SQL.format(placeholders=','.join('?' for _ in ids))
            plan = [row[3] for row in catalog.execute("EXPLAIN QUERY PLAN " + sql, ids)]
            for table in ("m", "s", "t", "mt"):
                assert any(step.startswith(f"SEARCH {table} ") for step in plan), plan
            steps = 0

            def budget() -> int:
                nonlocal steps
                steps += 100
                return int(steps > 100 * len(ids) + 5_000)

            catalog.set_progress_handler(budget, 100)
            try:
                assert [row[0] for row in catalog.execute(sql, ids)] == [pk for pk in ids if pk in (1, 20_001)]
            finally:
                catalog.set_progress_handler(None, 0)
            assert attached_messages(archive, ids) == {pk for pk in ids if pk in (1, 20_001)}
    with reader(archive) as rpc:
        actual = SearchPage.model_validate(rpc.call("search", "", 0, "date", "ascending", False, [], 512))
        assert actual == search_page(archive, "", limit=512, sort_by="date", direction="ascending")
        assert actual.results[0].attached_message
