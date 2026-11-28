# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Requirements: the Sources tab lists, enables and rescans every imported source.

Real CLI imports of a fixture directory record it as a source; Rescan Imports…
imports every checked source again, skipping files whose size and modification time
are unchanged without rereading them, while a changed file is reread and its
messages are recognized as duplicates. IMAP accounts appear with their settings and
checkbox; changing the server requires a password, a username change does not.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from mailarchiver.__main__ import IngestRequest, run_ingest
from mailarchiver.archive_config import load_archive_config, save_archive_config
from mailarchiver.gui_sources import (
    archive_sources,
    local_source_id,
    record_local_sources,
    rescan_roots,
    set_source_enabled,
    update_imap_account,
)
from mailarchiver.imap_models import ImapAccount
from tests.test_cli_processing import HEADER

OWNER = "owner@example.test"


def _import(archive: Path, roots: list[str]) -> None:
    run_ingest(IngestRequest(archive=archive, roots=roots, scan_policy="not-scanned",
                             owner_rules={"include": [OWNER], "exclude": []}), terminal=False)


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "Mail"
    source.mkdir()
    (source / "one.eml").write_bytes(HEADER + b"\r\nfirst body\r\n")
    (source / "two.eml").write_bytes(HEADER.replace(b"<parent@example.test>", b"<second@example.test>")
                                     + b"\r\nsecond body\r\n")
    return source


def test_imports_record_sources_and_rescan_skips_unchanged_files(tmp_path: Path) -> None:
    """Requirement: an import records its directory; a rescan skips unchanged files by size and mtime."""
    archive, source = tmp_path / "archive", _source(tmp_path)
    _import(archive, [str(source)])
    entries = archive_sources(archive)
    assert [(entry.kind, entry.detail, entry.enabled, entry.available) for entry in entries] == [
        ("directory", str(source), True, True)]
    assert rescan_roots(archive, 4) == [str(source)]

    touched = source / "two.eml"
    stat = touched.stat()
    os.utime(touched, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    _import(archive, rescan_roots(archive, 4))
    with sqlite3.connect(archive / "archive.sqlite3") as catalog:
        decisions = dict(catalog.execute(
            "SELECT s.source_path, c.reason FROM source_integrity_checks c JOIN source_files s USING (source_file_pk) "
            "WHERE c.run_pk = 2"))
        assert catalog.execute("SELECT count(*) FROM messages").fetchone() == (2,)
    reasons = {Path(path).name: reason for path, reason in decisions.items()}
    assert reasons["one.eml"] == "size and modification time match prior checkpoint"
    assert reasons["two.eml"] == "complete SHA-256 matches prior checkpoint"  # mtime changed: reread, same bytes.


def test_unchecked_and_missing_sources_are_left_out_of_a_rescan(tmp_path: Path) -> None:
    """Requirement: only checked, reachable sources are rescanned; the checkbox persists."""
    archive, source = tmp_path / "archive", _source(tmp_path)
    _import(archive, [str(source)])
    gone = tmp_path / "Gone"
    record_local_sources(archive, [str(gone)])
    source_id = local_source_id(str(source))
    assert {entry.id: entry.available for entry in archive_sources(archive)} == {
        source_id: True, local_source_id(str(gone)): False}
    set_source_enabled(archive, source_id, False)
    assert not next(entry for entry in archive_sources(archive) if entry.id == source_id).enabled
    assert rescan_roots(archive, 4) == []
    record_local_sources(archive, [str(source)])  # A later import keeps the checkbox.
    assert rescan_roots(archive, 4) == []
    assert load_archive_config(archive).version == 4


def test_sources_from_earlier_imports_and_imap_accounts(tmp_path: Path) -> None:
    """Requirement: roots of imports before the Sources list existed and IMAP accounts are listed."""
    archive, source = tmp_path / "archive", _source(tmp_path)
    _import(archive, [str(source)])
    config = load_archive_config(archive)
    config.local_sources = []  # As written by a version that did not record sources.
    account = ImapAccount(server="imap.example.test", username="owner@example.test")
    config.imap_sources = [account]
    save_archive_config(archive, config)
    assert [(entry.kind, entry.label) for entry in archive_sources(archive)] == [
        ("directory", "Mail"), ("imap", "owner@example.test")]
    assert rescan_roots(archive, 2)[1].startswith(f"imap://{account.id}?")
    set_source_enabled(archive, account.id, False)
    assert len(rescan_roots(archive, 2)) == 1
    previous, updated = update_imap_account(archive, account.id, "mail.example.test", 993, "other@example.test", "implicit")
    assert (previous.id, updated.id, updated.enabled, updated.server) == (account.id, account.id, False, "mail.example.test")
    with pytest.raises(ValueError, match="unknown IMAP source"):
        update_imap_account(archive, "missing", "x.example.test", 993, "u", "implicit")
