# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Shared search normalization and compatibility with existing word indexes.

Purpose: Match natural text without changing preserved mail, headers or previews.
How it works:
Case folding and accent removal share one contract; legacy FTS vocabulary and
native positions preserve word boundaries without rebuilding existing indexes.
Fundamental dependents:
- ``mailsearch``: content queries require compatible vocabulary and literal checks.
- ``search_selectors``: names and addresses use the same normalization contract.
"""
from __future__ import annotations

import re
import sqlite3
import threading
import unicodedata
from collections import OrderedDict
from itertools import product
from math import prod
from pathlib import Path

from pydantic import BaseModel, ConfigDict

MAX_PHRASE_VARIANTS = 64
_CACHE_LOCK = threading.Lock()


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


class SearchIndexStamp(BaseModel):
    model_config = ConfigDict(frozen=True)
    path: str
    device: int
    inode: int
    size: int
    modified: int
    changed: int

    @classmethod
    def read(cls, path: Path) -> SearchIndexStamp:
        state = path.stat()
        return cls(path=str(path.resolve()), device=state.st_dev, inode=state.st_ino,
                   size=state.st_size, modified=state.st_mtime_ns, changed=state.st_ctime_ns)


class WordVariant(BaseModel):
    normalized: str
    original: str


class SearchVocabulary(BaseModel):
    variants: list[WordVariant]


class CompiledText(BaseModel):
    match: str
    check_phrase: bool = False
    split_match: str = ""


_VOCABULARIES: OrderedDict[tuple[SearchIndexStamp, bool], SearchVocabulary] = OrderedDict()


def search_vocabulary(database: sqlite3.Connection, stamp: SearchIndexStamp | None,
                      attachments: bool) -> SearchVocabulary:
    """Read exceptional native tokens once per unchanged, read-locked index."""
    tables = ("message_fts", "attachment_fts") if attachments else ("message_fts",)
    for table in tables:
        database.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS temp.search_words_{table} USING fts5vocab(search,{table},row)")
    key = (stamp, attachments) if stamp is not None else None
    with _CACHE_LOCK:
        if key is not None and key in _VOCABULARIES:
            _VOCABULARIES.move_to_end(key)
            return _VOCABULARIES[key]
    variants: list[WordVariant] = []
    for table in tables:
        name = f"search_words_{table}"
        for (term,) in database.execute(f"SELECT term FROM {name} WHERE term GLOB '*[^ -~]*'"):
            normalized = search_fold(term)
            if normalized != term:
                variants.append(WordVariant(normalized=normalized, original=term))
    result = SearchVocabulary(variants=variants)
    if key is not None:
        with _CACHE_LOCK:
            _VOCABULARIES[key] = result
            _VOCABULARIES.move_to_end(key)
            while len(_VOCABULARIES) > 8:
                _VOCABULARIES.popitem(last=False)
    return result


def quote_fts(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _split_word(database: sqlite3.Connection, word: str, vocabulary: SearchVocabulary,
                attachments: bool) -> list[str]:
    """Find native fragment phrases through indexed vocabulary lookups."""
    tables = ("message_fts", "attachment_fts") if attachments else ("message_fts",)
    paths: list[tuple[int, tuple[str, ...]]] = [(0, ())]
    phrases: list[str] = []
    explored = 0

    def fallback() -> list[str]:
        prefixes = {word[:end] for end in range(1, len(word))}
        prefixes.update(variant.original for variant in vocabulary.variants
                        if word.startswith(variant.normalized) and len(variant.normalized) < len(word))
        return [quote_fts(prefix) for prefix in sorted(prefixes)]

    while paths:
        explored += 1
        if explored > MAX_PHRASE_VARIANTS:
            return fallback()
        start, parts = paths.pop()
        if start == len(word):
            if len(parts) > 1:
                phrases.append(quote_fts(" ".join(parts)))
            continue
        # Only proper fragments can explain a split native token.
        ends = range(start + 1, len(word) + 1)
        choices = [WordVariant(normalized=word[start:end], original=word[start:end])
                   for end in ends if start or end < len(word)]
        choices.extend(variant for variant in vocabulary.variants
                       if word.startswith(variant.normalized, start)
                       and (start or len(variant.normalized) < len(word)))
        if not choices:
            continue
        present: set[str] = set()
        for table in tables:
            for offset in range(0, len(choices), 500):
                batch = choices[offset:offset + 500]
                placeholders = ",".join("?" for _ in batch)
                present.update(row[0] for row in database.execute(
                    f"SELECT term FROM search_words_{table} WHERE term IN ({placeholders})",
                    [choice.original for choice in batch],
                ))
        paths.extend((start + len(choice.normalized), (*parts, choice.original))
                     for choice in choices if choice.original in present)
        if len(paths) + len(phrases) > MAX_PHRASE_VARIANTS:
            # Exceptionally ambiguous fragmentations retain the conservative
            # compatibility path; ordinary words never scan isolated prefixes.
            return fallback()
    return phrases


def compile_text(value: str, vocabulary: SearchVocabulary,
                 database: sqlite3.Connection, attachments: bool) -> CompiledText:
    words = search_words(search_fold(value))
    options = [sorted({word, *(variant.original for variant in vocabulary.variants
                              if variant.normalized == word)}) for word in words]
    if not words:
        return CompiledText(match="")
    prefixes = [sorted({*(quote_fts(item) for item in items),
                       *_split_word(database, word, vocabulary, attachments)})
                for word, items in zip(words, options, strict=True)]
    split_match = " AND ".join(
        "(" + " OR ".join(items) + ")" for items in prefixes
    ) if any(len(items) > len(native) for items, native in zip(prefixes, options, strict=True)) else ""
    if prod(len(items) for items in options) <= MAX_PHRASE_VARIANTS:
        phrases = [quote_fts(" ".join(items)) for items in product(*options)]
        return CompiledText(match="(" + " OR ".join(phrases) + ")", split_match=split_match)
    return CompiledText(match=" AND ".join(
        "(" + " OR ".join(quote_fts(word) for word in items) + ")" for items in options
    ), check_phrase=True, split_match=split_match)


def search_phrase(content: str, phrase: str) -> bool:
    """Check token adjacency after an exceptionally large FTS phrase expansion."""
    words, wanted = search_words(search_fold(content)), search_words(search_fold(phrase))
    return any(words[index:index + len(wanted)] == wanted for index in range(len(words)))


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


def has_combining_marks(content: str) -> bool:
    return not content.isascii() and any(unicodedata.category(match[0]) == "Mn"
                                        for match in re.finditer(r"[^\x00-\x7f]", content))


def search_split_phrase(content: str, phrase: str) -> bool:
    """Normalize extra candidates only if their original text has combining marks."""
    return has_combining_marks(content) and search_phrase(content, phrase)


def search_native_phrase(content: str, phrase: str) -> bool:
    """Reject native fragments whose accent disappears inside a normalized word."""
    return not has_combining_marks(content) or search_phrase(content, phrase)


def register_search_functions(database: sqlite3.Connection) -> None:
    database.create_function("search_fold", 1, search_fold, deterministic=True)
    database.create_function("search_phrase", 2, search_phrase, deterministic=True)
    database.create_function("search_literal", 2, search_literal, deterministic=True)
    database.create_function("search_split_phrase", 2, search_split_phrase, deterministic=True)
    database.create_function("search_native_phrase", 2, search_native_phrase, deterministic=True)
