# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""Shared selector recognition and indexed SQL for CLI, tiles and completion."""
from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

Tag = Literal["any", "from", "to", "cc", "bcc", "subject", "date", "before", "after"]
ADDRESS_ROLES = ("from", "to", "cc", "bcc")
EVIDENCE_NAME_PATH = "$.name"


class SearchStatement(BaseModel):
    sql: str
    parameters: list[str | int]


def contains(value: str) -> str:
    return "%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def recognize_text(value: str) -> str | None:
    return value.strip().casefold() or None


def recognize_date(value: str) -> str | None:
    """Recognize complete calendar dates without locale or two-digit-year guesses."""
    value = value.strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            selected = date.fromisoformat(value)
        elif match := re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", value):
            month, day, year = map(int, match.groups())
            selected = date(year, month, day)
        elif match := re.fullmatch(r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})", value):
            months = ("january", "february", "march", "april", "may", "june", "july",
                      "august", "september", "october", "november", "december")
            name, day, year = match.groups()
            month = next((index for index, full in enumerate(months, 1)
                          if name.lower() in (full, full[:3])), 0)
            selected = date(int(year), month, int(day))
        else:
            return None
        worldwide_bounds(selected.isoformat())  # Reject dates outside representable bounds.
        return selected.isoformat()
    except (ValueError, OverflowError):
        return None


def worldwide_bounds(value: str) -> tuple[str, str]:
    midnight = datetime.combine(date.fromisoformat(value), datetime.min.time(), UTC)
    return ((midnight - timedelta(hours=14)).isoformat(),
            (midnight + timedelta(days=1, hours=12)).isoformat())


def matching_addresses(value: str, names: bool = False) -> SearchStatement:
    sql = "SELECT address_pk FROM email_addresses WHERE lower(address) LIKE ? ESCAPE '\\'"
    parameters: list[str | int] = [contains(value)]
    if names:
        sql += (" UNION SELECT a.address_pk FROM address_search_names n "
                "JOIN email_addresses a ON a.address=n.address WHERE lower(n.name) LIKE ? ESCAPE '\\'")
        parameters.append(contains(value))
    return SearchStatement(sql=sql, parameters=parameters)


def address_sql(tag: str, value: str, names: bool) -> SearchStatement:
    matching = matching_addresses(value, names)
    if tag == "from":
        return SearchStatement(sql=f"m.sender_address_pk IN ({matching.sql})", parameters=matching.parameters)
    if tag == "any":
        sql = (f"m.message_pk IN (WITH matching AS MATERIALIZED ({matching.sql}) "
               "SELECT sent.message_pk FROM matching "
               "CROSS JOIN messages sent INDEXED BY messages_sender_address_pk "
               "ON sent.sender_address_pk=matching.address_pk UNION "
               "SELECT r.message_pk FROM matching CROSS JOIN recipients r INDEXED BY recipients_address_pk "
               "ON r.address_pk=matching.address_pk)")
        return SearchStatement(sql=sql, parameters=matching.parameters)
    return SearchStatement(
        sql=(f"m.message_pk IN (WITH matching AS MATERIALIZED ({matching.sql}) "
             "SELECT r.message_pk FROM matching CROSS JOIN recipients r INDEXED BY recipients_address_pk "
             "ON r.address_pk=matching.address_pk WHERE r.role=?)"),
        parameters=[*matching.parameters, tag])


def subject_sql(_tag: str, value: str, _names: bool) -> SearchStatement:
    return SearchStatement(sql=("m.message_pk IN (SELECT subject_match.message_pk "
        "FROM messages subject_match INDEXED BY messages_subject_message "
        "WHERE lower(subject_match.subject) LIKE ? ESCAPE '\\')"), parameters=[contains(value)])


def date_sql(tag: str, value: str, _names: bool) -> SearchStatement:
    start, end = worldwide_bounds(value)
    if tag == "date":
        return SearchStatement(sql="m.date_utc >= ? AND m.date_utc < ?", parameters=[start, end])
    if tag == "before":
        return SearchStatement(sql="m.date_utc < ?", parameters=[start])
    return SearchStatement(sql="m.date_utc >= ?", parameters=[end])


class Selector(BaseModel):
    model_config = ConfigDict(frozen=True)
    tag: Tag
    field: str
    label: str
    family: Literal["address", "subject", "date"]
    recognize: Callable[[str], str | None]
    sql: Callable[[str, str, bool], SearchStatement]


SELECTORS = (
    Selector(tag="any", field="any_address", label="Any", family="address", recognize=recognize_text, sql=address_sql),
    Selector(tag="from", field="from_", label="From", family="address", recognize=recognize_text, sql=address_sql),
    Selector(tag="to", field="to", label="To", family="address", recognize=recognize_text, sql=address_sql),
    Selector(tag="cc", field="cc", label="Cc", family="address", recognize=recognize_text, sql=address_sql),
    Selector(tag="bcc", field="bcc", label="Bcc", family="address", recognize=recognize_text, sql=address_sql),
    Selector(tag="subject", field="subject", label="Subject", family="subject", recognize=recognize_text, sql=subject_sql),
    Selector(tag="date", field="date", label="Date", family="date", recognize=recognize_date, sql=date_sql),
    Selector(tag="before", field="before", label="Before", family="date", recognize=recognize_date, sql=date_sql),
    Selector(tag="after", field="after", label="After", family="date", recognize=recognize_date, sql=date_sql),
)


def selector_for(tag: str) -> Selector | None:
    return next((spec for spec in SELECTORS if spec.tag == tag.lower()), None)


def prepare_names(database: sqlite3.Connection, archive: Path) -> None:
    """Read live identity edits and header names without rebuilding the content index."""
    sources = ["SELECT address, display_name AS name FROM search.address_suggestions"]
    if (archive / "processing.sqlite3").is_file():
        database.execute("ATTACH DATABASE ? AS identities", (f"file:{archive / 'processing.sqlite3'}?mode=ro",))
        sources.extend((
            "SELECT a.address,p.canonical_name FROM identities.addresses a "
            "JOIN identities.person_addresses pa USING(address_id) JOIN identities.persons p USING(person_id)",
            "SELECT a.address,n.name FROM identities.addresses a "
            "JOIN identities.person_addresses pa USING(address_id) JOIN identities.person_aliases n USING(person_id)",
            f"SELECT a.address,json_extract(e.value,'{EVIDENCE_NAME_PATH}') FROM identities.addresses a "
            "JOIN identities.evidence e USING(address_id) WHERE e.kind='header'",
        ))
    database.execute("CREATE TEMP VIEW address_search_names AS " + " UNION ".join(sources))
