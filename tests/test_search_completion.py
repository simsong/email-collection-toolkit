# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""Search requirements: live names, roles before content indexing, and worldwide dates."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from mailarchiver.__main__ import IngestRequest, run_ingest
from mailarchiver.gui_app import IdentityPickerApi
from mailarchiver.gui_processing import PickerPage, unfinished_work
from mailarchiver.gui_service import search_page, search_suggestions
from mailarchiver.mailsearch import parse_query, search_result_count
from mailarchiver.owner_rules import OwnerRules
from mailarchiver.search_selectors import recognize_date, worldwide_bounds
from mailarchiver.search_completion import completion_input


@pytest.mark.parametrize("query,expected", [
    ('subject:"Notes from:simson"', ("", "subject", "Notes from:simson")),
    ('from:other subject:"Annual report', ("from:other", "subject", "Annual report")),
    ('from:other simson', ("from:other", "", "simson")),
    ('date:"January 5, 2020"', ("", "date", "January 5, 2020")),
    ('January 5, 2020', ("", "", "January 5, 2020")),
])
def test_completion_preserves_query_syntax(query: str, expected: tuple[str, str, str]) -> None:
    """A colon within quoted text is not a new selector; only the active term is replaced."""
    assert completion_input(query) == expected


def completion_archive(root: Path) -> Path:
    source = root / "source"
    source.mkdir()
    headers = (
        "From: Simson Header <alpha@example.test>\nTo: Simson Header <alpha@example.test>\n",
        "From: Other <other@example.test>\nCc: Simson Header <alpha@example.test>\n",
        "From: Other <other@example.test>\nBcc: Hidden Name <hidden@example.test>\n",
    )
    for number, header in enumerate(headers):
        (source / f"{number}.eml").write_text(header + f"Message-ID: <completion-{number}@example.test>\n"
            "Date: Sun, 05 Jan 2020 22:00:00 -0500\nSubject: Simson subject\n\nbody\n")
    archive = root / "archive"
    run_ingest(IngestRequest(archive=archive, roots=[str(source)], scan_policy="not-scanned",
                            owner_rules=OwnerRules(include=["owner@example.test"]), continue_content=False), terminal=False)
    return archive


def test_completions_use_header_roles_and_live_authority_before_content(tmp_path: Path) -> None:
    """One Any lookup supplies deduplicated roles; header/canonical names work before content."""
    archive = completion_archive(tmp_path)
    assert unfinished_work(archive).content
    for short in ("si", "from:si", "subject:si", "date:1/"):
        assert not search_suggestions(archive, short).items
    suggestions = search_suggestions(archive, "simson")
    aggregate = suggestions.items[0]
    assert aggregate.tag == "any" and aggregate.value == "simson" and aggregate.message_count == 2
    assert [(entry.tag, entry.message_count) for entry in aggregate.choices] == [
        ("any", 2), ("from", 1), ("to", 1), ("cc", 1)]
    for tag, expected in (("any", 2), ("from", 1), ("to", 1), ("cc", 1), ("bcc", 0)):
        assert search_result_count(archive, parse_query(f"{tag}:simson")) == expected
        items = search_suggestions(archive, f"{tag}:simson").items
        assert all(item.tag == tag for item in items)
        assert bool(items) == bool(expected)
    hidden = search_suggestions(archive, "Hidden").items[0]
    assert hidden.tag == "bcc" and hidden.message_count == 1
    picker = IdentityPickerApi(archive, "name")
    page = PickerPage.model_validate(picker.query({"mailbox": "alpha"}))
    picker.update({"operation": "rename-person", "subject": page.groups[0].id, "name": "Authoritative Person"})
    for text in ("Authoritative", "Simson", "alpha@example"):
        result = search_suggestions(archive, text).items[0]
        assert result.message_count == 2
        assert search_result_count(archive, parse_query(f'any:"{text}"')) == 2
    # Multi-field queries retain preceding terms when a completion becomes a tile.
    compound = search_suggestions(archive, 'subject:Simson from:"Authoritative Person"')
    assert compound.prefix == "subject:Simson"
    assert compound.items[0].tag == "from" and compound.items[0].message_count == 1
    address_id = page.groups[0].addresses[0].address_id
    picker.update({"operation": "separate-address", "subject": address_id})
    separated = PickerPage.model_validate(picker.query({"mailbox": "alpha"}))
    picker.update({"operation": "rename-person", "subject": separated.groups[0].id, "name": "Moved Person"})
    assert search_result_count(archive, parse_query("from:simson")) == 1
    assert search_suggestions(archive, "Moved").items[0].message_count == 2


@pytest.mark.parametrize("value", ["2020-01-05", "1/5/2020", "January 5, 2020", "Jan 5, 2020"])
def test_worldwide_dates_share_parser_completions_and_results(tmp_path: Path, value: str) -> None:
    """The Boston 10 p.m. message belongs to January 5 with every supported spelling."""
    archive = completion_archive(tmp_path)
    query = f'date:"{value}"'
    items = search_suggestions(archive, value).items
    dates = [item for item in items if item.tag in ("date", "before", "after")]
    assert [(item.tag, item.value, item.message_count) for item in dates] == [
        ("date", "2020-01-05", 3), ("before", "2020-01-05", 0), ("after", "2020-01-05", 0)]
    assert len(search_page(archive, query).results) == 3
    assert search_result_count(archive, parse_query(query)) == 3
    assert [item.tag for item in search_suggestions(archive, query).items] == ["date"]


@pytest.mark.parametrize("value,start,end", [
    ("2020-01-05", "2020-01-04T10:00:00+00:00", "2020-01-06T12:00:00+00:00"),
    ("2020-02-29", "2020-02-28T10:00:00+00:00", "2020-03-01T12:00:00+00:00"),
    ("2020-01-01", "2019-12-31T10:00:00+00:00", "2020-01-02T12:00:00+00:00"),
])
def test_worldwide_date_boundaries(tmp_path: Path, value: str, start: str, end: str) -> None:
    """Exact endpoints partition before/date/after; adjacent daily searches overlap."""
    from datetime import datetime, timedelta

    archive = completion_archive(tmp_path)
    assert worldwide_bounds(value) == (start, end)
    with sqlite3.connect(archive / "archive.sqlite3") as database:
        for number, stamp in enumerate((start, end, (datetime.fromisoformat(start) - timedelta(seconds=1)).isoformat()), 1):
            database.execute("UPDATE messages SET date_utc=? WHERE message_pk=?", (stamp, number))
    assert [entry.message_pk for entry in search_page(archive, f"date:{value}").results] == [1]
    assert [entry.message_pk for entry in search_page(archive, f"before:{value}").results] == [3]
    assert [entry.message_pk for entry in search_page(archive, f"after:{value}").results] == [2]
    previous = (datetime.fromisoformat(value) - timedelta(days=1)).date().isoformat()
    assert 1 in [entry.message_pk for entry in search_page(archive, f"date:{previous}").results]


@pytest.mark.parametrize("value", ["2021-02-29", "13/1/2020", "1/5/20", "January 32, 2020", "simson"])
def test_invalid_dates_have_no_date_completions(tmp_path: Path, value: str) -> None:
    archive = completion_archive(tmp_path)
    assert recognize_date(value) is None
    assert not any(item.tag in ("date", "before", "after") for item in search_suggestions(archive, value).items)
    assert search_page(archive, f'date:"{value}"').error
