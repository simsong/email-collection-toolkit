# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Recognize provider-scoped routing addresses without merging them into people.
# Typed rules combine exact domains with optional whole-address regular expressions.
# Name extraction uses a display name, one named capture, or an explicit label.
# Family labels may interpolate only declared captures and fixed address fields.
# Matches retain the original address, header role, rule and policy fingerprint.
# The production host stores these derived observations in its evidence database.
"""Customizable reply-address family and attributed-name extraction."""
from __future__ import annotations

import hashlib
import re
from string import Formatter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .contact_filtering import RegexPattern

HeaderRole = Literal['from','to','cc','bcc','reply-to']
RULE_FIELD='rule_id'
DOMAIN_FIELD='domain'
LOCAL_FIELD='local'


class RoutingCandidate(BaseModel):
    address: str
    display_name: str = ''
    header: HeaderRole


class ReplyAddressRule(BaseModel):
    model_config = ConfigDict(extra='forbid',frozen=True)
    id: str = Field(pattern=r'^[a-z][a-z0-9-]{0,63}$')
    domains: tuple[str,...] = Field(min_length=1)
    address_pattern: RegexPattern | None = None
    family: str = Field(min_length=1,max_length=256)
    name_source: Literal['display-name','capture','fixed'] = 'display-name'
    name_group: str | None = None
    name_separators: str = Field(default='._',max_length=8)
    fallback_name: str = Field(default='',max_length=256)
    headers: tuple[HeaderRole,...] = ('from','to','cc','bcc','reply-to')

    @field_validator('domains')
    @classmethod
    def valid_domains(cls, domains: tuple[str,...]) -> tuple[str,...]:
        result=[]
        for domain in domains:
            normalized=domain.strip().casefold().rstrip('.')
            if not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?',normalized) or '..' in normalized:
                raise ValueError('Use exact domain names, not URLs, globs or regular expressions')
            result.append(normalized)
        return tuple(dict.fromkeys(result))

    @model_validator(mode='after')
    def extraction_fields(self) -> ReplyAddressRule:
        captures=re.compile(self.address_pattern,re.I).groupindex if self.address_pattern else {}
        if self.name_source=='capture' and self.name_group not in captures:
            raise ValueError('Capture name extraction requires a declared named regex group')
        if self.name_source=='fixed' and not self.fallback_name:
            raise ValueError('Fixed name extraction requires fallback_name')
        for _literal,field,format_spec,conversion in Formatter().parse(self.family):
            if field is not None and (field not in {*captures,RULE_FIELD,DOMAIN_FIELD,LOCAL_FIELD} or format_spec or conversion):
                raise ValueError('Family fields must be plain declared captures, rule_id, domain or local')
        return self


class ReplyAddressRules(BaseModel):
    model_config = ConfigDict(extra='forbid',frozen=True)
    version: Literal[1] = 1
    rules: tuple[ReplyAddressRule,...] = ()

    @model_validator(mode='after')
    def unique_ids(self) -> ReplyAddressRules:
        if len({rule.id for rule in self.rules})!=len(self.rules):
            raise ValueError('Reply-address rule IDs must be unique')
        return self


class RoutingMatch(BaseModel):
    model_config = ConfigDict(extra='forbid',frozen=True)
    address: str
    header: HeaderRole
    rule_id: str
    family: str
    name: str
    policy_revision: str
    kind: Literal['service'] = 'service'


def match_reply_address(candidate: RoutingCandidate, policy: ReplyAddressRules) -> RoutingMatch | None:
    address=candidate.address.strip()
    local,separator,domain=address.rpartition('@')
    if not separator or len(address)>4096:
        return None
    for rule in policy.rules:
        if domain.casefold().rstrip('.') not in rule.domains or candidate.header not in rule.headers:
            continue
        match=re.fullmatch(rule.address_pattern,address,re.I) if rule.address_pattern else None
        if rule.address_pattern and match is None:
            continue
        if rule.name_source=='capture':
            assert match is not None and rule.name_group is not None
            name=match.group(rule.name_group) or ''
            name=' '.join(name.translate(str.maketrans({character:' ' for character in rule.name_separators})).split())
        elif rule.name_source=='display-name':
            name=candidate.display_name.strip() or rule.fallback_name
        else:
            name=rule.fallback_name
        # This mapping is the documented str.format API boundary, not internal data.
        fields=match.groupdict(default='') if match else {}
        fields.update({RULE_FIELD:rule.id,DOMAIN_FIELD:domain.casefold(),LOCAL_FIELD:local})
        return RoutingMatch(address=candidate.address,header=candidate.header,rule_id=rule.id,
            family=rule.family.format_map(fields),name=name,
            policy_revision=hashlib.sha256(policy.model_dump_json().encode()).hexdigest())
    return None
