# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Define reusable schemas for lists formerly embedded as Python constants.
# Regex fields are compiled during validation before an edit is published.
# Domain lists are normalized without treating subdomains as identical domains.
# Entity rules expose explicit person/organization overrides separately from routing.
# These models are registered by list_catalog and consumed by existing services.
"""Typed customizable domain and entity-classification defaults."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

from .contact_filtering import RegexPattern
from .reply_addresses import ReplyAddressRule


class ProviderDomains(BaseModel):
    model_config = ConfigDict(extra='forbid',frozen=True)
    version: Literal[1] = 1
    domains: tuple[str,...]

    @field_validator('domains')
    @classmethod
    def normalized(cls, values: tuple[str,...]) -> tuple[str,...]:
        return ReplyAddressRule.valid_domains(values)


class EntityRules(BaseModel):
    model_config = ConfigDict(extra='forbid',frozen=True)
    version: Literal[1] = 1
    person_name_patterns: tuple[RegexPattern,...] = ()
    organization_name_patterns: tuple[RegexPattern,...] = ()
    service_local_patterns: tuple[RegexPattern,...] = ()
