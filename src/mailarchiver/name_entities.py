# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Classify display names separately from deciding whether addresses share an identity.
# Reuse pinned SBook5 company codewords, organization words and first-name gazetteer.
# Compile only bounded whole-word Flex rules, not the complete legacy scanner.
# Keep conflicting person/company evidence unknown rather than excluding a person.
# Return typed reasons for reversible picker and research-report display filters.
# Classification never changes source addresses, accepted groups or owner labels.
"""Conservative local entity typing using SBook5's maintained lexical knowledge."""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel
from .list_models import EntityRules

DATA = Path(__file__).with_name('sbook')
WORD_RULE = re.compile(r'^\{S\}(.+?)/\{END\}\s+\{aflag\s*(?:\|=|=)\s*([^}]+)\}')
TOKENS = re.compile(r'[^\W\d_]+',re.UNICODE)


class EntityType(BaseModel):
    kind: Literal['person','organization','service','unknown'] = 'unknown'
    reasons: tuple[str,...] = ()

    @property
    def non_person(self) -> bool:
        return self.kind in ('organization','service')


class Lexicon(BaseModel):
    companies: re.Pattern[str]
    organizations: re.Pattern[str]
    exceptions: re.Pattern[str]
    first_names: frozenset[str]


def flex_word(pattern: str) -> str:
    """Translate literal quoted spans in the supported whole-word rule subset."""
    if re.search(r'\{[A-Z]+\}',pattern):
        raise ValueError('Flex macro is outside the supported whole-word subset')
    return re.sub(r'"([^"\n]*)"',lambda match: re.escape(match[1]),pattern)


def word_rules(filename: str, flag: str) -> list[str]:
    patterns=[]
    for line in (DATA/filename).read_text().splitlines():
        rule=WORD_RULE.match(line)
        if rule and flag in rule[2] and 'P_WEAK' not in rule[2] and not re.search(r'\{[A-Z]+\}',rule[1]):
            patterns.append(flex_word(rule[1]))
    return patterns


def combined(patterns: list[str]) -> re.Pattern[str]:
    # Unicode word boundaries prevent ASCII codewords matching inside foreign names.
    return re.compile(r'(?<!\w)(?:'+'|'.join(patterns)+r')(?!\w)',re.I)


@lru_cache(maxsize=1)
def lexicon() -> Lexicon:
    companies=word_rules('parse_company.fp','P_COMPANY')
    for line in (DATA/'company_names.txt').read_text().splitlines():
        if line.strip() and not line.lstrip().startswith('#'):
            companies.append(flex_word(line.strip()))
    return Lexicon(companies=combined(companies),organizations=combined(word_rules('parse_address.fp','P_ORG')),
        exceptions=combined(word_rules('parse_company.fp','P_NOT_COMPANY')),
        first_names=frozenset(line.strip().casefold() for line in (DATA/'first_names.txt').read_text().splitlines() if line.strip()))


def classify_name(name: str, address: str = '', rules: EntityRules | None = None) -> EntityType:
    if rules is None:
        from .list_catalog import ENTITY_RULES
        rules=ENTITY_RULES.template()
    if any(re.search(pattern,name,re.I) for pattern in rules.person_name_patterns):
        return EntityType(kind='person',reasons=('Explicit person-name override',))
    if any(re.search(pattern,name,re.I) for pattern in rules.organization_name_patterns):
        return EntityType(kind='organization',reasons=('Explicit organization-name override',))
    local=address.rpartition('@')[0]
    if any(re.search(pattern,local,re.I) for pattern in rules.service_local_patterns):
        return EntityType(kind='service',reasons=('Service/role mailbox prefix',))
    return classify_display_name(name)


@lru_cache(maxsize=50000)
def classify_display_name(name: str) -> EntityType:
    value=' '.join(unicodedata.normalize('NFKC',name).split())
    if not value or len(value)>256 or '@' in value:
        return EntityType()
    words=TOKENS.findall(value)
    words_lower=[word.casefold() for word in words]
    if not words:
        return EntityType()
    rules=lexicon()
    given_names=[words_lower[0]]
    if ',' in value:
        after_comma=TOKENS.findall(value.partition(',')[2])
        if after_comma:
            given_names.append(after_comma[0].casefold())
    person_hint=2<=len(words)<=5 and any(given in rules.first_names or given in ('dr','prof','mr','mrs','ms') for given in given_names)
    if rules.exceptions.search(value):
        return EntityType(kind='person' if person_hint else 'unknown',reasons=('SBook explicit person exception',))
    match=rules.organizations.search(value) or rules.companies.search(value)
    if match:
        reason=f'SBook organization/company codeword: {match[0]}'
        return EntityType(kind='unknown' if person_hint else 'organization',
            reasons=(reason,'Conflicting first-name evidence; retained for review') if person_hint else (reason,))
    return EntityType(kind='person' if person_hint else 'unknown',
        reasons=('SBook first-name gazetteer and name shape',) if person_hint else ())
