# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Shared search normalization for indexed text, queries, selectors and literals.

Purpose: Match natural text without changing preserved mail, headers or previews.
How it works:
Case folding and accent removal share one contract. Writers fold content before
FTS indexing and mark the index; queries fold words the same way, so a plain FTS
phrase is exact. Quoted symbols are checked in candidate text by ``search_literal``.
Fundamental dependents:
- ``mailsearch``: content queries and literal checks.
- ``search_selectors``: names and addresses use the same normalization contract.
"""
from __future__ import annotations

import re
import sqlite3
import unicodedata


class _AccentMap(dict[int, str]):
    """Cache character translations for the native str.translate API."""

    def __missing__(self, key: int) -> str:
        character = chr(key)
        result = "" if unicodedata.category(character) == "Mn" else character
        self[key] = result
        return result


_ACCENTS = _AccentMap()


def search_fold(value: str | None) -> str:
    """Ignore case and combining accents, expanding ß/ẞ to ss."""
    value = value or ""
    if value.isascii():
        return value.lower()
    return unicodedata.normalize("NFD", value.casefold()).translate(_ACCENTS)


def search_words(value: str) -> list[str]:
    """Split normalized text at the word separators used by unicode61."""
    return re.findall(r"(?:[^\W_]|[\ue000-\uf8ff\U000f0000-\U000ffffd\U00100000-\U0010fffd])+", value)


def has_symbols(value: str) -> bool:
    return any(not character.isspace() and unicodedata.category(character)[0] not in "LNM"
               and unicodedata.category(character) != "Co" for character in value)


def quote_fts(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def search_literal(content: str, literal: str) -> bool:
    """Require word boundaries at the actual normalized literal occurrence."""
    content, literal = search_fold(content), search_fold(literal)
    if not literal:
        return False
    offset = content.find(literal)
    while offset >= 0:
        end = offset + len(literal)
        starts_inside = bool(offset and search_words(literal[0]) and search_words(content[offset - 1]))
        ends_inside = bool(end < len(content) and search_words(literal[-1]) and search_words(content[end]))
        if not starts_inside and not ends_inside:
            return True
        offset = content.find(literal, offset + 1)
    return False


def register_search_functions(database: sqlite3.Connection) -> None:
    database.create_function("search_fold", 1, search_fold, deterministic=True)
    database.create_function("search_literal", 2, search_literal, deterministic=True)
