# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""Offline ICANN public-suffix matching for organization-domain evidence."""
from functools import lru_cache
from importlib.resources import files

from pydantic import BaseModel, ConfigDict


class SuffixRules(BaseModel):
    model_config = ConfigDict(frozen=True)
    exact: frozenset[str]
    wildcard: frozenset[str]
    exceptions: frozenset[str]


@lru_cache(maxsize=1)
def rules() -> SuffixRules:
    exact, wildcard, exceptions = set(), set(), set()
    for line in files("mailarchiver").joinpath("public_suffix_list.dat").read_text(encoding="utf-8").splitlines():
        if line == "// ===END ICANN DOMAINS===":
            break
        if not line or line.startswith("//"):
            continue
        target = exceptions if line.startswith("!") else wildcard if line.startswith("*.") else exact
        target.add(line.removeprefix("!").removeprefix("*.").encode("idna").decode("ascii"))
    return SuffixRules(exact=frozenset(exact), wildcard=frozenset(wildcard), exceptions=frozenset(exceptions))


def registrable_domain(domain: str) -> str:
    """Apply longest-match, wildcard and exception rules; unknown suffixes use one label."""
    domain = domain.rstrip(".").lower()
    try:
        normalized = domain.encode("idna").decode("ascii")
    except UnicodeError:
        return domain
    labels = normalized.split(".")
    suffix_length = 1
    current = rules()
    for index in range(len(labels)):
        suffix = ".".join(labels[index:])
        length = len(labels) - index
        if suffix in current.exceptions:
            suffix_length = length - 1
            break
        if suffix in current.exact:
            suffix_length = max(suffix_length, length)
        if index and suffix in current.wildcard:
            suffix_length = max(suffix_length, length + 1)
    return ".".join(labels[-(suffix_length + 1):])
