# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""Complete selectors from catalog roles and live names, before content indexing."""
from __future__ import annotations

import re
import shlex
import sqlite3
from pathlib import Path

from pydantic import BaseModel, Field

from .search import SEARCH_CATEGORIES
from .search_selectors import (
    ADDRESS_ROLES, SELECTORS, Tag, matching_addresses, prepare_names, selector_for,
)


class TagChoice(BaseModel):
    tag: Tag
    label: str
    message_count: int


class Completion(BaseModel):
    tag: Tag
    value: str
    label: str
    message_count: int
    choices: list[TagChoice]


class SearchSuggestions(BaseModel):
    query: str
    prefix: str = ""
    items: list[Completion] = Field(default_factory=list)


class AddressMatch(BaseModel):
    address: str
    choices: list[TagChoice] = Field(default_factory=list)
    last_seen: str = ""


def completion_input(query: str) -> tuple[str, str, str]:
    """Preserve preceding terms; allow unfinished quotes while typing a selector."""
    tokens = list(re.finditer(r'''(?:[^\s"'\\]+|\\.|"(?:\\.|[^"\\])*"?|'[^']*'?)+''', query))
    prefix, tag, value = "", "", query.strip()
    if tokens and any(selector_for(token[0].partition(":")[0]) for token in tokens if ":" in token[0]):
        last = tokens[-1]
        prefix = query[:last.start()].strip()
        key, separator, tail = last[0].partition(":")
        if separator and selector_for(key):
            tag, value = key.lower(), tail
        else:
            value = last[0]
    try:
        value = " ".join(shlex.split(value))
    except ValueError:
        value = value.strip("\"'")
    return prefix, tag, value


def choice(tag: str, count: int) -> TagChoice:
    spec = selector_for(tag)
    assert spec is not None
    return TagChoice(tag=spec.tag, label=spec.label, message_count=count)


def address_completions(database: sqlite3.Connection, value: str, tag: str, limit: int) -> list[Completion]:
    """One Any query supplies distinct role counts, aggregate counts and ranked addresses."""
    matching = matching_addresses(value.casefold(), names=True)
    rows = database.execute(
        f"WITH matching AS MATERIALIZED ({matching.sql}), hits AS MATERIALIZED ("
        "SELECT a.address_pk,'from' AS role,m.message_pk,m.date_utc FROM matching a "
        "CROSS JOIN messages m INDEXED BY messages_sender_address_pk ON m.sender_address_pk=a.address_pk "
        "WHERE m.category IN (?,?) UNION ALL "
        "SELECT a.address_pk,r.role,m.message_pk,m.date_utc FROM matching a "
        "CROSS JOIN recipients r INDEXED BY recipients_address_pk USING(address_pk) "
        "JOIN messages m USING(message_pk) WHERE m.category IN (?,?)), "
        "counts AS (SELECT address_pk,role,count(DISTINCT message_pk) AS n,max(date_utc) AS seen "
        "FROM hits GROUP BY address_pk,role UNION ALL "
        "SELECT address_pk,'any',count(DISTINCT message_pk),max(date_utc) FROM hits GROUP BY address_pk UNION ALL "
        "SELECT NULL,role,count(DISTINCT message_pk),max(date_utc) FROM hits GROUP BY role UNION ALL "
        "SELECT NULL,'any',count(DISTINCT message_pk),max(date_utc) FROM hits) "
        ", ranked AS (SELECT address_pk FROM counts WHERE address_pk IS NOT NULL AND role=? "
        "ORDER BY n DESC,seen DESC,address_pk LIMIT ?) "
        "SELECT COALESCE(a.address,''),c.role,c.n,c.seen FROM counts c "
        "LEFT JOIN email_addresses a USING(address_pk) WHERE c.n > 0 "
        "AND (c.address_pk IS NULL OR c.address_pk IN (SELECT address_pk FROM ranked))",
        (*matching.parameters, *SEARCH_CATEGORIES, *SEARCH_CATEGORIES, tag or "any", limit))
    matches: dict[str, AddressMatch] = {}
    for address, role, count, seen in rows:
        entry = matches.setdefault(address, AddressMatch(address=address))
        entry.choices.append(choice(role, count))
        entry.last_seen = max(entry.last_seen, seen)
    result: list[Completion] = []
    for entry in matches.values():
        roles = [role for role in ADDRESS_ROLES if any(item.tag == role for item in entry.choices)]
        selected = tag or (roles[0] if len(roles) == 1 else "any")
        current = next((item for item in entry.choices if item.tag == selected), None)
        if current is None:
            continue
        available = [item for item in entry.choices if item.tag in roles or item.tag == "any"]
        available.sort(key=lambda item: ("any", *ADDRESS_ROLES).index(item.tag))
        result.append(Completion(tag=current.tag, value=entry.address or value, label=entry.address or value,
                                 message_count=current.message_count, choices=available))
    result.sort(key=lambda item: (item.value == value, item.message_count,
                                matches.get(item.value, matches.get("", AddressMatch(address=""))).last_seen,
                                item.value), reverse=True)
    return result[:limit + 1]


def search_suggestions(archive: Path, query: str, limit: int = 20) -> SearchSuggestions:
    if not 1 <= limit <= 50:
        raise ValueError("suggestion limit must be between 1 and 50")
    prefix, tag, value = completion_input(query)
    result = SearchSuggestions(query=query, prefix=prefix)
    if len(value) < 3:
        return result
    database = sqlite3.connect(f"file:{archive / 'archive.sqlite3'}?mode=ro", uri=True)
    try:
        database.execute("ATTACH DATABASE ? AS search", (f"file:{archive / 'search.sqlite3'}?mode=ro",))
        prepare_names(database, archive)
        spec = selector_for(tag) if tag else None
        if spec is None or spec.family == "address":
            result.items.extend(address_completions(database, value, tag, limit))
        if spec is None or spec.family == "subject":
            subject = selector_for("subject")
            assert subject is not None
            predicate = subject.sql(subject.tag, value.casefold(), False)
            count = database.execute(f"SELECT count(*) FROM messages m WHERE m.category IN (?,?) AND {predicate.sql}",
                                     (*SEARCH_CATEGORIES, *predicate.parameters)).fetchone()[0]
            result.items.append(Completion(tag="subject", value=value, label=f"Subject contains “{value}”",
                                           message_count=count, choices=[choice("subject", count)]))
            rows = database.execute(
                f"SELECT m.subject,count(*) FROM messages m WHERE m.category IN (?,?) AND {predicate.sql} "
                "GROUP BY m.subject ORDER BY count(*) DESC,lower(m.subject) LIMIT ?",
                (*SEARCH_CATEGORIES, *predicate.parameters, limit))
            for text, count in rows:
                result.items.append(Completion(tag="subject", value=text, label=text,
                                               message_count=count, choices=[choice("subject", count)]))
        if spec is None or spec.family == "date":
            dates = [item for item in SELECTORS if item.family == "date"]
            normalized = dates[0].recognize(value)
            if normalized:
                options = []
                for date_spec in dates:
                    predicate = date_spec.sql(date_spec.tag, normalized, False)
                    count = database.execute(f"SELECT count(*) FROM messages m WHERE m.category IN (?,?) AND {predicate.sql}",
                                             (*SEARCH_CATEGORIES, *predicate.parameters)).fetchone()[0]
                    options.append(choice(date_spec.tag, count))
                for option in options:
                    if not tag or option.tag == tag:
                        result.items.append(Completion(tag=option.tag, value=normalized, label=normalized,
                                                       message_count=option.message_count, choices=options))
        return result
    finally:
        database.close()
