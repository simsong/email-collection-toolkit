# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Requirement: Windows and macOS share the Python reader and preservation rules.
# Read a synthetic catalog/MBOX and exercise real search, rendering and export.
# Compare all collection bytes to detect accidental writes from reader operations.
# Verify the cross-platform native fixture can exercise the shipped bridge query.
# These checks do not claim Windows ingestion, signing, or native macOS acceptance.
"""Shared Python desktop reader regression checks."""
from pathlib import Path
from contextlib import closing
import hashlib
import subprocess
import sys

import pytest

from mailarchiver.application import ApplicationController, ApplicationPreferencesStore, InvalidArchiveError
from mailarchiver.gui_app import GuiApi
from mailarchiver.reader_fixture import RAW_MESSAGE, check_reader, create_fixture, inventory
from mailarchiver.mailbox_tree import FilterSet, FilterSetStore
from mailarchiver.gui_provenance import attached_messages
from mailarchiver.gui_service import search_page
from mailarchiver.processing.store import connect


def test_reader_export_and_search_preserve_collection(tmp_path: Path) -> None:
    report = check_reader(tmp_path / "acceptance")
    assert report.passed
    assert (tmp_path / "acceptance/export.eml").read_bytes() == RAW_MESSAGE


def test_native_bridge_fixture_and_independent_readers(tmp_path: Path) -> None:
    archive = tmp_path / "café collection.mailarchive"
    create_fixture(archive)
    before = inventory(archive)
    readers = [GuiApi(archive, preferences_file=tmp_path / f"filters-{i}.json") for i in range(2)]
    try:
        for api in readers:
            assert api.status()["message_count"] == 1
            result = api.search('"message viewer"', 0, "date", "descending", False, [], False)
            assert len(result["results"]) == 1
            assert "message viewer" in result["highlight_terms"]
            assert api.message(1)["subject"] == "Portable message viewer"
        assert inventory(archive) == before
    finally:
        for api in readers:
            api.close()


def test_fixture_refuses_existing_directory(tmp_path: Path) -> None:
    with pytest.raises(FileExistsError):
        create_fixture(tmp_path)


def test_complete_reader_paging_beyond_initial_limit(tmp_path: Path) -> None:
    # Requirement: search the whole collection, preserving stable sorting past 2,000.
    archive = tmp_path / "large.mailarchive"
    create_fixture(archive, messages=2107)
    before = inventory(archive)
    api = GuiApi(archive, preferences_file=tmp_path / "filters.json")
    try:
        for order in ("date", "subject", "sender"):
            first = api.search("observatory", sort_by=order, limit=2000)
            remainder = api.search("observatory", offset=2000, sort_by=order, limit=0)
            assert first["has_more"]
            assert len(first["results"]) == 2000
            ids = [row["message_pk"] for row in first["results"] + remainder["results"]]
            assert len(ids) == 2107
            assert set(ids) == set(range(1, 2108))
        assert inventory(archive) == before
    finally:
        api.close()


def test_saved_filters_survive_multiple_store_instances(tmp_path: Path) -> None:
    # Reader-only preferences remain writable outside the canonical collection.
    first = FilterSetStore(tmp_path / "filters.json")
    second = FilterSetStore(first.path)
    first.save(FilterSet(name="First"))
    second.save(FilterSet(name="Second"))
    first.rename("First", "Renamed")
    assert {item.name for item in second.read().filter_sets} == {"Renamed", "Second"}


def test_saved_filters_serialize_competing_processes(tmp_path: Path) -> None:
    # Requirement: separate desktop windows/processes cannot overwrite saved sets.
    path = tmp_path / "filters.json"
    code = ("from pathlib import Path; import sys; from mailarchiver.mailbox_tree import FilterSet,FilterSetStore; "
            "store=FilterSetStore(Path(sys.argv[1])); "
            "[store.save(FilterSet(name=sys.argv[2]+str(i))) for i in range(20)]")
    processes = [subprocess.Popen([sys.executable, "-c", code, str(path), prefix]) for prefix in ("A", "B")]
    try:
        for process in processes:
            assert process.wait(timeout=30) == 0
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait()
    assert len(FilterSetStore(path).read().filter_sets) == 40


def test_existing_processing_names_and_attachment_tags_are_read_only(tmp_path: Path) -> None:
    # Requirement: reuse canonical-hash tag joins and live organization-name search.
    archive = tmp_path / "processed #100% café.mailarchive"
    create_fixture(archive)
    digest = hashlib.sha256(RAW_MESSAGE).hexdigest()
    with closing(connect(archive, create=True, production=True)) as database:
        database.execute("INSERT INTO messages VALUES (?,?,?,?)", (digest, "reader-check@example.invalid", digest, "fixture"))
        database.execute("INSERT INTO message_state(message_id,root_item_json,catalog_message_pk) VALUES (?,'{}',1)", (digest,))
        database.execute("INSERT OR IGNORE INTO tags(name) VALUES ('attachment')")
        database.execute("INSERT INTO message_tags SELECT ?,tagid FROM tags WHERE name='attachment'", (digest,))
        database.execute("INSERT INTO addresses VALUES (1,'reader@example.invalid','reader','example.invalid')")
        database.execute("INSERT INTO organizations VALUES (1,'Observatory Institute',0)")
        database.execute("INSERT INTO organization_domains VALUES ('example.invalid',1,0)")
        database.commit()
    before = inventory(archive)
    assert attached_messages(archive, [1]) == {1}
    assert [item.message_pk for item in search_page(archive, 'from:"Observatory Institute"').results] == [1]
    assert inventory(archive) == before


@pytest.mark.parametrize("name", ["café collection.mailarchive", "legacy collection"])
def test_open_archive_member_resolves_root_without_writes(tmp_path: Path, name: str) -> None:
    # Requirement: selecting any file inside an archive opens the containing
    # document, including nested MBOX files, without changing collection bytes.
    archive = tmp_path / name
    create_fixture(archive)
    before = inventory(archive)
    preferences = ApplicationPreferencesStore(tmp_path / "preferences.json")
    controller = ApplicationController(preferences)
    document = controller.open_document(archive)
    for member in archive.rglob("*"):
        assert controller.open_document(member) is document
    assert controller.active_document is None
    assert inventory(archive) == before
    assert preferences.read().recent_archives == [archive]


def test_open_unrelated_file_and_damaged_inner_archive(tmp_path: Path) -> None:
    # Requirement: invalid selections fail clearly; do not open a different
    # archive when the nearest enclosing package has missing databases.
    controller = ApplicationController(ApplicationPreferencesStore(tmp_path / "preferences.json"))
    unrelated = tmp_path / "unrelated.txt"
    unrelated.write_bytes(b"preserve me")
    with pytest.raises(InvalidArchiveError, match="not inside a mail archive"):
        controller.open_document(unrelated)
    archive = tmp_path / "outer.mailarchive"
    create_fixture(archive)
    inner = archive / "damaged.mailarchive"
    inner.mkdir()
    member = inner / "message.eml"
    member.write_bytes(RAW_MESSAGE)
    before = inventory(archive)
    with pytest.raises(InvalidArchiveError, match="archive is missing"):
        controller.open_document(member)
    with pytest.raises(InvalidArchiveError, match="does not exist"):
        controller.open_document(inner / "missing.eml")
    assert inventory(archive) == before
    assert unrelated.read_bytes() == b"preserve me"
