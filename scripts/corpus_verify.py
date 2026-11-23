# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Check an imported archive against the synthetic corpus, end to end.

Purpose: prove an import kept every generated message intact, with correct
metadata and search, rather than only counting what the importer reports.
How it works: regenerate the corpus from its seed (``perf_corpus``), then read
the archive's own files and databases. It checks the message count, unique
Subjects, hashes, each location pointer into the archived MBOX, sender,
recipients, date, Message-ID, attachments, full-text search for each message's
tokens and sampled words, and processing state, and runs the archive verifier.
Document identifiers must be searchable for every attachment type that an
installed processor reads. Other types are reported as gaps, not failures.
System role: used by ``benchmark_import.py`` and the crash-recovery tests.
"""

from __future__ import annotations

import contextlib
import email
import email.policy
import hashlib
import random
import sqlite3
import sys
from pathlib import Path

from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent))
from perf_corpus import CorpusManifest, SyntheticMessage, corpus_messages  # noqa: E402

from mailarchiver.layout import mbox_directory  # noqa: E402
from mailarchiver.mailsearch import read_message_bytes  # noqa: E402
from mailarchiver.mboxrd import unquote  # noqa: E402
from mailarchiver.plugin_loader import builtin_plugin_directory  # noqa: E402
from mailarchiver.processing.registry import load_processors  # noqa: E402
from mailarchiver.standalone_verify import verify_archive  # noqa: E402

SAMPLED_WORDS = 50


class Verification(BaseModel):
    problems: list[str] = []
    gaps: list[str] = []


def _first(items: set[str] | list[str]) -> str:
    return min(items) if items else ""


def _matches(database: sqlite3.Connection, table: str, word: str) -> set[str]:
    return {row[0] for row in database.execute(f"SELECT sha256 FROM {table} WHERE {table} MATCH ?", (f'"{word}"',))}


def verify(archive: Path, manifest: CorpusManifest, readable: set[str] | None = None) -> Verification:  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    """Return problems (failures) and gaps; ``readable`` overrides the installed processors' MIME types."""
    result = Verification()
    problems = result.problems
    messages: dict[str, SyntheticMessage] = {}
    for message in corpus_messages(manifest.target_bytes, manifest.seed):
        messages[hashlib.sha256(message.archived).hexdigest()] = message
    if len(messages) != manifest.message_count:
        problems.append(f"corpus regenerated {len(messages)} distinct messages, manifest says {manifest.message_count}")
    if len({message.subject for message in messages.values()}) != len(messages):
        problems.append("generated Subjects are not unique")

    with contextlib.closing(sqlite3.connect(archive / "archive.sqlite3")) as catalog:
        rows = catalog.execute(
            "SELECT m.message_pk, m.sha256, m.message_id_normalized, m.subject, m.date_utc, m.date_source, "
            "a.address, g.filename, l.byte_offset, l.byte_length FROM messages m "
            "JOIN email_addresses a ON a.address_pk = m.sender_address_pk "
            "LEFT JOIN locations l ON l.message_pk = m.message_pk "
            "LEFT JOIN mbox_generations g ON g.generation_pk = l.generation_pk").fetchall()
        recipients: dict[int, set[tuple[str, str]]] = {}
        for pk, address, role in catalog.execute(
                "SELECT r.message_pk, a.address, r.role FROM recipients r JOIN email_addresses a USING(address_pk)"):
            recipients.setdefault(pk, set()).add((address, role))
    if len(rows) != len(messages):
        problems.append(f"catalog holds {len(rows)} messages, the corpus has {len(messages)}")
    cataloged = {row[1] for row in rows}
    if missing := messages.keys() - cataloged:
        problems.append(f"{len(missing)} corpus messages are not in the catalog, e.g. {_first(missing)}")
    if extra := cataloged - messages.keys():
        problems.append(f"{len(extra)} catalog messages are not in the corpus, e.g. {_first(extra)}")

    wrong: dict[str, list[str]] = {}
    for pk, digest, message_id, subject, date_utc, date_source, sender, filename, offset, length in rows:
        expected = messages.get(digest)
        if expected is None:
            continue
        def flag(field: str, digest: str = digest) -> None:
            wrong.setdefault(field, []).append(digest)
        try:
            stored = read_message_bytes(archive, pk)  # Follows the catalog's location pointer.
        except (ValueError, OSError):
            stored = b""
        if stored != expected.archived or hashlib.sha256(stored).hexdigest() != digest:
            flag("bytes or hash")
            stored = expected.archived  # Still check the remaining metadata.
        if filename is None:
            flag("location pointer (missing)")
        else:
            try:
                with (mbox_directory(archive) / filename).open("rb") as mbox:
                    mbox.seek(offset)
                    record = mbox.read(length)
            except OSError:
                flag("location pointer (unreadable MBOX)")
            else:
                envelope, _, body = record.partition(b"\n")
                if not envelope.startswith(b"From ") or unquote(body).rstrip(b"\n") != expected.archived.rstrip(b"\n"):
                    flag("location pointer (wrong bytes)")
        if subject != expected.subject:
            flag("subject")
        if message_id != expected.message_id.strip("<>"):
            flag("Message-ID")
        if sender != expected.sender:
            flag("sender")
        if recipients.get(pk, set()) != {(address, "to") for address in expected.recipients}:
            flag("recipients")
        if expected.date_utc is not None and (date_source != "date" or date_utc != expected.date_utc.isoformat()):
            flag("date")
        if expected.date_utc is None and date_source == "date":
            flag("date source of undated message")
        parsed = email.message_from_bytes(stored, policy=email.policy.default)
        found: dict[str | None, str] = {}
        for part in parsed.walk():
            if part.get_filename():
                payload = part.get_payload(decode=True)
                found[part.get_filename()] = hashlib.sha256(payload if isinstance(payload, bytes) else b"").hexdigest()
        if found != {attachment.filename: attachment.sha256 for attachment in expected.attachments}:
            flag("attachment bytes")
    for field, digests in sorted(wrong.items()):
        problems.append(f"{len(digests)} messages have the wrong {field}, e.g. {_first(digests)}")

    if readable is None:
        readable = {mime for plugin in load_processors((builtin_plugin_directory(),)) for mime in plugin.manifest.subscribes}
    with contextlib.closing(sqlite3.connect(archive / "search.sqlite3")) as search:
        metadata = {digest: count for digest, count in search.execute("SELECT sha256, attachment_count FROM message_metadata")}
        listed: dict[str, set[tuple[str, str]]] = {}
        for digest, filename, mime in search.execute("SELECT sha256, filename, mime_type FROM message_attachments"):
            listed.setdefault(digest, set()).add((filename, mime))
        unsearchable: list[str] = []
        misindexed: list[str] = []
        for digest, message in messages.items():
            if digest not in metadata:
                unsearchable.append(digest)
                continue
            expected_attachments = {(a.filename, a.mime_type) for a in message.attachments}
            if metadata[digest] != len(message.attachments) or listed.get(digest, set()) != expected_attachments:
                misindexed.append(digest)
            for token in (message.subject_token, message.body_token):
                if _matches(search, "message_fts", token) != {digest}:
                    unsearchable.append(digest)
        if unsearchable:
            problems.append(f"{len(set(unsearchable))} messages are missing from search or their tokens do not "
                            f"find exactly them, e.g. {_first(unsearchable)}")
        if misindexed:
            problems.append(f"{len(misindexed)} messages have wrong attachment listings in search, e.g. {_first(misindexed)}")
        rng = random.Random(manifest.seed)
        sample = rng.sample(sorted(messages), min(SAMPLED_WORDS, len(messages)))
        picks = [(rng.choice(messages[digest].words), digest) for digest in sample]
        missed = [f"{word} in {digest}" for word, digest in picks if digest not in _matches(search, "message_fts", word)]
        if missed:
            problems.append(f"{len(missed)} of {len(sample)} sampled body words are not indexed for their message, e.g. {missed[0]}")
        for digest, message in sorted(messages.items()):
            for attachment in message.attachments:
                if attachment.identifier is None:
                    continue
                hits = _matches(search, "attachment_fts", attachment.identifier) | _matches(
                    search, "message_fts", attachment.identifier)
                if hits == {digest}:
                    continue
                if attachment.mime_type in readable:
                    problems.append(f"{attachment.filename}: identifier {attachment.identifier} is not searchable")
                else:
                    result.gaps.append(f"{attachment.filename} ({attachment.mime_type}): no installed processor "
                                       "extracts its text; identifier not searchable")

    with contextlib.closing(sqlite3.connect(archive / "processing.sqlite3")) as processing:
        unfinished = processing.execute("SELECT status, count(*) FROM jobs WHERE status != 'completed' GROUP BY status").fetchall()
    if unfinished:
        problems.append("processing jobs not completed: " + ", ".join(f"{status}={count}" for status, count in unfinished))
    problems.extend(f"archive verification: {failure}" for failure in verify_archive(archive, quiet=True))
    return result
