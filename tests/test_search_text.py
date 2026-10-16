# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Exercise natural search against real disposable catalogs and FTS databases.
# Synthetic messages cover old unnormalized indexes and newly indexed mail.
# Quotes retain symbols while ordinary phrases retain native word boundaries.
# Unicode normalization must agree across content, selectors and attachments.
# Counts and ordered pages must exclude rejected candidates before their limits.
# File hashes establish that compatibility searches do not require reindexing.
"""Requirements: natural normalized search and literal quoted symbols, without rebuilding."""
from __future__ import annotations

import hashlib
import sqlite3
from email.header import Header
from pathlib import Path

import pytest

from mailarchiver.bagit import initialize_bag
from mailarchiver.catalog import address_pk, create_catalog, create_search
from mailarchiver.gui_service import search_page, search_suggestions
from mailarchiver.mailsearch import SortDirection, SortField, parse_query, search_headers, search_result_count
from mailarchiver.search import index_message, message_text
from mailarchiver.search_text import compile_text, search_vocabulary
from test_mailsearch import add_catalogued_message, run_search


def test_split_compatibility_prunes_unrelated_documents_before_reading_content() -> None:
    """Selective searches cannot fetch every document containing a one-letter prefix."""
    with sqlite3.connect(":memory:") as database:
        database.execute("ATTACH ':memory:' AS search")
        database.execute("CREATE VIRTUAL TABLE search.message_fts USING fts5(content)")
        database.executemany("INSERT INTO search.message_fts VALUES (?)",
                             [("a résumé description " * 100,)] * 1000)
        vocabulary = search_vocabulary(database, None, False)
        compiled = compile_text("annual", vocabulary, database, False)
        query = compiled.match + (f" OR ({compiled.split_match})" if compiled.split_match else "")
        assert database.execute(
            "SELECT count(*) FROM search.message_fts WHERE message_fts MATCH ?", (query,),
        ).fetchone() == (0,)
        database.execute("INSERT INTO search.message_fts VALUES (?)", ("an\u1dc0nu\u1dc0al",))
        compiled = compile_text("annual", search_vocabulary(database, None, False), database, False)
        assert compiled.split_match
        assert database.execute(
            "SELECT count(*) FROM search.message_fts WHERE message_fts MATCH ?", (compiled.split_match,),
        ).fetchone() == (1,)


def test_ambiguous_split_fallback_retains_native_sharp_s(tmp_path: Path) -> None:
    """Bounded legacy fragmentation must keep native prefixes that expand under folding."""
    archive = natural_archive(tmp_path, ["straße\u1dc0" + "a" * 20,
                                         " ".join("a" * size for size in range(1, 21))], legacy=True)
    assert [row.message_pk for row in search_page(archive, "strasse" + "a" * 20).results] == [1]


def test_fragment_search_bounds_dead_end_exploration() -> None:
    """An absent repetitive token cannot explore exponentially many native fragment paths."""
    with sqlite3.connect(":memory:") as database:
        database.execute("ATTACH ':memory:' AS search")
        database.execute("CREATE VIRTUAL TABLE search.message_fts USING fts5(content)")
        database.execute("INSERT INTO search.message_fts VALUES ('a aa')")
        vocabulary = search_vocabulary(database, None, False)
        statements: list[str] = []
        database.set_trace_callback(statements.append)
        compiled = compile_text("a" * 40 + "z", vocabulary, database, False)
        assert compiled.split_match
        assert sum(statement.startswith("SELECT term FROM search_words_")
                   for statement in statements) <= 64


def natural_archive(root: Path, bodies: list[str], *, legacy: bool = False,
                    attachment: str = "") -> Path:
    archive = root / "archive"
    initialize_bag(archive)
    catalog = create_catalog(archive / "archive.sqlite3")
    search = create_search(archive / "search.sqlite3")
    messages: list[bytes] = []
    try:
        sender = address_pk(catalog, "sender@example.org")
        for number, body in enumerate(bodies, 1):
            headers = (
                f"From: {Header('José Straße', 'utf-8').encode()} <sender@example.org>\n"
                f"Subject: {Header(f'Café Straße {number}', 'utf-8').encode()}\n"
                f"Date: {number:02d} Jan 2024 10:00:00 +0000\nMessage-ID: <natural-{number}@example.org>\n"
            )
            payload = f"Content-Type: text/plain; charset=utf-8\n\n{body}\n"
            if attachment:
                payload = (
                    'Content-Type: multipart/mixed; boundary="natural"\n\n--natural\n'
                    + payload + '\n--natural\nContent-Type: text/plain; charset=utf-8\n'
                    'Content-Disposition: attachment; filename="synthetic.txt"\n\n'
                    + attachment + "\n--natural--\n"
                )
            raw = (headers + payload).encode()
            digest = hashlib.sha256(raw).hexdigest()
            catalog.execute(
                "INSERT INTO messages(message_pk,message_id_normalized,sha256,sender_address_pk,subject,"
                "date_utc,date_source,category) VALUES (?,?,?,?,?,?,'date','Archive')",
                (number, f"natural-{number}@example.org", digest, sender,
                 f"Café Straße {number}", f"2024-01-{number:02d}T10:00:00+00:00"),
            )
            index_message(search, raw, bool(attachment))
            if legacy:
                search.execute("UPDATE message_fts SET content=? WHERE sha256=?", (message_text(raw, False), digest))
                if attachment:
                    search.execute("UPDATE attachment_fts SET content=? WHERE sha256=?", (attachment, digest))
            messages.append(raw)
        catalog.commit()
        search.commit()
    finally:
        catalog.close()
        search.close()
    for number, raw in enumerate(messages, 1):
        add_catalogued_message(archive, number, raw)
    return archive


@pytest.mark.parametrize("legacy", [False, True])
def test_normalization_matches_old_and_new_index_without_rebuilding(tmp_path: Path, legacy: bool) -> None:
    """Case, composed/decomposed accents, sharp s and native accent exceptions agree."""
    archive = natural_archive(tmp_path, ["Hám+Sandwhích CACM-123 a\u0301bc Straße ẞtraße hộp Καλημέρα"], legacy=legacy)
    files = [archive / "search.sqlite3", archive / "data/mbox/2024-Archive1.mbox"]
    before = [hashlib.sha256(path.read_bytes()).hexdigest() for path in files]
    for query in ('"HAM+SANDWHICH"', '"cacm-"', "ABC", "STRASSE", "straße", "sstrasse", "hop", "ΚΑΛΗΜΕΡΑ"):
        assert len(search_page(archive, query).results) == 1, query
    assert [hashlib.sha256(path.read_bytes()).hexdigest() for path in files] == before
    assert search_page(archive, "connect").results == []


@pytest.mark.parametrize("legacy", [False, True])
def test_quotes_preserve_symbols_without_changing_unquoted_word_search(tmp_path: Path, legacy: bool) -> None:
    """A quoted plus is literal; spaces and other separators must not substitute for it."""
    archive = natural_archive(tmp_path, ["HÁM+SANDWHICH", "Ham Sandwhich", "Ham-Sandwhich",
                                         "Ham+delicious+Sandwhich", "Ham+Sandwhich extra"], legacy=legacy)
    assert [row.message_pk for row in search_page(archive, '"Ham+Sandwhich"').results] == [5, 1]
    assert [row.message_pk for row in search_page(archive, "Ham+Sandwhich").results] == [5, 3, 2, 1]
    assert [row.message_pk for row in search_page(archive, '"Ham Sandwhich"').results] == [5, 3, 2, 1]
    assert [row.message_pk for row in search_page(archive, "'Ham+Sandwhich'").results] == [5, 1]


@pytest.mark.parametrize("legacy", [False, True])
def test_decomposed_non_latin_accents_do_not_require_reindexing(tmp_path: Path, legacy: bool) -> None:
    """Legacy token breaks at non-Latin combining accents must not hide whole words."""
    archive = natural_archive(tmp_path, ["Καλημε\u0301ρα שָׁלוֹם", "ΚαλημεραSuffix"], legacy=legacy)
    for query in ("ΚΑΛΗΜΕΡΑ", "שלום"):
        assert [row.message_pk for row in search_page(archive, query).results] == [1]


@pytest.mark.parametrize("legacy", [False, True])
def test_split_latin_accents_work_in_bodies_and_attachments(tmp_path: Path, legacy: bool) -> None:
    """A combining dotted grave accent can split Latin words in an old native index."""
    archive = natural_archive(tmp_path, ["co\u1dc0operate", "co operate"], legacy=legacy,
                              attachment="re\u1dc0sume+send")
    assert [row.message_pk for row in search_page(archive, "cooperate").results] == [1]
    assert search_result_count(archive, parse_query("cooperate")) == 1
    assert search_page(archive, '"resume+send"').results == []
    assert len(search_page(archive, '"resume+send"', search_attachments=True).results) == 2


@pytest.mark.parametrize("legacy", [False, True])
def test_split_native_fragments_are_not_normalized_word_matches(tmp_path: Path, legacy: bool) -> None:
    """Legacy native fragments and phrases cannot survive removed accent boundaries."""
    archive = natural_archive(tmp_path, ["co\u1dc0operate a\u1dc0bc"], legacy=legacy,
                              attachment="co\u1dc0operate a\u1dc0bc")
    for query in ("co", "a", '"co operate"'):
        for attachments in (False, True):
            assert search_page(archive, query, search_attachments=attachments).results == []
            assert search_result_count(archive, parse_query(query), search_attachments=attachments) == 0
    assert len(search_page(archive, "cooperate abc").results) == 1


@pytest.mark.parametrize("query,bodies,expected", [
    ('"CACM-"', ["CACM", "CACM/QUEUE", "CACM-123", "XCACM-123", "CACM XCACM-123"], [3]),
    ('"+Ham"', ["+Ham", "Ham+", "Ham"], [1]),
    ('"C++"', ["C++", "C", "C+", "C+++"] , [4, 1]),
    ('"a_b"', ["a_b", "a b", "a-b"], [1]),
    ('"rate%"', ["rate%", "rateX"], [1]),
    ('"+"', ["plus+", "plain"], [2, 1]),  # Indexed Date headers also contain +0000.
    ('"★"', ["star★", "plain"], [1]),
    ('"CACM-" "C++"', ["CACM-123 C++", "CACM C++", "CACM-123 C"], [1]),
    ('"Ham+Sandwhich"', ["Ham\x00+Sandwhich", "Ham+Sandwhich"], [2]),
    ('"Ham+Sandwhich"', ["Ham Sandwhich XHam+SandwhichY", "Ham+SandwhichY", "XHam+Sandwhich"], []),
])
def test_literal_prefix_suffix_internal_symbols_and_combinations(
    tmp_path: Path, query: str, bodies: list[str], expected: list[int],
) -> None:
    """Symbols are not SQL wildcards, and filtering does not introduce interior-word matches."""
    archive = natural_archive(tmp_path, bodies, legacy=True)
    assert [row.message_pk for row in search_page(archive, query).results] == expected


def test_postfilter_precedes_counts_limits_offsets_and_all_orderings(tmp_path: Path) -> None:
    """Rejected newer candidates cannot hide older literal matches or inflate counts."""
    archive = natural_archive(tmp_path, ["café+straße", "cafe strasse", "café+straße", "cafe strasse"], legacy=True)
    terms = parse_query('"CAFE+STRASSE"')
    assert search_result_count(archive, terms) == 2
    assert search_result_count(archive, terms, maximum=1) == 1
    for sort in SortField:
        for direction in SortDirection:
            all_rows = search_headers(archive, terms, 0, sort_by=sort, direction=direction)
            assert {row.message_pk for row in all_rows} == {1, 3}
            for offset, expected in enumerate(all_rows):
                page = search_headers(archive, terms, 1, offset, sort_by=sort, direction=direction)
                assert page == [expected]
            assert search_headers(archive, terms, 1, 2, sort_by=sort, direction=direction) == []


@pytest.mark.parametrize("legacy", [False, True])
def test_attachment_terms_keep_their_own_literal_predicates(tmp_path: Path, legacy: bool) -> None:
    """A term can match body or opted-in attachment text, with literals checked in that same table."""
    archive = natural_archive(tmp_path, ["alpha café strasse"], legacy=legacy, attachment="Café+Straße")
    query = 'alpha "CAFE+STRASSE"'
    assert search_page(archive, query).results == []
    assert len(search_page(archive, query, search_attachments=True).results) == 1
    assert search_result_count(archive, parse_query(query), search_attachments=True) == 1
    assert search_page(archive, '"alpha+Café"', search_attachments=True).results == []


@pytest.mark.parametrize("legacy", [False, True])
def test_separate_occurrences_do_not_satisfy_literal_word_boundaries(tmp_path: Path, legacy: bool) -> None:
    """A legitimate word elsewhere cannot validate an interior-word literal in the body or an attachment."""
    archive = natural_archive(tmp_path, ["CACM XCACM-123 Ham Sandwhich XHam+SandwhichY"],
                              legacy=legacy, attachment="CACM XCACM-123 Ham Sandwhich XHam+SandwhichY")
    for query in ('"CACM-"', '"Ham+Sandwhich"'):
        for attachments in (False, True):
            assert search_page(archive, query, search_attachments=attachments).results == []
            assert search_result_count(archive, parse_query(query), search_attachments=attachments) == 0


def test_selectors_and_suggestions_share_normalization(tmp_path: Path) -> None:
    """Full text, subjects and header names use the same accent/sharp-s equivalence."""
    archive = natural_archive(tmp_path, ["body"], legacy=True)
    assert len(search_page(archive, 'subject:"CAFE STRASSE"').results) == 1
    assert len(search_page(archive, 'from:"JOSE STRASSE"').results) == 1
    assert any(item.message_count == 1 for item in search_suggestions(archive, "subject:CAFE").items)
    assert any(item.message_count == 1 for item in search_suggestions(archive, "from:STRASSE").items)


def test_large_legacy_phrase_expansion_still_requires_adjacent_words(tmp_path: Path) -> None:
    """Bounded query expansion must not relax a seven-word phrase into an unordered AND."""
    phrase = " ".join(["Straße"] * 7)
    archive = natural_archive(tmp_path, [phrase, "Straße interruption " + phrase], legacy=True)
    # Also index the folded spelling so seven positions each have two native alternatives.
    query = '"' + " ".join(["STRASSE"] * 7) + '"'
    assert {row.message_pk for row in search_page(archive, query).results} == {1, 2}
    interrupted = '"' + " ".join(["STRASSE"] * 8) + '"'
    assert search_page(archive, interrupted).results == []


def test_vocabulary_cache_tracks_index_updates_and_atomic_replacement(tmp_path: Path) -> None:
    """An index's changed contents or inode cannot reuse stale legacy token alternatives."""
    archive = natural_archive(tmp_path, ["body"], legacy=True)
    assert search_page(archive, "unusualstrasse").results == []
    path = archive / "search.sqlite3"
    with sqlite3.connect(path) as database:
        database.execute("UPDATE message_fts SET content=content || ' unusualstraße'")
    assert len(search_page(archive, "unusualstrasse").results) == 1
    replacement = tmp_path / "replacement.sqlite3"
    with sqlite3.connect(path) as original, sqlite3.connect(replacement) as copied:
        original.backup(copied)
        copied.execute("UPDATE message_fts SET content=content || ' anotherstraße'")
    replacement.replace(path)
    assert len(search_page(archive, "anotherstrasse").results) == 1


def test_cli_query_preserves_literal_quotation(tmp_path: Path) -> None:
    """The CLI accepts intact search-box syntax despite the shell consuming outer quotes."""
    archive = natural_archive(tmp_path, ["Ham Sandwhich", "Ham+Sandwhich"], legacy=True)
    result = run_search("--archive", str(archive), "--query", '"Ham+Sandwhich"')
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("2 ")
    assert "\n1 " not in result.stdout
