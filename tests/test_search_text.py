# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Exercise natural search against real disposable catalogs and FTS databases.
# Messages are indexed by the current writer, which folds case and accents.
# Quotes retain symbols while ordinary phrases retain word boundaries.
# Unicode normalization must agree across content, selectors and attachments.
# Counts and ordered pages must exclude rejected candidates before their limits.
# An index without the folded-content marker must be rebuilt before searching.
"""Requirements: natural normalized search, literal quoted symbols, rebuild of old indexes."""
from __future__ import annotations

import hashlib
from contextlib import closing
import sqlite3
from email.header import Header
from pathlib import Path
from typing import Any, cast

import pytest

from mailarchiver.bagit import initialize_bag
from mailarchiver.catalog import address_pk, create_catalog, create_search
from mailarchiver.gui_service import search_page, search_suggestions
from mailarchiver.mailsearch import SortDirection, SortField, parse_query, search_headers, search_result_count
from mailarchiver.search import index_message
from mailarchiver import mailsearch
from mailarchiver.catalog import UnsupportedSearchSchemaError
from mailarchiver.gui_service import search_count
from test_mailsearch import add_catalogued_message, run_search


def natural_archive(root: Path, bodies: list[str], *, attachment: str = "") -> Path:
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
            messages.append(raw)
        catalog.commit()
        search.commit()
    finally:
        catalog.close()
        search.close()
    for number, raw in enumerate(messages, 1):
        add_catalogued_message(archive, number, raw)
    return archive


def test_normalization_matches_case_accents_and_sharp_s(tmp_path: Path) -> None:
    """Case, composed/decomposed accents, sharp s and native accent exceptions agree."""
    archive = natural_archive(tmp_path, ["Hám+Sandwhích CACM-123 a\u0301bc Straße ẞtraße hộp Καλημέρα"])
    files = [archive / "search.sqlite3", archive / "data/mbox/2024-Archive1.mbox"]
    before = [hashlib.sha256(path.read_bytes()).hexdigest() for path in files]
    for query in ('"HAM+SANDWHICH"', '"cacm-"', "ABC", "STRASSE", "straße", "sstrasse", "hop", "ΚΑΛΗΜΕΡΑ"):
        assert len(search_page(archive, query).results) == 1, query
    assert [hashlib.sha256(path.read_bytes()).hexdigest() for path in files] == before
    assert search_page(archive, "connect").results == []


def test_quotes_preserve_symbols_without_changing_unquoted_word_search(tmp_path: Path) -> None:
    """A quoted plus is literal; spaces and other separators must not substitute for it."""
    archive = natural_archive(tmp_path, ["HÁM+SANDWHICH", "Ham Sandwhich", "Ham-Sandwhich",
                                         "Ham+delicious+Sandwhich", "Ham+Sandwhich extra"])
    assert [row.message_pk for row in search_page(archive, '"Ham+Sandwhich"').results] == [5, 1]
    assert [row.message_pk for row in search_page(archive, "Ham+Sandwhich").results] == [5, 3, 2, 1]
    assert [row.message_pk for row in search_page(archive, '"Ham Sandwhich"').results] == [5, 3, 2, 1]
    assert [row.message_pk for row in search_page(archive, "'Ham+Sandwhich'").results] == [5, 1]


def test_decomposed_non_latin_accents_match_whole_words(tmp_path: Path) -> None:
    """Decomposed non-Latin combining accents fold away inside whole words."""
    archive = natural_archive(tmp_path, ["Καλημε\u0301ρα שָׁלוֹם", "ΚαλημεραSuffix"])
    for query in ("ΚΑΛΗΜΕΡΑ", "שלום"):
        assert [row.message_pk for row in search_page(archive, query).results] == [1]


def test_decomposed_latin_accents_match_in_bodies_and_attachments(tmp_path: Path) -> None:
    """A combining dotted grave accent can split Latin words in an old native index."""
    archive = natural_archive(tmp_path, ["co\u1dc0operate", "co operate"],
                              attachment="re\u1dc0sume+send")
    assert [row.message_pk for row in search_page(archive, "cooperate").results] == [1]
    assert search_result_count(archive, parse_query("cooperate")) == 1
    assert search_page(archive, '"resume+send"').results == []
    assert len(search_page(archive, '"resume+send"', search_attachments=True).results) == 2


def test_word_fragments_around_removed_accents_do_not_match(tmp_path: Path) -> None:
    """Removed accents leave one folded word, not fragments or a two-word phrase."""
    archive = natural_archive(tmp_path, ["co\u1dc0operate a\u1dc0bc"],
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
    archive = natural_archive(tmp_path, bodies)
    assert [row.message_pk for row in search_page(archive, query).results] == expected


def test_postfilter_precedes_counts_limits_offsets_and_all_orderings(tmp_path: Path) -> None:
    """Rejected newer candidates cannot hide older literal matches or inflate counts."""
    archive = natural_archive(tmp_path, ["café+straße", "cafe strasse", "café+straße", "cafe strasse"])
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


def test_attachment_terms_keep_their_own_literal_predicates(tmp_path: Path) -> None:
    """A term can match body or opted-in attachment text, with literals checked in that same table."""
    archive = natural_archive(tmp_path, ["alpha café strasse"], attachment="Café+Straße")
    query = 'alpha "CAFE+STRASSE"'
    assert search_page(archive, query).results == []
    assert len(search_page(archive, query, search_attachments=True).results) == 1
    assert search_result_count(archive, parse_query(query), search_attachments=True) == 1
    assert search_page(archive, '"alpha+Café"', search_attachments=True).results == []


def test_separate_occurrences_do_not_satisfy_literal_word_boundaries(tmp_path: Path) -> None:
    """A legitimate word elsewhere cannot validate an interior-word literal in the body or an attachment."""
    archive = natural_archive(tmp_path, ["CACM XCACM-123 Ham Sandwhich XHam+SandwhichY"], attachment="CACM XCACM-123 Ham Sandwhich XHam+SandwhichY")
    for query in ('"CACM-"', '"Ham+Sandwhich"'):
        for attachments in (False, True):
            assert search_page(archive, query, search_attachments=attachments).results == []
            assert search_result_count(archive, parse_query(query), search_attachments=attachments) == 0


def test_selectors_and_suggestions_share_normalization(tmp_path: Path) -> None:
    """Full text, subjects and header names use the same accent/sharp-s equivalence."""
    archive = natural_archive(tmp_path, ["body"])
    assert len(search_page(archive, 'subject:"CAFE STRASSE"').results) == 1
    assert len(search_page(archive, 'from:"JOSE STRASSE"').results) == 1
    assert any(item.message_count == 1 for item in search_suggestions(archive, "subject:CAFE").items)
    assert any(item.message_count == 1 for item in search_suggestions(archive, "from:STRASSE").items)


def test_long_phrases_require_adjacent_words(tmp_path: Path) -> None:
    """Bounded query expansion must not relax a seven-word phrase into an unordered AND."""
    phrase = " ".join(["Straße"] * 7)
    archive = natural_archive(tmp_path, [phrase, "Straße interruption " + phrase])
    # Also index the folded spelling so seven positions each have two native alternatives.
    query = '"' + " ".join(["STRASSE"] * 7) + '"'
    assert {row.message_pk for row in search_page(archive, query).results} == {1, 2}
    interrupted = '"' + " ".join(["STRASSE"] * 8) + '"'
    assert search_page(archive, interrupted).results == []


def test_cli_query_preserves_literal_quotation(tmp_path: Path) -> None:
    """The CLI accepts intact search-box syntax despite the shell consuming outer quotes."""
    archive = natural_archive(tmp_path, ["Ham Sandwhich", "Ham+Sandwhich"])
    result = run_search("--archive", str(archive), "--query", '"Ham+Sandwhich"')
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("2 ")
    assert "\n1 " not in result.stdout


def test_date_walk_for_frequent_words_matches_sorted_hits(tmp_path: Path) -> None:
    """Frequent-word pages walked in date order equal pages from sorting every hit, at every offset."""
    bodies = ["co᷀operate alpha", "cooperate", "co operate", "alpha co᷀operate beta", "a᷀bc",
              "abc cooperate", "unrelated", "COOPÉRATE", "co᷀operate"]
    archive = natural_archive(tmp_path, bodies)

    def pages(query: str) -> list[list[int]]:
        return [[row.message_pk for row in search_page(archive, query, offset=offset, limit=2).results]
                for offset in range(0, len(bodies) + 1, 2)]

    for query in ("cooperate", "abc", "alpha cooperate", "co", '"co operate"'):
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(mailsearch, "_frequent_text", lambda *_args: False)
            sorted_hits = pages(query)
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(mailsearch, "DATE_WALK_MINIMUM_HITS", 1)
            walked = pages(query)
        assert walked == sorted_hits, query
    assert pages("cooperate")[0] == [9, 8]




def test_index_without_folded_marker_requires_rebuild(tmp_path: Path) -> None:
    """Search of an older index asks for a rebuild instead of returning incomplete results."""
    archive = natural_archive(tmp_path, ["Straße"])
    path = archive / "search.sqlite3"
    with closing(sqlite3.connect(path)) as database, database:
        database.execute("PRAGMA user_version = 0")
    page = search_page(archive, "strasse")
    assert page.rebuild_required and page.results == [] and "rebuilt" in (page.error or "")
    assert search_count(archive, "strasse").rebuild_required
    with pytest.raises(UnsupportedSearchSchemaError):
        create_search(path).close()


def test_desktop_search_of_old_index_offers_rebuild(tmp_path: Path) -> None:
    """The window's search asks the application for the rebuild pop-up."""
    from mailarchiver import gui_app

    archive = natural_archive(tmp_path, ["body"])
    with closing(sqlite3.connect(archive / "search.sqlite3")) as database, database:
        database.execute("PRAGMA user_version = 0")
    offers: list[str] = []

    class Application:
        def offer_rebuild(self, _api: object, reason: str) -> bool:
            offers.append(reason)
            return True

    api = gui_app.GuiApi(archive, application=cast(Any, Application()))
    assert api.search("body")["rebuild_required"] is True
    assert len(offers) == 1 and "rebuilt" in offers[0]
