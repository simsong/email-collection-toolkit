"""Verify semantic reconciliation between Apple Mail EMLX and canonical MBOX."""

from __future__ import annotations

import hashlib
import mailbox
from pathlib import Path

from mailarchiver.apple_mail_compare import compare_apple_mail
from mailarchiver.bagit import initialize_bag
from mailarchiver.catalog import address_pk, create_catalog
from mailarchiver.layout import mbox_directory
from mailarchiver.mbox import add_message
from mailarchiver.standalone_verify import semantic_bytes


def _write_emlx(path: Path, raw: bytes, *, partial: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = b"<?xml version='1.0'?><plist></plist>"
    path.with_name(path.stem + ".partial.emlx" if partial else path.name).write_bytes(
        str(len(raw)).encode() + b"\n" + raw + suffix
    )


def _archive(archive: Path, messages: list[bytes]) -> None:
    initialize_bag(archive)
    path = mbox_directory(archive) / "2024-Archive1.mbox"
    catalog = create_catalog(archive / "archive.sqlite3")
    run_pk = catalog.execute(
        "INSERT INTO ingest_runs(started_at, completed_at, result, detail) VALUES ('now', 'now', 'complete', '')"
    ).lastrowid
    volume_pk = catalog.execute(
        "INSERT INTO source_volumes(identity_json, metadata_json, first_observed_at, last_observed_at) "
        "VALUES ('{\"kind\":\"fixture\"}', '{}', 'now', 'now')"
    ).lastrowid
    source_pk = catalog.execute(
        "INSERT INTO source_files(source_volume_pk, source_path, hierarchy_path, path_kind, source_kind) "
        "VALUES (?, 'fixture', '', 'file', 'mbox')",
        (volume_pk,),
    ).lastrowid
    generation_pk = catalog.execute(
        "INSERT INTO mbox_generations(filename, sha256, message_count, byte_count) VALUES (?, '', 0, 0)",
        (path.name,),
    ).lastrowid
    box = mailbox.mbox(path)
    try:
        for ordinal, raw in enumerate(messages, 1):
            location = add_message(box, path, raw)
            digest = hashlib.sha256(raw).hexdigest()
            sender_pk = address_pk(catalog, "sender@example")
            message_pk = catalog.execute(
                "INSERT INTO messages(message_id_normalized, sha256, sender_address_pk, subject, "
                "date_utc, date_source, category) VALUES (?, ?, ?, '', '2024-01-01', 'date', 'Archive')",
                (f"message-{ordinal}@example", digest, sender_pk),
            ).lastrowid
            catalog.execute(
                "INSERT INTO locations(message_pk, generation_pk, byte_offset, byte_length) VALUES (?, ?, ?, ?)",
                (message_pk, generation_pk, location.byte_offset, location.byte_length),
            )
            catalog.execute(
                "INSERT INTO observations(run_pk, message_pk, source_file_pk, source_offset, raw_sha256, "
                "semantic_sha256, disposition, detail) VALUES (?, ?, ?, ?, ?, ?, 'archived', '')",
                (run_pk, message_pk, source_pk, ordinal, digest, hashlib.sha256(semantic_bytes(raw)).hexdigest()),
            )
    finally:
        box.close()
        catalog.commit()
        catalog.close()


def test_comparison_distinguishes_exact_semantic_header_and_missing_records(tmp_path: Path) -> None:
    """Requirement: cache reconciliation uses h3 while reporting raw and header differences."""
    exact = b"From: sender@example\nMessage-ID: <exact@example>\n\nexact body\n"
    archived_added = b"From: sender@example\nMessage-ID: <added@example>\n\nadded body\n"
    apple_added = archived_added.replace(
        b"Message-ID:", b"X-Apple-Test: local metadata\nMessage-ID:"
    )
    archived_folded = (
        b"From: sender@example\r\nMessage-ID: <folded@example>\r\n"
        b"Subject: folded\r\n value\r\n\r\nbody\r\n"
    )
    apple_refolded = archived_folded.replace(b"Subject: folded\r\n value", b"Subject:  folded value")
    archive_only = b"From: sender@example\nMessage-ID: <archive-only@example>\n\narchive\n"
    cache_only = b"From: sender@example\nMessage-ID: <cache-only@example>\n\ncache\n"
    archive = tmp_path / "archive"
    apple = tmp_path / "Mail"
    _archive(archive, [exact, archived_added, archived_folded, archive_only])
    _write_emlx(apple / "V10/account/Inbox.mbox/Messages/1.emlx", exact)
    _write_emlx(apple / "V10/account/Inbox.mbox/Messages/2.emlx", apple_added)
    _write_emlx(apple / "V10/account/Inbox.mbox/Messages/3.emlx", apple_refolded)
    _write_emlx(apple / "V10/account/Inbox.mbox/Messages/4.emlx", cache_only)
    _write_emlx(apple / "V10/account/Inbox.mbox/Messages/5.emlx", b"not complete", partial=True)

    report = compare_apple_mail(apple, archive, progress_every=0)

    assert report.complete_emlx == 4
    assert report.partial_emlx == 1
    assert report.unreadable_emlx == 0
    assert report.exact_raw_matches == 1
    assert report.semantic_only_matches == 2
    assert report.cache_only_messages == 1
    assert report.archive_semantic_matches == 3
    assert report.archive_only_messages == 1
    assert report.ambiguous_semantic_matches == 0
    assert report.header_pairs_analyzed == 2
    assert report.formatting_only_pairs == 1
    assert [(change.name, change.messages, change.occurrences) for change in report.apple_added_headers] == [
        ("x-apple-test", 1, 1)
    ]
    assert report.apple_missing_headers == []
    assert report.changed_headers == []
    assert not report.source_changed_during_scan
