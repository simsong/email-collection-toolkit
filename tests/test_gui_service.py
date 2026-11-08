# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Verify GUI services search, sanitize, preview, and export without archive writes."""

from __future__ import annotations

import base64
import codecs
import hashlib
import json
import sqlite3
import sys
import time
import zipfile
from importlib.metadata import version
from webview.menu import MenuAction, MenuSeparator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event

import pytest
from pydantic import ValidationError

from mailarchiver.application import ApplicationController, ApplicationPreferencesStore
from mailarchiver.bagit import initialize_bag
from mailarchiver.catalog import address_pk, create_catalog, create_search
from mailarchiver.mailsearch import read_message_bytes
from mailarchiver.configuration import application_configuration, load_configuration
from mailarchiver.gui_app import (
    GuiApi,
    PyWebViewApplication,
    application_icon_path,
    application_menu,
    application_metadata,
    external_link_destination,
    license_html,
)
from mailarchiver.gui_service import (
    LEGACY_X_HTML_PART_ID,
    RAW_PART_ID,
    attachment_content,
    attachment_descriptor,
    describe_message,
    is_risky,
    safe_filename,
    message_previews,
    render_part,
    search_count,
    search_page,
    search_suggestions,
    searchable_message_count,
    write_attachment,
    write_message,
    write_messages_zip,
)
from mailarchiver.gui_app import (
    archive_destination,
    dialog_paths,
    owner_rules_text,
    DocumentOptionsApi,
)
from mailarchiver.document_options import DocumentOptions, read_owner_names, source_owner_names
from mailarchiver.owner_rules import OwnerRules
from mailarchiver.archive_config import (
    ArchiveConfig, config_path, import_directory, load_archive_config,
    remember_import_directory, save_archive_config,
)
from mailarchiver.writer_lock import ArchiveBusyError, WriterLease
from mailarchiver.mailsearch import RECENT_FTS_SCAN_LIMIT, _search_statement, parse_query
from mailarchiver.layout import mbox_directory
from mailarchiver.mailbox_tree import (
    FilterSet,
    FilterSetStore,
    MailboxSelection,
    MailboxTreeNode,
    mailbox_tree,
)
from mailarchiver.mbox import PreservingMbox, add_message
from mailarchiver.plugin_api import SourceContainerMetadata, SourceRelationship
from mailarchiver.search import index_message
from mailarchiver.standalone_verify import semantic_bytes


def test_owner_rules_defaults_and_document_edits(tmp_path: Path) -> None:
    """Owner rules requirement: YAML replaces legacy defaults without changing sources."""
    archive = tmp_path / "archive"
    archive.mkdir()
    source = tmp_path / "source"
    source.mkdir()
    names = source / "owner-names.txt"
    original = "  José  \r\n\n jose@example.org \n# ignored\n"
    names.write_bytes(original.encode())
    assert source_owner_names([source / "message.eml"]) == []
    assert source_owner_names([source, source]) == ["jose@example.org", "josé"]
    store = DocumentOptions(archive)
    assert store.defaults([source]).include == ["jose@example.org", "josé"]
    lease = WriterLease.acquire(archive, "fixture", "test", "test", "test")
    try:
        rules = OwnerRules(include=["*simson*", "slg"], exclude=["*david*"])
        state = store.save(rules, lease)
        assert store.defaults([source]) == rules
        original_config = config_path(archive).stat().st_mtime_ns
        store.save(rules, lease)
        assert config_path(archive).stat().st_mtime_ns == original_config
        store.record_import(rules, lease)
        assert not store.state().changed_since_import
        changed = store.save(OwnerRules(include=["slg"]), lease, state.revision)
        assert changed.changed_since_import
        with pytest.raises(ValueError, match="another window"):
            store.save(rules, lease, state.revision)
        assert DocumentOptions(archive).state().changed_since_import
        store.record_import(store.defaults(), lease)
        assert not store.state().changed_since_import
        # An intentionally empty saved list must not resurrect source defaults.
        store.save(OwnerRules(), lease)
        assert store.defaults([source]) == OwnerRules()
    finally:
        lease.release()
    with pytest.raises(ValueError, match="active writer lease"):
        store.save(OwnerRules(), lease)
    assert names.read_bytes() == original.encode()


def test_archive_config_remembers_last_import_directory(tmp_path: Path) -> None:
    """The next import starts at the prior source directory, not the home directory."""
    archive = tmp_path / "archive"
    archive.mkdir()
    source = tmp_path / "mail" / "nested"
    source.mkdir(parents=True)
    message = source / "mail.eml"
    message.write_text("From: test@example.org\n\nbody\n", encoding="utf-8")
    assert import_directory(archive) == archive.parent
    assert remember_import_directory(archive, [source]) == source.absolute()
    assert load_archive_config(archive).last_import_directory == source.absolute()
    assert import_directory(archive) == source
    assert config_path(archive).read_text(encoding="utf-8").startswith("version: 2\n")
    save_archive_config(archive, ArchiveConfig(last_import_directory=message))
    assert import_directory(archive) == archive.parent
    config_path(archive).write_text("version: 1\n", encoding="utf-8")
    rules = OwnerRules(include=["slg"], exclude=["*david*"])
    config = load_archive_config(archive)
    config.owner = rules
    save_archive_config(archive, config)
    remember_import_directory(archive, [source])
    assert load_archive_config(archive).owner == rules
    broken = "last_import_directory: [broken\n"
    config_path(archive).write_text(broken, encoding="utf-8")
    with pytest.raises(ValueError, match="invalid archive configuration"):
        remember_import_directory(archive, [source])
    assert config_path(archive).read_text(encoding="utf-8") == broken
    with pytest.raises(ValueError, match="invalid archive configuration"):
        load_archive_config(archive)


def test_document_owner_controls_are_bound_and_locked(tmp_path: Path) -> None:
    """Options split, sort, deduplicate and delete only in their bound document."""
    controller = ApplicationController(ApplicationPreferencesStore(tmp_path / "preferences.json"))
    document = controller.create_document(tmp_path / "one.mailarchive")
    assert document.path is not None
    other = controller.create_document(tmp_path / "two.mailarchive")
    assert other.path is not None
    api = DocumentOptionsApi(document)
    state = api.status()
    updated = api.update("*Simson*, SLG\nslg", "*David*", state["revision"])
    assert updated["include"] == ["*simson*", "slg"]
    assert updated["exclude"] == ["*david*"]
    lease = WriterLease.acquire(document.path, document.descriptor.identity, "ingest", "test", "test")
    try:
        with pytest.raises(ArchiveBusyError):
            api.update("wrong", "", updated["revision"])
    finally:
        lease.release()
    deleted = api.update("slg", "", updated["revision"])
    assert deleted["include"] == ["slg"]
    assert deleted["exclude"] == []
    with pytest.raises(ValueError, match="another window"):
        api.update("wrong", "", updated["revision"])
    assert read_owner_names(other.path / "owner-names.txt") == []
    assert not config_path(other.path).exists()
    assert api.update("", "", deleted["revision"])["include"] == []


@pytest.mark.parametrize("value", ["", " \n\t", "# only comments", "Name\x00"])
def test_owner_names_reject_invalid_entry_without_writes(tmp_path: Path, value: str) -> None:
    """The owner editor must not save empty or invalid aliases."""
    with pytest.raises(ValueError):
        owner_rules_text(value, "")
    assert not (tmp_path / "owner-names.txt").exists()


def test_invalid_owner_names_are_not_silently_replaced(tmp_path: Path) -> None:
    """Existing invalid settings produce errors, not guessed aliases or replacement."""
    archive = tmp_path / "archive"
    archive.mkdir()
    path = archive / "owner-names.txt"
    for content in (b"bad\x00name", b"\xff"):
        path.write_bytes(content)
        with pytest.raises(ValueError):
            DocumentOptions(archive).state()
        assert path.read_bytes() == content

@pytest.mark.parametrize("as_sequence", [False, True])
def test_native_save_default_archive_path(tmp_path: Path, as_sequence: bool) -> None:
    """New must accept native SAVE strings and sequences without truncating paths."""
    selected = str(tmp_path / "Untitled")
    destination = archive_destination((selected,) if as_sequence else selected)
    controller = ApplicationController(ApplicationPreferencesStore(tmp_path / "preferences.json"))
    document = controller.create_document(destination)
    assert document.path is not None
    assert document.path == tmp_path / "Untitled.mailarchive"
    assert (document.path / "archive.sqlite3").is_file()
    assert (document.path / "search.sqlite3").is_file()


def test_native_dialog_paths_preserve_unicode_and_reject_root() -> None:
    """Native paths remain intact; invalid root destinations fail before archive writes."""
    selected = "/Users/example/資料/Untitled.mailarchive"
    assert dialog_paths(selected) == (Path(selected),)
    assert archive_destination([selected]) == Path(selected)
    with pytest.raises(ValueError, match="filesystem root"):
        archive_destination("/")


SIMPLE_MESSAGE = (
    b"Message-ID: <simple@example>\nFrom: sender@example.net\nTo: recipient@example.net\n"
    b"Subject: annual plan\nDate: Wed, 03 Jan 2024 10:00:00 +0000\n\nThe report is ready.\n"
)
MULTIPART_MESSAGE = (
    b"Message-ID: <multipart@example>\nFrom: sender@example.net\nTo: recipient@example.net\n"
    b"Subject: multipart message\nDate: Thu, 04 Jan 2024 10:00:00 +0000\n"
    b"Content-Type: multipart/mixed; boundary=outer\n\n"
    b"--outer\nContent-Type: multipart/alternative; boundary=alternative\n\n"
    b"--alternative\nContent-Type: text/plain; charset=utf-8\n\nPlain version.\n"
    b"--alternative\nContent-Type: text/html; charset=utf-8\n\n"
    b'<html><body onload="bad()"><script>bad()</script><p>HTML version.</p><a href="https://example.org/archive" target="_blank">Archive</a>'
    b'<img src="cid:logo"><img src="https://tracker.example/pixel"></body></html>\n'
    b"--alternative--\n"
    b"--outer\nContent-Type: image/png\nContent-ID: <logo>\nContent-Disposition: inline; filename=logo.png\n"
    b"Content-Transfer-Encoding: base64\n\naW1hZ2U=\n"
    b"--outer\nContent-Type: application/pdf\nContent-Disposition: attachment; filename=../report.pdf\n"
    b"Content-Transfer-Encoding: base64\n\nJVBERi0xLjQK\n"
    b"--outer\nContent-Type: text/plain; charset=utf-8\nContent-Disposition: attachment; filename=notes.txt\n\n"
    b"Appendixquartz appears only in this attachment.\n"
    b"--outer--\n"
)
LEGACY_X_HTML_MESSAGE = (
    b"Message-ID: <legacy-x-html@example>\nFrom: sender@example.net\nTo: recipient@example.net\n"
    b"Subject: legacy x-html\nDate: Fri, 05 Jan 2024 10:00:00 +0000\nMIME-Version: 1.0\n"
    b"Content-Type: multipart/alternative; boundary=missing\n\n"
    b'<x-html><HTML><BODY onload="bad()"><script>bad()</script><p>Legacy HTML version.</p>'
    b'<img src="https://tracker.example/pixel"></BODY></HTML></x-html>\n'
)
X_HTML_MENTION_MESSAGE = (
    b"Message-ID: <x-html-mention@example>\nFrom: sender@example.net\nTo: recipient@example.net\n"
    b"Subject: x-html mention\nDate: Sat, 06 Jan 2024 10:00:00 +0000\n\n"
    b"This plain text mentions <x-html> without wrapping the whole body.\n"
)
KOREAN_MESSAGE = (
    b"Message-ID: <legacy-korean@example>\n"
    + b"From: " + "장나나".encode("euc-kr") + b" <jjanana@hanmail.net>\n"
    + b"To: simsong@example.net\n"
    + b"Subject: " + "이렇게 하면 부자가 됩니다".encode("euc-kr") + b"\n"
    + b"Date: Sat, 17 May 2003 15:23:19 +0900\n"
    + b"Content-Type: text/html; charset=euc-kr\n\n"
    + b"<html><body><p>" + "최고의 기회".encode("euc-kr") + b"</p></body></html>\n"
)
MULTI_HTML_MESSAGE = (
    b"Message-ID: <multi-html@example>\nFrom: sender@example.net\nTo: recipient@example.net\n"
    b"Subject: multi HTML message\nDate: Sun, 07 Jan 2024 10:00:00 +0000\n"
    b"Content-Type: multipart/mixed; boundary=html\n\n"
    b"--html\nContent-Type: text/html\n\n<p>Brief notice.</p>\n"
    b"--html\nContent-Type: text/html\n\n"
    b"<html><body><h1>Complete web report</h1><p>The complete report is here.</p></body></html>\n"
    b"--html--\n"
)


def make_gui_archive(
    tmp_path: Path,
    extra_messages: tuple[tuple[bytes, str, str, str], ...] = (),
) -> Path:
    archive = tmp_path / "archive"
    initialize_bag(archive)
    catalog = create_catalog(archive / "archive.sqlite3")
    search = create_search(archive / "search.sqlite3")
    message_pks: list[int] = []
    try:
        sender = address_pk(catalog, "sender@example.net")
        recipient = address_pk(catalog, "recipient@example.net")
        records = (
            (SIMPLE_MESSAGE, "simple@example", "annual plan", "2024-01-03T10:00:00+00:00"),
            (MULTIPART_MESSAGE, "multipart@example", "multipart message", "2024-01-04T10:00:00+00:00"),
            *extra_messages,
        )
        for raw, message_id, subject, timestamp in records:
            cursor = catalog.execute(
                "INSERT INTO messages(message_id_normalized, sha256, sender_address_pk, subject, date_utc, date_source, category) "
                "VALUES (?, ?, ?, ?, ?, 'date', 'Archive')",
                (message_id, hashlib.sha256(raw).hexdigest(), sender, subject, timestamp),
            )
            assert cursor.lastrowid is not None
            message_pks.append(int(cursor.lastrowid))
            catalog.execute(
                "INSERT INTO recipients(message_pk, address_pk, role) VALUES (?, ?, 'to')",
                (cursor.lastrowid, recipient),
            )
            index_message(search, raw, True)
        catalog.commit()
        search.commit()
    finally:
        catalog.close()
        search.close()
    path = mbox_directory(archive) / "2024-Archive1.mbox"
    # Publish fixture bytes through the same newline-preserving archive writer.
    box = PreservingMbox(path)
    try:
        locations = [add_message(box, path, raw) for raw, _, _, _ in records]
    finally:
        box.close()
    catalog = create_catalog(archive / "archive.sqlite3")
    try:
        generation = catalog.execute(
            "INSERT INTO mbox_generations(filename, sha256, message_count, byte_count) VALUES (?, '', 0, 0) "
            "RETURNING generation_pk",
            (path.name,),
        ).fetchone()
        assert generation is not None
        catalog.executemany(
            "INSERT INTO locations(message_pk, generation_pk, byte_offset, byte_length) VALUES (?, ?, ?, ?)",
            ((message_pk, generation[0], location.byte_offset, location.byte_length)
             for message_pk, location in zip(message_pks, locations)),
        )
        run = catalog.execute("INSERT INTO ingest_runs(started_at) VALUES (?)", ("2026-08-23T00:00:00+00:00",)).lastrowid
        volume = catalog.execute(
            "INSERT INTO source_volumes(identity_json, metadata_json, first_observed_at, last_observed_at) VALUES (?, ?, ?, ?)",
            (
                json.dumps({"kind": "local-volume", "stable_id": "fixture"}),
                json.dumps({"volume_label": "Fixture Backup", "current_mount_path": "/Volumes/Fixture"}),
                "2026-08-23T00:00:00+00:00", "2026-08-23T00:00:00+00:00",
            ),
        ).lastrowid
        source_file = catalog.execute(
            "INSERT INTO source_files(source_volume_pk, source_path, hierarchy_path, path_kind, source_kind) "
            "VALUES (?, 'mail/simple.eml', 'mail/simple.eml', 'file', 'message')",
            (volume,),
        ).lastrowid
        catalog.execute(
            "INSERT INTO observations(run_pk, message_pk, source_file_pk, source_offset, raw_sha256, semantic_sha256, disposition, detail) "
            "VALUES (?, ?, ?, 0, ?, ?, 'archived', 'Archive')",
            (run, message_pks[0], source_file, hashlib.sha256(SIMPLE_MESSAGE).hexdigest(),
             hashlib.sha256(semantic_bytes(SIMPLE_MESSAGE)).hexdigest()),
        )
        catalog.commit()
    finally:
        catalog.close()
    return archive


def test_gui_search_field_preserves_selector_and_quote_semantics(tmp_path: Path) -> None:
    """Requirement: selectors and quoted phrases can coexist in one GUI search field."""
    archive = make_gui_archive(tmp_path)

    assert [result.subject for result in search_page(archive, "subject:annual report").results] == ["annual plan"]
    assert search_page(archive, 'subject:"annual report"').results == []
    page = search_page(archive, 'from:sender subject:"annual plan" report REPORT')
    assert [result.subject for result in page.results] == ["annual plan"]
    assert page.highlight_terms == ["report", "sender", "annual plan"]


def test_gui_highlight_configuration_is_packaged_and_rejects_css_injection(tmp_path: Path) -> None:
    """Requirement: the viewer highlight color comes from strictly validated YAML."""
    assert application_configuration().gui.search_highlight_background == "#fff59d"
    invalid = tmp_path / "configuration.yaml"
    invalid.write_text(
        "version: 1\nmode: replace\ngui:\n  search_highlight_background: 'yellow; } body { display: none'\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="gui.search_highlight_background"):
        load_configuration(invalid)


def test_gui_count_and_search_cover_the_complete_archive_time_span(tmp_path: Path) -> None:
    """Requirement: an archive query counts and returns matches older than the recent catalog window."""
    archive = make_gui_archive(tmp_path)
    catalog = create_catalog(archive / "archive.sqlite3")
    try:
        sender = address_pk(catalog, "recent@example.net")
        catalog.executemany(
            "INSERT INTO messages(message_id_normalized, sha256, sender_address_pk, subject, date_utc, "
            "date_source, category) VALUES (?, ?, ?, '', '2025-01-01T00:00:00+00:00', 'date', 'Archive')",
            (
                (f"recent-{number}@example", hashlib.sha256(f"recent-{number}".encode()).hexdigest(), sender)
                for number in range(RECENT_FTS_SCAN_LIMIT)
            ),
        )
        catalog.commit()
    finally:
        catalog.close()

    summary = search_count(archive, "report")
    results = search_page(archive, "report")

    assert summary.total == 1
    assert summary.immediate_limit == 2_000
    assert [result.message_pk for result in results.results] == [1]
    assert not results.has_more


@pytest.mark.parametrize("query", ["sender", "To:recipient", "To:recipient sender"])
def test_gui_unlimited_remainder_continues_the_same_sorted_result_set(tmp_path: Path, query: str) -> None:
    """Requirement: a background continuation starts after the immediate result prefix without duplicates."""
    archive = make_gui_archive(tmp_path)

    summary = search_count(archive, query, immediate_limit=1)
    first = search_page(
        archive, query, limit=1, sort_by="subject", direction="ascending",
    )
    remainder = search_page(
        archive, query, offset=1, limit=0, sort_by="subject", direction="ascending"
    )

    assert summary.total is None
    assert summary.immediate_limit == 1
    assert [result.message_pk for result in first.results] == [1]
    assert first.has_more
    assert [result.message_pk for result in remainder.results] == [2]
    assert not remainder.has_more


def test_gui_application_metadata_names_the_product() -> None:
    """Requirement: native menus and the About panel identify Email Collection Toolkit, not Python."""
    metadata = application_metadata()

    assert metadata.name == "Email Collection Toolkit"
    assert metadata.version == version("mailarchiver")
    assert metadata.copyright == "Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved."


def test_gui_application_uses_the_source_controlled_rainbow_icon() -> None:
    """Requirement: the Python application uses the same named icon as the site."""
    icon = application_icon_path()

    assert icon.name == "rainbow-post-192.png"
    assert icon.is_file()


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_native_menus_route_through_the_application_controller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str,
) -> None:
    """Requirement: native menu callbacks resolve active state instead of capturing one GuiApi."""
    controller = ApplicationController(ApplicationPreferencesStore(tmp_path / "preferences.json"))
    application = PyWebViewApplication(controller)
    monkeypatch.setattr(sys, "platform", platform)

    menus = application_menu(application)

    assert [menu.title for menu in menus] == ["File", "Window", "Help"]
    assert all(isinstance(item, (MenuAction, MenuSeparator)) for menu in menus for item in menu.items)
    assert [item.title for item in menus[0].items if isinstance(item, MenuAction)] == [
        "New", "Open…", "Import…", "Reprocess Saved Mail…", "Document Options…", "Customizable Default Lists…",
    ] + (["Show Archive in Explorer"] if sys.platform == "win32" else []) + ["Close"] + (
        ["Quit"] if sys.platform == "win32" else [])
    if sys.platform == "win32":
        assert [item.title for item in menus[2].items if isinstance(item, MenuAction)] == [
            "Community Forum", "Contact Support", "User Manual", "Source Code", "License",
            "About", "Check for Program Updates", "Check for Anti-Virus Updates", "Preferences…"]
        antivirus_action = menus[2].items[-2]
        assert isinstance(antivirus_action, MenuAction)
        assert antivirus_action.function == application.check_antivirus_updates
    assert [item.title for item in menus[1].items if isinstance(item, MenuAction)] == ["New Search Window", "Ingests"]
    for action in menus[1].items:
        assert isinstance(action, MenuAction)
        assert not action.function()


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_help_actions_open_the_requested_destinations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str,
) -> None:
    """Requirement: every Help action dispatches its own destination on both desktops."""
    application = PyWebViewApplication(ApplicationController(
        ApplicationPreferencesStore(tmp_path / "preferences.json")))
    opened: list[str] = []
    # Replace only the OS boundaries to avoid launching browsers/native windows in unit tests.
    monkeypatch.setattr("mailarchiver.gui_app.open_external_url", opened.append)
    monkeypatch.setattr(application, "show_license", lambda: opened.append("embedded-license"))
    monkeypatch.setattr(sys, "platform", platform)
    actions = application_menu(application)[2].items[:5]
    assert [action.title for action in actions if isinstance(action, MenuAction)] == [
        "Community Forum", "Contact Support", "User Manual", "Source Code", "License",
    ]
    for action in actions:
        assert isinstance(action, MenuAction)
        action.function()
    assert opened == [
        "https://github.com/simsong/email-collection-toolkit/discussions",
        "https://github.com/simsong/email-collection-toolkit/issues",
        "https://simsong.github.io/email-collection-toolkit/manual/",
        "https://github.com/simsong/email-collection-toolkit",
        "embedded-license",
    ]


def test_license_view_preserves_the_complete_local_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement: License works offline from source and packaged GUI resources."""
    from bs4 import BeautifulSoup  # pylint: disable=import-outside-toplevel

    original = (Path(__file__).parents[1] / "LICENSE").read_text(encoding="utf-8")
    pre = BeautifulSoup(license_html(), "html.parser").pre
    assert pre is not None and pre.get_text() == original
    assets = tmp_path / "gui"
    assets.mkdir()
    (assets / "LICENSE").write_text(original, encoding="utf-8")
    monkeypatch.setattr("mailarchiver.gui_app.GUI_DIRECTORY", assets)
    page = BeautifulSoup(license_html(), "html.parser")
    assert page.pre is not None and page.pre.get_text() == original
    assert not page.find_all(["script", "link", "iframe"])


def test_gui_api_records_independent_search_window_state(tmp_path: Path) -> None:
    """Requirement: each GUI bridge records search and selection state on its search window."""
    archive = make_gui_archive(tmp_path)
    controller = ApplicationController(ApplicationPreferencesStore(tmp_path / "preferences.json"))
    document = controller.open_document(archive)
    assert document.path is not None
    session = controller.new_search_window(document)
    api = GuiApi(archive, document=document, search_window=session)
    try:
        page = api.search("report", sort_by="subject", direction="ascending")
        api.message(page["results"][0]["message_pk"])
        assert api.search("from:")["error"] == "from: requires a value"
    finally:
        api.close()

    assert session.query == "report"
    assert session.sort_by == "subject"
    assert session.sort_direction == "ascending"
    assert session.selected_message == 1


def test_gui_copy_source_path_reports_missing_native_bridge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Requirement: copying provenance reports a missing native clipboard bridge."""
    archive = make_gui_archive(tmp_path)
    api = GuiApi(archive)
    monkeypatch.setitem(sys.modules, "AppKit", None)
    try:
        expected = "requires an open reader window" if sys.platform == "win32" else "requires macOS with PyObjC installed"
        with pytest.raises(ValueError, match=expected):
            api.copy_source_path(1, 0)
    finally:
        api.close()


def test_gui_search_sorting_is_whitelisted_and_stable(tmp_path: Path) -> None:
    """Requirement: GUI results sort by date, subject, or sender in either direction."""
    archive = make_gui_archive(tmp_path)

    ascending = search_page(archive, "", sort_by="subject", direction="ascending")
    descending = search_page(archive, "", sort_by="subject", direction="descending")

    assert [result.subject for result in ascending.results] == ["annual plan", "multipart message"]
    assert [result.subject for result in descending.results] == ["multipart message", "annual plan"]
    with pytest.raises(ValueError):
        search_page(archive, "", sort_by="subject; DROP TABLE messages")


def test_gui_search_results_include_indexed_attachment_counts(tmp_path: Path) -> None:
    """Requirement: result rows receive attachment counts without rereading canonical MBOX data."""
    archive = make_gui_archive(tmp_path)

    results = search_page(archive, "", sort_by="date", direction="ascending").results

    assert [(result.message_pk, result.attachment_count) for result in results] == [(1, 0), (2, 3)]


def test_gui_attachment_checkbox_expands_full_text_search(tmp_path: Path) -> None:
    """Requirement: attachment terms affect GUI results only when attachment search is selected."""
    archive = make_gui_archive(tmp_path)

    assert search_page(archive, "Appendixquartz").results == []
    assert [result.message_pk for result in search_page(archive, "Appendixquartz", search_attachments=True).results] == [2]
    assert [result.message_pk for result in search_page(archive, "Plain Appendixquartz", search_attachments=True).results] == [2]


def test_gui_suggestions_use_trigram_substrings_and_deduplicated_message_counts(tmp_path: Path) -> None:
    """Requirement: partial names, addresses, and subject substrings produce ranked completions."""
    archive = make_gui_archive(tmp_path)
    search = create_search(archive / "search.sqlite3")
    catalog = create_catalog(archive / "archive.sqlite3")
    raw_messages: list[bytes] = []
    try:
        sender = address_pk(catalog, "beth@example.org")
        for number, subject in enumerate(("Flight for ELISABETH", "Ordinary subject"), 1):
            raw = "".join((
                (f"Message-ID: <suggestion-{number}@example>\n"
                "From: Beth Rosenberg <beth@example.org>\n"),
                "Cc: Beth Rosenberg <beth@example.org>\n" if number == 1 else "",
                f"Subject: {subject}\n\nbody\n",
            )).encode()
            raw_messages.append(raw)
            index_message(search, raw, False, date_utc=f"2024-01-0{number}T00:00:00+00:00")
            catalog.execute(
                "INSERT INTO messages(message_id_normalized, sha256, sender_address_pk, subject, date_utc, "
                "date_source, category) VALUES (?, ?, ?, ?, '2024-01-01T00:00:00+00:00', 'date', 'Archive')",
                (f"suggestion-{number}@example", hashlib.sha256(raw).hexdigest(), sender, subject),
            )
        index_message(search, raw_messages[0], False, date_utc="2024-01-01T00:00:00+00:00")
        search.commit()
        catalog.commit()
    finally:
        search.close()
        catalog.close()

    suggestions = search_suggestions(archive, "beth")

    assert [(item.value, item.tag, item.message_count) for item in suggestions.items if item.tag != "subject"] == [
        ("beth", "from", 2), ("beth@example.org", "from", 2)
    ]
    assert search_suggestions(archive, "Rosenberg").items[0].value == "Rosenberg"
    assert [(item.value, item.message_count) for item in suggestions.items if item.tag == "subject"] == [
        ("beth", 1), ("Flight for ELISABETH", 1)
    ]
    assert search_suggestions(archive, "be").items == []
    assert searchable_message_count(archive) == 4


def test_gui_address_suggestions_break_frequency_ties_by_recency(tmp_path: Path) -> None:
    """Requirement: equally frequent address completions prefer the most recently seen address."""
    archive = make_gui_archive(tmp_path)
    search = create_search(archive / "search.sqlite3")
    try:
        index_message(
            search, b"From: Older Match <older-match@example.org>\n\nbody", False,
            date_utc="2024-01-01T00:00:00+00:00",
        )
        index_message(
            search, b"From: Newer Match <newer-match@example.org>\n\nbody", False,
            date_utc="2025-01-01T00:00:00+00:00",
        )
        search.commit()
    finally:
        search.close()

    with create_catalog(archive / "archive.sqlite3") as catalog:
        for number, (address, seen) in enumerate((("older-match@example.org", "2024-01-01"),
                                                  ("newer-match@example.org", "2025-01-01"))):
            sender = address_pk(catalog, address)
            catalog.execute("INSERT INTO messages(message_id_normalized,sha256,sender_address_pk,subject,date_utc,date_source,category) "
                            "VALUES(?,?,?,'',?,'date','Archive')", (f"rank-{number}", f"rank-{number}", sender, seen))
    assert [item.value for item in search_suggestions(archive, "match").items if item.tag == "from"] == [
        "match", "newer-match@example.org", "older-match@example.org",
    ]


def test_gui_loads_indexed_previews_on_its_background_worker(tmp_path: Path) -> None:
    """Requirement: result metadata is independent from asynchronously loaded body previews."""
    archive = make_gui_archive(tmp_path)
    assert [(item.message_pk, item.preview) for item in message_previews(archive, [1, 2])] == [
        (1, "The report is ready."),
        (2, "Plain version."),
    ]
    api = GuiApi(archive)
    try:
        assert api.request_previews([1, 2])
        batch = api.take_previews([1, 2], True)
    finally:
        api.close()

    assert not batch["pending"]
    assert batch["error"] is None
    assert [(item["message_pk"], item["preview"]) for item in batch["previews"]] == [
        (1, "The report is ready."),
        (2, "Plain version."),
    ]


@pytest.mark.parametrize("kind", ["suggestions", "previews"])
def test_result_wait_deadline_reports_failure_and_explicit_retry_can_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    """DESKTOP: a failed wait remains a typed error, not a timer tick; actual work stays retryable."""
    import mailarchiver.gui_app as gui_module
    from mailarchiver.gui_app import SuggestionStatus
    from mailarchiver.gui_service import PreviewBatch
    monkeypatch.setattr(gui_module, "WORK_RESULT_TIMEOUT_SECONDS", 0)
    api = GuiApi(make_gui_archive(tmp_path))
    executor = api._suggestion_executor if kind == "suggestions" else api._preview_executor
    occupied, release = Barrier(executor._max_workers + 1), Event()

    def hold() -> None:
        occupied.wait(timeout=5)
        assert release.wait(5), "Queued-work fixture was not released"

    blockers = [executor.submit(hold) for _ in range(executor._max_workers)]
    request: SuggestionStatus | None = None
    try:
        occupied.wait(timeout=5)
        if kind == "suggestions":
            request = SuggestionStatus.model_validate(api.suggestions_start("sender"))
            future = api._suggestion_future
            response = SuggestionStatus.model_validate(api.suggestions_status(request.generation, True))
            assert not response.complete and response.error and "Timed out" in response.error
        else:
            api.request_previews([1])
            future = next(iter(api._preview_futures))
            batch = PreviewBatch.model_validate(api.take_previews([1], True))
            assert batch.pending and batch.error and "Timed out" in batch.error
        assert future is not None and not future.done()
        release.set()
        for blocker in blockers:
            blocker.result(timeout=5)
        future.result(timeout=5)
        if kind == "suggestions":
            assert request is not None
            response = SuggestionStatus.model_validate(api.suggestions_status(request.generation, True))
            assert response.complete and response.error is None and response.result is not None
            assert response.result.query == "sender"
        else:
            batch = PreviewBatch.model_validate(api.take_previews([1], True))
            assert not batch.pending and batch.error is None
            assert batch.previews[0].preview == "The report is ready."
    finally:
        release.set()
        api.close()


def test_preview_futures_ignore_old_archive_and_observe_reader_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Desktop previews: only current-generation results publish; failures are consumed and retryable."""
    first = make_gui_archive(tmp_path / "first")
    second = make_gui_archive(tmp_path / "second")
    with sqlite3.connect(second / "search.sqlite3") as database:
        database.execute("UPDATE message_metadata SET preview='New archive preview'")
    blocker = sqlite3.connect(first / "search.sqlite3")
    blocker.execute("BEGIN EXCLUSIVE")
    release = Event()
    api = GuiApi(first)
    try:
        import mailarchiver.gui_app as gui_module
        started = Event()
        real_read = gui_module.message_previews

        def observed_read(archive: Path, keys: list[int]):
            # Acknowledge entry only; this remains the actual blocked SQLite reader.
            started.set()
            return real_read(archive, keys)

        monkeypatch.setattr(gui_module, "message_previews", observed_read)
        api.request_previews([1])
        old_future = next(iter(api._preview_futures))
        assert started.wait(2), "Preview reader did not enter"
        assert old_future.running()
        queued_gate = api._preview_executor.submit(release.wait)
        api.archive = second
        api.invalidate_archive()
        api.request_previews([1])
        current_future = next(future for future, request in api._preview_futures.items() if request.archive == second)
        blocker.rollback()
        assert old_future.result(timeout=5)[0].preview == "The report is ready."
        batch = api.take_previews([1])
        assert batch["pending"] and batch["previews"] == [] and batch["error"] is None
        release.set()
        queued_gate.result(timeout=5)
        current_future.result(timeout=5)
        assert api.take_previews([1])["previews"] == [{"message_pk": 1, "preview": "New archive preview"}]
        api.invalidate_archive()
        search = second / "search.sqlite3"
        saved = search.with_suffix(".saved")
        search.rename(saved)
        search.write_bytes(b"invalid SQLite fixture")
        try:
            api.request_previews([1])
            failed = next(iter(api._preview_futures))
            with pytest.raises(sqlite3.DatabaseError):
                failed.result(timeout=5)
            batch = api.take_previews([1])
            assert not batch["pending"] and batch["previews"] == [] and batch["error"]
        finally:
            search.unlink()
            saved.rename(search)
        api.request_previews([1])
        next(iter(api._preview_futures)).result(timeout=5)
        assert api.take_previews([1])["error"] is None
    finally:
        release.set()
        blocker.close()
        api.close()
    with pytest.raises(ValueError, match="closed"):
        api.request_previews([1])


def test_gui_selects_multipart_views_and_sanitizes_html(tmp_path: Path) -> None:
    """Requirement: body alternatives are selectable and HTML is inert with remote content blocked."""
    archive = make_gui_archive(tmp_path)
    view = describe_message(archive, 2)
    plain_id = next(part.part_id for part in view.body_parts if part.content_type == "text/plain")
    html_id = next(part.part_id for part in view.body_parts if part.content_type == "text/html")

    assert {part.content_type for part in view.body_parts} == {"text/plain", "text/html", "message/rfc822"}
    assert view.preferred_part_id == html_id
    assert [(item.filename, item.preview) for item in view.attachments] == [
        ("logo.png", "image"),
        ("report.pdf", "pdf"),
        ("notes.txt", None),
    ]
    assert render_part(archive, 2, plain_id).content == "Plain version."
    blocked = render_part(archive, 2, html_id)
    assert "<script" not in blocked.content
    assert "onload" not in blocked.content
    assert 'href="https://example.org/archive"' in blocked.content
    assert "target=\"_blank\"" not in blocked.content
    assert "data:image/png;base64,aW1hZ2U=" in blocked.content
    assert "tracker.example" not in blocked.content
    assert blocked.remote_content_blocked
    allowed = render_part(archive, 2, html_id, allow_remote=True)
    assert "https://tracker.example/pixel" in allowed.content
    assert not allowed.remote_content_blocked
    assert "Subject: multipart message" in render_part(archive, 2, RAW_PART_ID).content


@pytest.mark.parametrize("newline", [b"\n", b"\r\n", b"\r"])
@pytest.mark.parametrize("transfer", ["7bit", "base64", "quoted-printable", "x-uuencode"])
@pytest.mark.parametrize("malformed", [False, True])
def test_attached_messages_keep_original_bytes_and_parent_body(
    tmp_path: Path, newline: bytes, transfer: str, malformed: bool,
) -> None:
    """DESKTOP/SEARCH: attached EML is exact export data, never its parent's body."""
    import quopri
    from email import policy
    from email.parser import BytesParser
    from mailarchiver.search import attachment_text, message_text
    child = (b"From: nested@example.test\nSubject: original \xff\nX-Folded: first\n\tsecond\n"
             + (b"malformed header line\n" if malformed else b"")
             + b"Content-Type: text/html\n\n<p>nestedattachmentsecret</p>\n").replace(b"\n", newline)
    encoded = (base64.b64encode(child) if transfer == "base64" else quopri.encodestring(child) if transfer == "quoted-printable"
               else codecs.encode(child, "uu").replace(b"\n", newline) if transfer == "x-uuencode" else child)
    prefix = (b"From: sender@example.net\nMessage-ID: <nested-export@example>\n"
              b"Date: Mon, 1 Jan 2024 00:00:00 +0000\nSubject: enclosing message\n"
              b"Content-Type: multipart/mixed; boundary=audit\n\n--audit\n"
              b"Content-Type: text/plain\n\nouterbodyonly\n--audit\n"
              b"Content-Type: message/rfc822\nContent-Disposition: attachment; filename=original.eml\n"
              + f"Content-Transfer-Encoding: {transfer}\n\n".encode()).replace(b"\n", newline)
    raw = prefix + encoded + newline + b"--audit--" + newline
    archive = make_gui_archive(tmp_path, ((raw, "nested-export@example", "enclosing message", "2024-01-01T00:00:00+00:00"),))
    view = describe_message(archive, 3)
    assert len(view.attachments) == 1
    attachment = view.attachments[0]
    assert attachment.byte_length == len(child)
    assert render_part(archive, 3, view.preferred_part_id).content == "outerbodyonly"
    assert "nestedattachmentsecret" not in message_text(raw, False)
    assert "nestedattachmentsecret" in message_text(raw, True)
    if transfer != "7bit":
        assert attachment_text(BytesParser(policy=policy.compat32).parsebytes(raw)) == ""
    assert base64.b64decode(attachment_content(archive, 3, attachment.part_id).content_base64) == child
    output = tmp_path / "saved-original.eml"
    write_attachment(archive, 3, attachment.part_id, output)
    assert output.read_bytes() == child
    assert read_message_bytes(archive, 3) == raw


@pytest.mark.parametrize("filename", ["CON.txt", "con .txt", "NUL", "COM¹.pdf", "LPT9.log", "a?b*.pdf", 'a"<>|b.pdf',
    "report.pdf. ", "😀" * 100 + ".pdf", "a" * 239 + " " + "b" * 60,
    " " * 240 + "b" * 60, "CON" + " " * 240 + "b" * 60, "CON." + "x" * 300])
def test_attachment_export_names_are_portable(filename: str) -> None:
    """DESKTOP: derived basenames are valid on Windows and bounded in UTF-8 bytes."""
    name = safe_filename(filename, 2, "application/pdf")
    assert name and not any(character in name for character in '<>:"/\\|?*')
    assert not name.endswith((".", " ")) and len(name.encode("utf-8")) <= 240
    assert name.split(".", 1)[0].rstrip(" ").upper() not in {"CON", "NUL", "COM¹", "LPT9"}


def test_gui_recovers_declared_korean_headers_and_raw_display(tmp_path: Path) -> None:
    """Requirement: GUI recovery uses declared Korean charsets for derived headers and source text."""
    archive = make_gui_archive(
        tmp_path,
        ((KOREAN_MESSAGE, "legacy-korean@example", "placeholder", "2003-05-17T06:23:19+00:00"),),
    )

    view = describe_message(archive, 3)
    raw = render_part(archive, 3, RAW_PART_ID).content
    html = render_part(archive, 3, view.preferred_part_id).content

    assert view.subject == "이렇게 하면 부자가 됩니다"
    assert next(header.value for header in view.headers if header.name == "From").startswith("장나나")
    assert "장나나" in raw and "최고의 기회" in raw and "�" not in raw
    assert "최고의 기회" in html and "�" not in html


def test_gui_prefers_the_most_substantive_html_part(tmp_path: Path) -> None:
    """Requirement: a message with multiple HTML parts opens its substantive HTML view."""
    archive = make_gui_archive(
        tmp_path,
        ((MULTI_HTML_MESSAGE, "multi-html@example", "multi HTML message", "2024-01-07T10:00:00+00:00"),),
    )

    view = describe_message(archive, 3)

    assert view.preferred_part_id == 2
    assert "Complete web report" in render_part(archive, 3, view.preferred_part_id).content


def test_gui_recognizes_whole_body_legacy_x_html_without_hiding_raw_source(tmp_path: Path) -> None:
    """Requirement: a whole-body x-html wrapper is a safe HTML view while raw RFC 5322 remains selectable."""
    archive = make_gui_archive(
        tmp_path,
        (
            (LEGACY_X_HTML_MESSAGE, "legacy-x-html@example", "legacy x-html", "2024-01-05T10:00:00+00:00"),
            (X_HTML_MENTION_MESSAGE, "x-html-mention@example", "x-html mention", "2024-01-06T10:00:00+00:00"),
        ),
    )

    legacy = describe_message(archive, 3)
    assert [(part.part_id, part.content_type, part.label) for part in legacy.body_parts] == [
        (LEGACY_X_HTML_PART_ID, "text/html", "HTML — legacy x-html"),
        (RAW_PART_ID, "message/rfc822", "Raw Source"),
    ]
    assert legacy.preferred_part_id == LEGACY_X_HTML_PART_ID
    rendered = render_part(archive, 3, LEGACY_X_HTML_PART_ID)
    assert rendered.kind == "html"
    assert "Legacy HTML version." in rendered.content
    assert "x-html" not in rendered.content.casefold()
    assert "<script" not in rendered.content.casefold()
    assert "onload" not in rendered.content.casefold()
    assert "tracker.example" not in rendered.content
    assert rendered.remote_content_blocked
    assert "<x-html>" in render_part(archive, 3, RAW_PART_ID).content

    mention = describe_message(archive, 4)
    assert [(part.part_id, part.content_type) for part in mention.body_parts] == [
        (0, "text/plain"),
        (RAW_PART_ID, "message/rfc822"),
    ]
    with pytest.raises(ValueError, match="no legacy x-html"):
        render_part(archive, 4, LEGACY_X_HTML_PART_ID)


def test_gui_displays_archive_and_source_locations(tmp_path: Path) -> None:
    """Requirement: the message viewer distinguishes archive mailboxes from source discoveries."""
    archive = make_gui_archive(tmp_path)

    view = describe_message(archive, 1)

    assert view.archive_path == "data/mbox/2024-Archive1.mbox"
    assert [(item.volume, item.path, item.offset) for item in view.source_locations] == [
        ("Fixture Backup", "mail/simple.eml", 0)
    ]
    assert view.source_locations[0].origin == "Local source"
    assert not view.source_locations[0].preferred
    assert view.source_locations[0].copy_path == "/Volumes/Fixture/mail/simple.eml"
    archive_path = describe_message(archive, 2).archive_path
    assert archive_path is not None
    assert archive_path.startswith("data/mbox/2024-Archive1.mbox?offset=")

    database = sqlite3.connect(archive / "archive.sqlite3")
    try:
        database.execute("UPDATE source_files SET source_path = 'Users/arch/simple.eml'")
        database.execute(
            "UPDATE source_volumes SET metadata_json = ?",
            (json.dumps({"volume_label": "Local source", "current_mount_path": "/"}),),
        )
        database.commit()
    finally:
        database.close()
    local_root = describe_message(archive, 1).source_locations[0]
    assert local_root.path == "/Users/arch/simple.eml"
    assert local_root.copy_path == "/Users/arch/simple.eml"


def test_gui_prefers_direct_cloud_observation_over_local_cache(tmp_path: Path) -> None:
    """Requirement: direct provider provenance precedes a retained local-cache observation."""
    archive = make_gui_archive(tmp_path)
    database = sqlite3.connect(archive / "archive.sqlite3")
    try:
        cache_metadata = SourceContainerMetadata(
            display_name="Apple Mail All Mail cache",
            hierarchy=("[Gmail]", "All Mail"),
            provenance_json="{}",
            relationship=SourceRelationship(
                role="cache", upstream_plugin_kind="gmail", account_hint="APPLE-ACCOUNT-UUID"
            ),
        ).model_dump_json()
        database.execute("UPDATE source_files SET metadata_json = ?", (cache_metadata,))
        provider_volume = database.execute(
            "INSERT INTO source_volumes(identity_json, metadata_json, first_observed_at, last_observed_at) "
            "VALUES (?, ?, '2026-08-28', '2026-08-28') RETURNING source_volume_pk",
            (
                json.dumps({"plugin_kind": "gmail", "source_id": "simsong@gmail.com"}),
                json.dumps({"plugin_kind": "gmail", "volume_label": "simsong@gmail.com"}),
            ),
        ).fetchone()
        assert provider_volume is not None
        provider_metadata = SourceContainerMetadata(
            display_name="All Mail",
            hierarchy=("All Mail",),
            provenance_json="{}",
        ).model_dump_json()
        provider_file = database.execute(
            "INSERT INTO source_files(source_volume_pk, source_plugin, source_path, hierarchy_path, "
            "metadata_json, path_kind, source_kind) VALUES (?, 'gmail', 'messages/1', 'All Mail', ?, "
            "'provider', 'gmail') RETURNING source_file_pk",
            (provider_volume[0], provider_metadata),
        ).fetchone()
        assert provider_file is not None
        run_pk = database.execute("SELECT min(run_pk) FROM ingest_runs").fetchone()[0]
        database.execute(
            "INSERT INTO observations(run_pk, message_pk, source_file_pk, source_cursor, raw_sha256, "
            "disposition, detail) VALUES (?, 1, ?, 'gmail-message-1', '', 'duplicate', "
            "'same Message-ID and SHA-256')",
            (run_pk, provider_file[0]),
        )
        database.commit()
    finally:
        database.close()

    view = describe_message(archive, 1)

    assert [item.origin for item in view.source_locations] == [
        "Direct Gmail source",
        "Local cache of Gmail",
    ]
    assert [item.preferred for item in view.source_locations] == [True, False]


def test_gui_exposes_computed_date_tag_for_warning_banner(tmp_path: Path) -> None:
    """Requirement: the GUI identifies messages whose catalog date replaced the Date header."""
    archive = make_gui_archive(tmp_path)
    database = sqlite3.connect(archive / "archive.sqlite3")
    try:
        database.execute(
            "UPDATE messages SET date_utc = '2024-02-02T00:00:00+00:00', date_source = 'received-median' "
            "WHERE message_pk = 1"
        )
        database.commit()
    finally:
        database.close()

    view = describe_message(archive, 1)
    assert view.date_source == "received-median"
    assert view.date_adjustment is not None
    assert view.date_adjustment.model_dump() == {
        "date_header": "Wed, 03 Jan 2024 10:00:00 +0000",
        "received_median_utc": "2024-02-02T00:00:00+00:00",
        "archive_routing_utc": "2024-02-02T00:00:00+00:00",
    }


def test_gui_exports_exact_eml_and_decoded_attachment(tmp_path: Path) -> None:
    """Requirement: .eml export preserves RFC 5322 bytes and attachment export decodes the selected part."""
    archive = make_gui_archive(tmp_path)
    view = describe_message(archive, 2)
    pdf = next(item for item in view.attachments if item.content_type == "application/pdf")
    eml_path, pdf_path = tmp_path / "message.eml", tmp_path / "report.pdf"

    write_message(archive, 2, eml_path)
    write_attachment(archive, 2, pdf.part_id, pdf_path)

    assert eml_path.read_bytes() == MULTIPART_MESSAGE
    assert hashlib.sha256(eml_path.read_bytes()).hexdigest() == hashlib.sha256(MULTIPART_MESSAGE).hexdigest()
    assert pdf_path.read_bytes() == b"%PDF-1.4\n"
    descriptor = attachment_descriptor(archive, 2, pdf.part_id)
    assert (descriptor.filename, descriptor.content_type) == ("report.pdf", "application/pdf")
    content = attachment_content(archive, 2, pdf.part_id)
    assert base64.b64decode(content.content_base64) == pdf_path.read_bytes()
    assert content.filename == "report.pdf"


def test_gui_multi_message_drag_zip_preserves_each_exported_message(tmp_path: Path) -> None:
    """Requirement: an explicit multi-message drag creates a ZIP of exact RFC 5322 exports."""
    archive = make_gui_archive(tmp_path)
    destination = tmp_path / "drag" / "messages.zip"

    write_messages_zip(archive, [1, 2, 1], destination)

    with zipfile.ZipFile(destination) as exported:
        names = exported.namelist()
        values = {name: exported.read(name) for name in names}
    assert len(names) == 2
    assert set(values.values()) == {SIMPLE_MESSAGE, MULTIPART_MESSAGE}


def test_gui_concurrent_drag_exports_have_distinct_temporary_paths(tmp_path: Path) -> None:
    """Requirement: concurrent drag preparation cannot share a temporary export file."""
    archive = make_gui_archive(tmp_path)
    api = GuiApi(archive)
    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            exports = list(executor.map(lambda number: api.prepare_drag([1 if number % 2 else 2]), range(16)))
        paths = []
        for number, exported in enumerate(exports):
            with api._drag_export(exported["token"]) as path:
                assert path.read_bytes() == (SIMPLE_MESSAGE if number % 2 else MULTIPART_MESSAGE)
                paths.append(path)
        assert len(set(paths)) == 16
    finally:
        api.close()
    assert not any(path.exists() for path in paths)


def test_opened_attachments_keep_separate_export_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """DESKTOP: opening a second same-named attachment cannot change the first viewer's file."""
    from mailarchiver import gui_app
    other = MULTIPART_MESSAGE.replace(b"JVBERi0xLjQK", b"JVBERi0xLjUK")
    archive = make_gui_archive(tmp_path, ((other, "other-attachment@example", "other", "2024-01-01T00:00:00+00:00"),))
    opened: list[Path] = []
    # Intercept only the external viewer launch; extraction and path ownership are real.
    monkeypatch.setattr(gui_app, "open_export", opened.append)
    api = GuiApi(archive)
    try:
        for message_pk in (2, 3):
            attachment = next(item for item in describe_message(archive, message_pk).attachments if item.content_type == "application/pdf")
            assert api.open_attachment(message_pk, attachment.part_id)["opened"]
        assert len(set(opened)) == 2
        assert [path.read_bytes() for path in opened] == [b"%PDF-1.4\n", b"%PDF-1.5\n"]
    finally:
        api.close()


@pytest.mark.parametrize(("filename", "content_type", "risky"), [
    ("installer.dmg", "application/octet-stream", True),
    ("archive.tar", "application/x-tar", True),
    ("archive.gz", "application/gzip", True),
    ("archive.7z", "application/x-7z-compressed", True),
    ("archive.rar", "application/vnd.rar", True),
    ("document.docm", "application/vnd.ms-word.document.macroenabled.12", True),
    ("results.csv", "text/csv", True),
    ("script", "application/x-sh", True),
    ("picture.png", "application/octet-stream", True),
    ("document.pdf.exe", "application/pdf", True),
    ("report.pdf", "application/pdf", False),
    ("picture.PNG", "image/png", False),
    ("notes.txt", "text/plain", False),
])
def test_gui_attachment_confirmation(filename: str, content_type: str, risky: bool) -> None:
    """Requirement: only a matching inert MIME/suffix pair opens without confirmation."""
    assert is_risky(filename, content_type) is risky


def test_gui_external_links_require_a_safe_explicit_destination(tmp_path: Path) -> None:
    """Requirement: the viewer can pass only approved external links to its explicit actions."""
    api = GuiApi(None, e2e_directory=tmp_path)
    try:
        for destination in ("https://example.org/path?view=1", "http://example.org/", "mailto:archivist@example.org"):
            assert external_link_destination(destination) == destination
            assert api.open_link(destination) == destination
        for destination in ("", "https://", "file:///private/secret", "javascript:alert(1)", "https://example.org/\nnext"):
            with pytest.raises(ValueError):
                external_link_destination(destination)
    finally:
        api.close()


def find_tree_node(nodes: list[MailboxTreeNode], label: str) -> MailboxTreeNode:
    for node in nodes:
        if node.label == label:
            return node
        try:
            return find_tree_node(node.children, label)
        except AssertionError:
            continue
    raise AssertionError(f"tree has no node named {label}")


def add_tree_source(
    archive: Path, identity: str, label: str, path: str, source_kind: str, message_pks: list[int]
) -> None:
    database = sqlite3.connect(archive / "archive.sqlite3")
    try:
        volume = database.execute(
            "INSERT INTO source_volumes(identity_json, metadata_json, first_observed_at, last_observed_at) "
            "VALUES (?, ?, '2026-08-26', '2026-08-26') ON CONFLICT(identity_json) DO UPDATE SET "
            "metadata_json = excluded.metadata_json RETURNING source_volume_pk",
            (identity, json.dumps({"volume_label": label, "current_mount_path": f"/Volumes/{label}"})),
        ).fetchone()
        assert volume is not None
        source_file = database.execute(
            "INSERT INTO source_files(source_volume_pk, source_path, hierarchy_path, path_kind, source_kind) "
            "VALUES (?, ?, ?, 'file', ?) RETURNING source_file_pk",
            (volume[0], path, path, source_kind),
        ).fetchone()
        assert source_file is not None
        run_pk = database.execute("SELECT min(run_pk) FROM ingest_runs").fetchone()[0]
        database.executemany(
            "INSERT INTO observations(run_pk, message_pk, source_file_pk, raw_sha256, disposition, detail) "
            "VALUES (?, ?, ?, '', 'archived', 'Archive')",
            ((run_pk, message_pk, source_file[0]) for message_pk in message_pks),
        )
        database.commit()
    finally:
        database.close()


def test_original_mailbox_tree_deduplicates_counts_merges_volumes_and_filters(tmp_path: Path) -> None:
    """Requirement: tree counts and selected-path unions use canonical message identities."""
    archive = make_gui_archive(tmp_path)
    add_tree_source(archive, '{"stable_id":"backup-1"}', "Backup 1", "Professional/Inbox/work.mbox", "mbox", [1, 2])
    add_tree_source(archive, '{"stable_id":"backup-2"}', "Backup 2", "Professional/Inbox/work.mbox", "mbox", [1])
    add_tree_source(archive, '{"stable_id":"backup-1b"}', "Backup 1", "Personal/Loose/001.eml", "message", [1])

    merged = mailbox_tree(archive)
    professional = find_tree_node(merged, "Professional")
    loose = find_tree_node(merged, "Loose")

    assert professional.count == 2
    assert loose.count == 1
    assert not any(node.label == "001.eml" for node in loose.children)
    assert [item.message_pk for item in search_page(archive, "", mailbox_selections=[professional.selection]).results] == [2, 1]
    assert [item.message_pk for item in search_page(archive, "", mailbox_selections=[loose.selection]).results] == [1]

    by_volume = mailbox_tree(archive, show_volumes=True)
    assert {node.label for node in by_volume} >= {"Backup 1", "Backup 2"}
    backup_two = next(node for node in by_volume if node.label == "Backup 2")
    scoped = find_tree_node(backup_two.children, "Professional")
    assert scoped.count == 1
    assert [item.message_pk for item in search_page(archive, "", mailbox_selections=[scoped.selection]).results] == [1]


@pytest.mark.parametrize("show_volumes", [False, True])
def test_imap_mailbox_labels_unquote_once_without_changing_selections(tmp_path: Path, show_volumes: bool) -> None:
    """Desktop requirement: decode IMAP labels, retaining literal names and exact folder filters."""
    archive = make_gui_archive(tmp_path)
    identity = '{"stable_id":"imap-labels"}'
    encoded = "%5BGmail%5D/All%20Mail"
    add_tree_source(archive, identity, "IMAP", encoded, "imap", [1, 2])
    add_tree_source(archive, identity, "IMAP", "%5BGmail%5D/Literal%2520%2B%25%2F%2E%C3%89", "imap", [1])
    add_tree_source(archive, '{"stable_id":"local-labels"}', "Local", "Local%20Folder/A+B%25.mbox", "mbox", [2])
    tree = mailbox_tree(archive, show_volumes)
    gmail = find_tree_node(tree, "[Gmail]")
    all_mail = find_tree_node(gmail.children, "All Mail")
    literal = find_tree_node(gmail.children, "Literal%20+%/.É")
    assert gmail.count == 2 and all_mail.count == 2 and literal.count == 1
    assert not literal.children
    selection = MailboxSelection.from_token(all_mail.selection)
    assert selection.path == encoded
    assert selection.volume_identity == (identity if show_volumes else None)
    assert MailboxSelection.from_token(all_mail.logical_selection).path == encoded
    assert [item.message_pk for item in search_page(archive, "", mailbox_selections=[all_mail.selection]).results] == [2, 1]
    assert [item.message_pk for item in search_page(archive, "", mailbox_selections=[literal.selection]).results] == [1]
    assert find_tree_node(tree, "Local%20Folder").children[0].label == "A+B%25.mbox"


def test_filter_sets_are_versioned_and_atomically_managed(tmp_path: Path) -> None:
    """Requirement: named selections persist outside an archive and support rename/delete."""
    path = tmp_path / "preferences" / "filter-sets.json"
    store = FilterSetStore(path)

    saved = store.save(FilterSet(name="Work", selections=["selection-a"]))
    assert [(item.name, item.selections) for item in saved.filter_sets] == [("Work", ["selection-a"])]
    assert path.read_text(encoding="utf-8").endswith("\n")
    renamed = store.rename("Work", "Professional")
    assert [item.name for item in renamed.filter_sets] == ["Professional"]
    assert store.delete("Professional").filter_sets == []
    with pytest.raises(ValueError, match="reserved"):
        store.save(FilterSet(name="None"))


def test_original_mailbox_count_and_search_queries_use_provenance_indexes(tmp_path: Path) -> None:
    """Requirement: source-tree counts and pre-page filtering use the intended indexes."""
    archive = make_gui_archive(tmp_path)
    selection = MailboxSelection(path="mail")
    database = sqlite3.connect(archive / "archive.sqlite3")
    try:
        count_plan = database.execute(
            "EXPLAIN QUERY PLAN SELECT count(DISTINCT observations.message_pk) "
            "FROM source_files INDEXED BY source_files_hierarchy_volume "
            "JOIN observations INDEXED BY observations_source_file_offset USING (source_file_pk) "
            "WHERE observations.message_pk IS NOT NULL AND (source_files.hierarchy_path = ? OR "
            "(source_files.hierarchy_path >= ? AND source_files.hierarchy_path < ?))",
            ("mail", "mail/", "mail0"),
        ).fetchall()
        database.execute("ATTACH DATABASE ? AS search", (str(archive / "search.sqlite3"),))
        statement = _search_statement(parse_query(""), 10, mailbox_selections=[selection])
        search_plan = database.execute("EXPLAIN QUERY PLAN " + statement.sql, statement.parameters).fetchall()
    finally:
        database.close()

    assert any("source_files_hierarchy_volume" in detail for *_prefix, detail in count_plan)
    assert any("observations_source_file_offset" in detail for *_prefix, detail in count_plan)
    assert any("SEARCH source_files" in detail and "source_files_hierarchy_volume" in detail
               for *_prefix, detail in search_plan)
    assert any("SEARCH observations" in detail and "observations_source_file_offset" in detail
               for *_prefix, detail in search_plan)
    assert not any("CORRELATED" in detail for *_prefix, detail in search_plan)


@pytest.mark.parametrize("query, error", [
    ("from:", "from: requires a value"),
    ("To:", "to: requires a value"),
    ('subject:"unfinished', "search has an unclosed quote"),
    ("date:yesterday", "date: requires a valid date (YYYY-MM-DD, m/d/yyyy, or Month day, year)"),
])
def test_gui_search_syntax_errors_are_results(tmp_path: Path, query: str, error: str) -> None:
    """Requirement: invalid GUI queries return feedback without a bridge exception."""
    archive = make_gui_archive(tmp_path)
    api = GuiApi(archive)
    try:
        result = api.search(query)
        assert result["error"] == error
        assert result["results"] == []
        assert result["has_more"] is False
        assert api.search("report")["results"]
    finally:
        api.close()


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
def test_multipart_attachment_is_one_exact_export_and_never_parent_body(tmp_path: Path, newline: bytes) -> None:
    """DESKTOP/SEARCH: multipart attachment ancestry and byte export agree across readers."""
    from email import policy
    from email.parser import BytesParser
    from mailarchiver.search import indexed_attachments, message_text
    from mailarchiver.processing.mime import extract_parts
    payload = (b"--inner\nContent-Type: text/html\n\n<p>nestedsecret</p>\n--inner--\n").replace(b"\n", newline)
    raw = (b"From: sender@example.net\nDate: Mon, 1 Jan 2024 00:00:00 +0000\nSubject: multipart attachment\n"
           b"Content-Type: multipart/mixed; boundary=outer\n\n--outer\nContent-Type: text/plain\n\nparentonly\n"
           b"--outer\nContent-Type: multipart/mixed; boundary=inner\nContent-Disposition: attachment; filename=parts.mime\n\n").replace(b"\n", newline)
    raw += payload + newline + b"--outer--" + newline
    archive = make_gui_archive(tmp_path, ((raw, "multipart-attachment@example", "multipart attachment", "2024-01-01T00:00:00+00:00"),))
    view = describe_message(archive, 3)
    attachment, = view.attachments
    assert base64.b64decode(attachment_content(archive, 3, attachment.part_id).content_base64) == payload
    assert render_part(archive, 3, view.preferred_part_id).content == "parentonly"
    assert "nestedsecret" not in message_text(raw, False) and "nestedsecret" in message_text(raw, True)
    assert len(indexed_attachments(BytesParser(policy=policy.compat32).parsebytes(raw), raw)) == 1
    source = tmp_path / "multipart.eml"
    source.write_bytes(raw)
    workspace = tmp_path / "parts"
    workspace.mkdir()
    extracted = extract_parts(source, workspace)
    assert len(extracted.attachments) == 1 and extracted.attachments[0].part_id == attachment.part_id
    assert all(part.scope == "attachment" for part in extracted.parts if b"nestedsecret" in part.reference.path.read_bytes())


def malformed_attachment_message(transfer: str, payload: bytes, content_type: str = "message/rfc822") -> bytes:
    """Build a readable parent with invalid transfer data in one attached email."""
    return (b"From: sender@example.net\nDate: Mon, 1 Jan 2024 00:00:00 +0000\nSubject: readable parent\n"
           b"Content-Type: multipart/mixed; boundary=outer\n\n--outer\nContent-Type: text/plain\n\nhealthybody\n"
           + f"--outer\nContent-Type: {content_type}\nContent-Disposition: attachment; filename=bad.eml\n".encode()
           + f"Content-Transfer-Encoding: {transfer}\n\n".encode() + payload + b"\n--outer--\n")


@pytest.mark.parametrize(("transfer", "payload"), [
    ("base64", b"%%%invalid%%%"), ("quoted-printable", b"=zz"), ("uuencode", b"begin 644 bad.eml\n!\n"),
    ("uuencode", b"begin 644 bad.eml\nM\nend\n"),
])
@pytest.mark.parametrize("content_type", ["message/rfc822", "application/pdf", "image/png"])
def test_bad_attachment_encoding_does_not_hide_its_parent(tmp_path: Path, transfer: str, payload: bytes, content_type: str) -> None:
    """DESKTOP: invalid attached data is local metadata failure, while parent/raw/export remain accessible."""
    raw = malformed_attachment_message(transfer, payload, content_type)
    archive = make_gui_archive(tmp_path, ((raw, "bad-attachment@example", "readable parent", "2024-01-01T00:00:00+00:00"),))
    view = describe_message(archive, 3)
    attachment, = view.attachments
    assert attachment.byte_length is None and attachment.error and "Cannot decode attachment" in attachment.error
    from mailarchiver.search import message_text
    assert payload.decode() not in message_text(raw, True)
    assert render_part(archive, 3, view.preferred_part_id).content == "healthybody"
    assert payload.decode() in render_part(archive, 3, RAW_PART_ID).content
    output = tmp_path / "original.eml"
    write_message(archive, 3, output)
    assert output.read_bytes() == raw
    destination = tmp_path / "bad-export.eml"
    with pytest.raises(ValueError):
        write_attachment(archive, 3, attachment.part_id, destination)
    assert not destination.exists() and read_message_bytes(archive, 3) == raw
    api = GuiApi(archive)
    try:
        exported = api.prepare_drag([3])
        with api._drag_export(exported["token"]) as path:
            assert path.read_bytes() == raw
    finally:
        api.close()


@pytest.mark.parametrize("separator", [b"\n\r\n", b"\r\n\n", b"\r\n\r", b"\r\r\n", b"\n\r"])
def test_attached_export_retains_headers_with_mixed_newline_separator(tmp_path: Path, separator: bytes) -> None:
    """DESKTOP: raw payload offsets follow logical header lines, including mixed legacy separators."""
    child = b"From: nested@example.test\nSubject: retained headers\n\nchild body\n"
    raw = (b"From: sender@example.net\nDate: Mon, 1 Jan 2024 00:00:00 +0000\nSubject: mixed attachment\n"
           b"Content-Type: multipart/mixed; boundary=x\n\n--x\nContent-Type: message/rfc822\n"
           b"Content-Disposition: attachment; filename=child.eml" + separator + child + b"\n--x--\n")
    archive = make_gui_archive(tmp_path, ((raw, "mixed-export@example", "mixed attachment", "2024-01-01T00:00:00+00:00"),))
    attachment, = describe_message(archive, 3).attachments
    assert attachment.error is None and attachment.byte_length == len(child)
    assert base64.b64decode(attachment_content(archive, 3, attachment.part_id).content_base64) == child


@pytest.mark.parametrize("position", ["first", "later"])
@pytest.mark.parametrize("newline", [b"\n", b"\r\n", b"\r"])
def test_redundant_opening_boundaries_keep_attachment_numbering(tmp_path: Path, position: str, newline: bytes) -> None:
    """DESKTOP: stdlib-ignored repeated boundaries cannot create a zero-byte or wrong attachment."""
    child = b"From: nested@example.test\nSubject: exact child\n\nnonempty child\n".replace(b"\n", newline)
    head = (b"From: sender@example.net\nDate: Mon, 1 Jan 2024 00:00:00 +0000\nSubject: repeated boundary\n"
            b"Content-Type: multipart/mixed; boundary=x\n\n").replace(b"\n", newline)
    before = b"--x\nContent-Type: text/plain\n\nparent body\n" if position == "later" else b""
    wrapper = (b"--x\n--x\nContent-Type: message/rfc822\nContent-Disposition: attachment; filename=child.eml\n\n")
    raw = head + (before + wrapper).replace(b"\n", newline) + child + newline + b"--x--" + newline
    archive = make_gui_archive(tmp_path, ((raw, "repeated-export@example", "repeated boundary", "2024-01-01T00:00:00+00:00"),))
    attachment, = describe_message(archive, 3).attachments
    assert attachment.error is None and attachment.byte_length == len(child)
    assert base64.b64decode(attachment_content(archive, 3, attachment.part_id).content_base64) == child


def test_standalone_webview_is_registered_and_listed_without_native_start(tmp_path: Path) -> None:
    """DESKTOP: standalone children participate in shared menu/focus snapshots on both platforms."""
    import webview
    application = PyWebViewApplication(ApplicationController(ApplicationPreferencesStore(tmp_path / "preferences.json")))
    document = application.controller.create_document(tmp_path / "archive")
    window = webview.create_window("Standalone synthetic message", "about:blank", menu=application.menu())
    assert window is not None
    application._native_child_ids[window.uid] = document.descriptor.document_id
    try:
        assert window in application._registered_windows()
        assert [menu.title for menu in window.menu] == ["File", "Window", "Help"]
        assert any(item.title == window.title for item in application.window_menu_items())
    finally:
        application._native_child_ids.pop(window.uid)
        webview.windows.remove(window)
        application.shutdown()


@pytest.mark.parametrize("newline", [b"\n", b"\r\n", b"\r"])
def test_attached_payload_with_missing_header_separator_keeps_all_original_bytes(tmp_path: Path, newline: bytes) -> None:
    """DESKTOP: a malformed wrapper's first non-header line starts payload without silent truncation."""
    child = (b"malformed first body line\nFrom: child@example.test\nSubject: original child\n\noriginal child body\n").replace(b"\n", newline)
    raw = (b"From: sender@example.net\nDate: Mon, 1 Jan 2024 00:00:00 +0000\nSubject: malformed wrapper\n"
           b"Content-Type: multipart/mixed; boundary=x\n\n--x\nContent-Type: message/rfc822\n"
           b"Content-Disposition: attachment; filename=child.eml\n").replace(b"\n", newline)
    raw += child + newline + b"--x--" + newline
    archive = make_gui_archive(tmp_path, ((raw, "missing-separator@example", "malformed wrapper", "2024-01-01T00:00:00+00:00"),))
    attachment, = describe_message(archive, 3).attachments
    assert attachment.error is None and attachment.byte_length == len(child)
    assert base64.b64decode(attachment_content(archive, 3, attachment.part_id).content_base64) == child


@pytest.mark.parametrize("newline", [b"\n", b"\r\n", b"\r"])
@pytest.mark.parametrize("body", [b"", b"original body\n"])
def test_missing_separator_unix_envelope_refuses_ambiguous_export(tmp_path: Path, newline: bytes, body: bytes) -> None:
    """DESKTOP: ambiguous wrapper envelopes cannot silently disappear from an exported attachment."""
    child = b"From retained Thu Jan  1 00:00:00 1970\n" + body
    raw = malformed_attachment_message("7bit", child).replace(b"Content-Transfer-Encoding: 7bit\n\n", b"Content-Transfer-Encoding: 7bit\n")
    raw = raw.replace(b"\n", newline)
    archive = make_gui_archive(tmp_path, ((raw, "ambiguous-envelope@example", "readable parent", "2024-01-01T00:00:00+00:00"),))
    view = describe_message(archive, 3)
    attachment, = view.attachments
    assert attachment.error and "ambiguous Unix envelope" in attachment.error and attachment.byte_length is None
    assert render_part(archive, 3, view.preferred_part_id).content == "healthybody"
    output = tmp_path / "original.eml"
    write_message(archive, 3, output)
    assert output.read_bytes() == raw
    destination = tmp_path / "ambiguous.eml"
    with pytest.raises(ValueError, match="ambiguous Unix envelope"):
        write_attachment(archive, 3, attachment.part_id, destination)
    assert not destination.exists() and read_message_bytes(archive, 3) == raw


@pytest.mark.parametrize("newline", [b"\n", b"\r\n", b"\r"])
@pytest.mark.parametrize("transfer", ["x-uuencode", "uuencode", "uue", "x-uue"])
def test_legacy_uuencode_binary_attachment_exports_decoded_bytes(tmp_path: Path, newline: bytes, transfer: str) -> None:
    """DESKTOP: previously supported uuencode aliases retain binary export across transport line endings."""
    payload = b"%PDF-1.0\nlegacy binary \x00\xff\r\n"
    encoded = codecs.encode(payload, "uu").replace(b"\nend\n", b"\n end \t\n")
    raw = malformed_attachment_message(transfer, encoded, "application/pdf").replace(b"\n", newline)
    archive = make_gui_archive(tmp_path, ((raw, "uuencode@example", "readable parent", "2024-01-01T00:00:00+00:00"),))
    attachment, = describe_message(archive, 3).attachments
    assert attachment.error is None and attachment.byte_length == len(payload)
    output = tmp_path / "legacy.pdf"
    write_attachment(archive, 3, attachment.part_id, output)
    assert output.read_bytes() == payload and read_message_bytes(archive, 3) == raw


def test_many_small_attachments_have_responsive_exact_metadata(tmp_path: Path) -> None:
    """DESKTOP: a small valid message within the part budget must not incur quadratic selection latency."""
    payload = b"x" * 1024
    parts = [f"--many\nContent-Type: application/octet-stream\nContent-Disposition: attachment; filename={number}.bin\n\n".encode()
             + payload + b"\n" for number in range(2000)]
    raw = (b"From: sender@example.test\nContent-Type: multipart/mixed; boundary=many\n\n" + b"".join(parts) + b"--many--\n")
    archive = make_gui_archive(tmp_path, ((raw, "many@example", "many attachments", "2024-01-01T00:00:00+00:00"),))
    started = time.monotonic()
    view = describe_message(archive, 3)
    elapsed = time.monotonic() - started
    assert len(view.attachments) == 2000 and all(item.byte_length == len(payload) and item.error is None for item in view.attachments)
    # Wide allowance for CI hosts; the regression took about 29 seconds locally.
    assert elapsed < 10, f"describing a 2 MiB message took {elapsed:.2f}s"
    for item in (view.attachments[0], view.attachments[-1]):
        assert base64.b64decode(attachment_content(archive, 3, item.part_id).content_base64) == payload


@pytest.mark.parametrize("transfer", ["7bit", "base64", "quoted-printable"])
def test_encoded_attached_multipart_keeps_inventory_reader_and_export_ids(tmp_path: Path, transfer: str) -> None:
    """SEARCH/DESKTOP: decoded child traversal gives later attachments the same IDs before and after rebuild."""
    import quopri
    from mailarchiver.processing.mime import extract_parts
    from mailarchiver.search import prepare_search_message
    child = (b"From: child@example.test\nContent-Type: multipart/alternative; boundary=child\n\n"
             b"--child\nContent-Type: text/plain\n\nchild body\n--child\nContent-Type: text/html\n\n<p>child body</p>\n--child--\n")
    encoded = base64.b64encode(child) if transfer == "base64" else quopri.encodestring(child) if transfer == "quoted-printable" else child
    raw = (b"From: parent@example.test\nContent-Type: multipart/mixed; boundary=outer\n\n"
           b"--outer\nContent-Type: text/plain\n\nparent body\n--outer\nContent-Type: message/rfc822\n"
           b"Content-Disposition: attachment; filename=child.eml\nContent-Transfer-Encoding: " + transfer.encode() + b"\n\n"
           + encoded + b"\n--outer\nContent-Type: application/pdf\nContent-Disposition: attachment; filename=later.pdf\n\n%PDF-original\n--outer--\n")
    source, workspace = tmp_path / "source.eml", tmp_path / "parts"
    source.write_bytes(raw)
    workspace.mkdir()
    extracted = extract_parts(source, workspace)
    rebuilt = prepare_search_message(raw, False)
    expected = [(2, "child.eml"), (6, "later.pdf")]
    assert [(item.part_id, item.filename) for item in extracted.attachments] == expected
    assert [(item.part_id, item.filename) for item in rebuilt.attachments] == expected
    archive = make_gui_archive(tmp_path, ((raw, "stable-ids@example", "stable IDs", "2024-01-01T00:00:00+00:00"),))
    view = describe_message(archive, 3)
    assert [(item.part_id, item.filename) for item in view.attachments] == expected
    assert render_part(archive, 3, view.preferred_part_id).content == "parent body"
    assert base64.b64decode(attachment_content(archive, 3, 2).content_base64) == child
    assert base64.b64decode(attachment_content(archive, 3, 6).content_base64) == b"%PDF-original"
