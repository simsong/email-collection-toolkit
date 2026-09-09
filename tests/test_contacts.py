"""Requirements: Contact CLI is read-only and meaningful only for direct correspondence."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from mailarchiver.catalog import address_pk, create_catalog


def _message(
    database: sqlite3.Connection,
    sender: int,
    date: str,
    category: str,
    recipients: tuple[tuple[int, str], ...] = (),
) -> None:
    cursor = database.execute(
        "INSERT INTO messages(message_id_normalized, sha256, sender_address_pk, subject, date_utc, date_source, category) "
        "VALUES (?, ?, ?, '', ?, 'date', ?)",
        (date, hashlib.sha256(date.encode()).hexdigest(), sender, date, category),
    )
    for address, role in recipients:
        database.execute("INSERT INTO recipients(message_pk, address_pk, role) VALUES (?, ?, ?)", (cursor.lastrowid, address, role))


def _archive(tmp_path: Path) -> Path:
    archive = tmp_path / "archive"
    archive.mkdir()
    database = create_catalog(archive / "archive.sqlite3")
    try:
        owner = address_pk(database, "owner@example.org")
        alice = address_pk(database, "alice@example.org")
        bob = address_pk(database, "bob@example.org")
        hidden = address_pk(database, "hidden@example.org")
        copy = address_pk(database, "copy@example.org")
        list_sender = address_pk(database, "list@example.org")
        _message(database, alice, "2024-01-01T00:00:00+00:00", "Archive", ((owner, "to"),))
        _message(database, owner, "2024-01-02T00:00:00+00:00", "Sent", ((bob, "to"), (copy, "cc"), (hidden, "bcc")))
        _message(database, alice, "2024-01-03T00:00:00+00:00", "Archive", ((copy, "cc"),))
        _message(database, list_sender, "2024-01-04T00:00:00+00:00", "Archive", ((owner, "cc"),))
        _message(database, list_sender, "2024-01-05T00:00:00+00:00", "Archive", ((owner, "to"),))
        database.commit()
    finally:
        database.close()
    return archive


def _contacts(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "mailarchiver", *arguments], text=True, capture_output=True, check=False)


def test_contacts_meaningful_filter_uses_direct_to_and_bcc_only(tmp_path: Path) -> None:
    """Requirement: direct correspondence excludes Cc and list mail without owner in To."""
    archive = _archive(tmp_path)
    owners = tmp_path / "owners.txt"
    owners.write_text("# archive owner\nowner, unused; another-unused\n", encoding="utf-8")
    result = _contacts(
        "--archive", str(archive), "human-contacts", "--owner-address-file", str(owners), "--format", "json"
    )

    assert result.returncode == 0, result.stderr
    rows = {row["address"]: row for row in json.loads(result.stdout)}
    assert set(rows) == {"alice@example.org", "bob@example.org", "hidden@example.org", "list@example.org"}
    assert rows["bob@example.org"] == {
        "address": "bob@example.org",
        "first_seen": "2024-01-02",
        "last_seen": "2024-01-02",
        "message_count": 1,
    }
    assert rows["alice@example.org"]["message_count"] == 2
    assert rows["list@example.org"]["message_count"] == 2
    assert "copy@example.org" not in rows


def test_contacts_all_lists_every_header_address_without_owner_configuration(tmp_path: Path) -> None:
    """Requirement: all-header Contact statistics are available without meaningful classification."""
    archive = _archive(tmp_path)
    result = _contacts("--archive", str(archive), "human-contacts", "--all", "--format", "tsv")

    assert result.returncode == 0, result.stderr
    rows = {line.split("\t")[0]: line.split("\t") for line in result.stdout.splitlines()[1:]}
    assert rows["copy@example.org"][1:] == ["2024-01-02", "2024-01-03", "2"]
    assert "owner@example.org" in rows


def test_contacts_reports_unmatched_owner_alias_without_a_traceback(tmp_path: Path) -> None:
    """Regression: an owner-name file incompatible with an archive is a normal CLI error."""
    archive = _archive(tmp_path)
    owners = tmp_path / "owners.txt"
    owners.write_text("not-an-owner", encoding="utf-8")

    result = _contacts("--archive", str(archive), "human-contacts", "--owner-address-file", str(owners))

    assert result.returncode == 2
    assert "owner aliases did not match a Sent sender address" in result.stderr
    assert "Traceback" not in result.stderr
