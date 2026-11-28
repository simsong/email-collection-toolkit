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

import hashlib
import os
import re
import sqlite3
import sys
import tempfile
import threading
import unicodedata
from collections import OrderedDict
from itertools import product
from math import prod
from pathlib import Path

from pydantic import BaseModel, ConfigDict, PrivateAttr

MAX_PHRASE_VARIANTS = 64
VOCABULARY_CACHE_ENV = "MAILARCHIVER_SEARCH_VOCABULARY_CACHE"
STORED_VOCABULARIES = 8
_CACHE_LOCK = threading.Lock()
_SCAN_LOCK = threading.Lock()


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
    _originals: dict[str, list[str]] | None = PrivateAttr(default=None)


class CompiledText(BaseModel):
    match: str
    check_phrase: bool = False
    split_match: str = ""


_VOCABULARIES: OrderedDict[tuple[SearchIndexStamp, bool], SearchVocabulary] = OrderedDict()


def vocabulary_cache_directory() -> Path:
    """Locate the per-user cache of legacy vocabulary scans (never inside an archive)."""
    configured = os.environ.get(VOCABULARY_CACHE_ENV)
    if configured:
        return Path(configured)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "mailarchiver" / "search-vocabulary"
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "mailarchiver" / "search-vocabulary"


def index_lineage(path: Path) -> tuple[str, int, int, int]:
    """Identify one index file across in-place writes; a rebuild's replacement file differs."""
    state = path.stat()
    born = getattr(state, "st_birthtime_ns", None) or int(getattr(state, "st_birthtime", 0) * 1e9)
    return str(path.resolve()), state.st_dev, state.st_ino, born


def _stored_vocabulary_path(stamp: SearchIndexStamp, attachments: bool) -> Path:
    """Key stored scans by file lineage, not by every write to that file.

    Current writers fold content before indexing it, so in-place writes add no
    exceptional spellings: a scan of an earlier state of the same file is a
    superset of what the file can now contain, and extra alternatives match
    nothing. A replaced file (rebuild or restore) has a new lineage and is rescanned.
    """
    try:
        lineage = index_lineage(Path(stamp.path))
    except OSError:
        lineage = (stamp.path, stamp.device, stamp.inode, 0)
    identity = repr(lineage) + ("+attachments" if attachments else "")
    return vocabulary_cache_directory() / f"{hashlib.sha256(identity.encode()).hexdigest()}.json"


def _read_stored_vocabulary(path: Path) -> SearchVocabulary | None:
    try:
        return SearchVocabulary.model_validate_json(path.read_bytes())
    except (OSError, ValueError):
        return None


def _write_stored_vocabulary(path: Path, vocabulary: SearchVocabulary) -> None:
    """Persist a scan atomically; an unwritable cache only costs a later rescan."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, suffix=".tmp", delete=False) as handle:
            handle.write(vocabulary.model_dump_json().encode())
        Path(handle.name).replace(path)
        # Each rebuilt or restored index has a new lineage; keep only the most recent scans.
        stored = sorted(path.parent.glob("*.json"), key=lambda item: item.stat().st_mtime_ns, reverse=True)
        for stale in stored[STORED_VOCABULARIES:]:
            stale.unlink(missing_ok=True)
    except OSError:
        return


def _scan_terms(database: sqlite3.Connection, tables: tuple[str, ...]) -> list[WordVariant]:
    """Read every native token whose spelling changes under folding (a full vocabulary scan)."""
    variants: list[WordVariant] = []
    for table in tables:
        name = f"search_words_{table}"
        for (term,) in database.execute(f"SELECT term FROM {name} WHERE term GLOB '*[^ -~]*'"):
            normalized = search_fold(term)
            if normalized != term:
                variants.append(WordVariant(normalized=normalized, original=term))
    return variants


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
    with _SCAN_LOCK:
        return _scan_vocabulary(database, stamp, attachments, tables, key)


def _scan_vocabulary(database: sqlite3.Connection, stamp: SearchIndexStamp | None, attachments: bool,
                     tables: tuple[str, ...], key: tuple[SearchIndexStamp, bool] | None) -> SearchVocabulary:
    """Serialize scans so a background warm-up and a search never scan the same index twice."""
    with _CACHE_LOCK:
        if key is not None and key in _VOCABULARIES:
            _VOCABULARIES.move_to_end(key)
            return _VOCABULARIES[key]
    stored = _stored_vocabulary_path(stamp, attachments) if stamp is not None else None
    result = _read_stored_vocabulary(stored) if stored is not None else None
    if result is None:
        result = SearchVocabulary(variants=_scan_terms(database, tables))
        if stored is not None:
            _write_stored_vocabulary(stored, result)
    if key is not None:
        with _CACHE_LOCK:
            _VOCABULARIES[key] = result
            _VOCABULARIES.move_to_end(key)
            while len(_VOCABULARIES) > 8:
                _VOCABULARIES.popitem(last=False)
    return result


def quote_fts(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _variant_originals(vocabulary: SearchVocabulary) -> dict[str, list[str]]:
    """Index native spellings by normalized form once per cached vocabulary."""
    index = vocabulary._originals
    if index is None:
        index = {}
        for variant in vocabulary.variants:
            index.setdefault(variant.normalized, []).append(variant.original)
        vocabulary._originals = index
    return index


def _present_terms(database: sqlite3.Connection, terms: set[str], attachments: bool) -> set[str]:
    """Return the native tokens present in the searched FTS vocabularies."""
    tables = ("message_fts", "attachment_fts") if attachments else ("message_fts",)
    ordered = sorted(terms)
    present: set[str] = set()
    for table in tables:
        for offset in range(0, len(ordered), 500):
            batch = ordered[offset:offset + 500]
            placeholders = ",".join("?" for _ in batch)
            present.update(row[0] for row in database.execute(
                f"SELECT term FROM search_words_{table} WHERE term IN ({placeholders})", batch,
            ))
    return present


def _split_word(database: sqlite3.Connection, word: str, vocabulary: SearchVocabulary,
                attachments: bool) -> str:
    """Match legacy tokens split by combining accents with adjacent fragment pairs.

    Any split of ``word`` into two or more native fragments begins with an
    adjacent pair (proper prefix, next fragment) and ends with an adjacent pair
    (fragment, proper suffix). Requiring one of each keeps the candidate set
    exact enough for FTS to prune it, using O(len(word)^2) phrases rather than
    one per fragmentation or a broad single-token prefix scan.
    """
    if len(word) < 2:
        return ""
    originals = _variant_originals(vocabulary)
    pieces = {(start, end): sorted({word[start:end], *originals.get(word[start:end], ())})
              for start in range(len(word)) for end in range(start + 1, len(word) + 1)}
    present = _present_terms(database, {item for items in pieces.values() for item in items}, attachments)
    pieces = {span: [item for item in items if item in present] for span, items in pieces.items()}

    def pairs(spans: list[tuple[tuple[int, int], tuple[int, int]]]) -> list[str]:
        return sorted({quote_fts(f"{left} {right}") for first, second in spans
                       for left, right in product(pieces[first], pieces[second])})

    size = len(word)
    leading = pairs([((0, cut), (cut, end)) for cut in range(1, size) for end in range(cut + 1, size + 1)])
    trailing = pairs([((start, cut), (cut, size)) for cut in range(1, size) for start in range(cut)])
    if not leading or not trailing:
        return ""
    return "(" + " OR ".join(leading) + ") AND (" + " OR ".join(trailing) + ")"


def compile_text(value: str, vocabulary: SearchVocabulary,
                 database: sqlite3.Connection, attachments: bool) -> CompiledText:
    words = search_words(search_fold(value))
    originals = _variant_originals(vocabulary)
    options = [sorted({word, *originals.get(word, ())}) for word in words]
    if not words:
        return CompiledText(match="")
    splits = [_split_word(database, word, vocabulary, attachments) for word in words]
    split_match = " AND ".join(
        "(" + " OR ".join(quote_fts(item) for item in items) + (f" OR ({split})" if split else "") + ")"
        for items, split in zip(options, splits, strict=True)
    ) if any(splits) else ""
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


_ASCII_RUNS = re.compile(r"[\x00-\x7f]+")


def has_combining_marks(content: str) -> bool:
    """Drop ASCII runs in C, then test each distinct remaining character once."""
    return not content.isascii() and any(
        unicodedata.category(character) == "Mn" for character in set(_ASCII_RUNS.sub("", content))
    )


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
