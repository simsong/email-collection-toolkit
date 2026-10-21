# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Register the application's customizable lists in one discoverable catalog.
# Each entry binds a packaged template, strict schema, scope and consumer contract.
# Existing owner/contact YAML files are retained through small compatibility adapters.
# Personal template edits never modify shipped resources or existing DB snapshots.
# The editor, processors and documentation share these stable list identifiers.
"""Typed catalog of customizable default lists and compatibility adapters."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from yaml import safe_dump

from .archive_config import load_archive_config, save_archive_config
from .configuration import ApplicationConfiguration
from .contact_filtering import ContactFilterConfiguration, extend_contact_filters, load_contact_filters
from .custom_lists import DefaultList, atomic_text
from .list_models import EntityRules, ProviderDomains
from .message import MessagePatternConfig
from .owner_rules import OwnerRules
from .reply_addresses import ReplyAddressRules
from .sources import LocalSourceRules

PACKAGE=Path(__file__).parent


def _owner(archive: Path, _directory: Path | None) -> OwnerRules | None:
    configured=load_archive_config(archive).owner
    if configured is not None:
        return configured
    legacy=archive/'owner-names.txt'
    return OwnerRules(include=legacy.read_text(encoding='utf-8').splitlines()) if legacy.is_file() else None


def _save_owner(archive: Path, value: OwnerRules) -> None:
    config=load_archive_config(archive)
    if config.owner!=value:
        config.owner=value
        save_archive_config(archive,config)


def _contacts(archive: Path, directory: Path | None) -> ContactFilterConfiguration | None:
    path=archive/'contact_filters.yaml'
    if not path.is_file():
        return None
    configured=load_contact_filters(path)
    return configured if isinstance(configured,ContactFilterConfiguration) else extend_contact_filters(CONTACT_FILTERS.template(directory),configured)


def _save_contacts(archive: Path, value: ContactFilterConfiguration) -> None:
    path=archive/'contact_filters.yaml'
    if path.is_file():
        atomic_text(path,safe_dump(value.model_dump(mode='json'),sort_keys=False,allow_unicode=True))


OWNERS=DefaultList('owner-rules','Owner / sender identities',
    'Addresses whose messages belong in Sent. Empty initially; customize during first-run setup. Exclusions win.',
    OwnerRules,PACKAGE/'owner_rules.yaml',legacy=_owner,legacy_write=_save_owner,requires_customization=lambda value:not value.include)
REPLY_ADDRESSES=DefaultList('reply-addresses','Reply-address patterns',
    'Provider-scoped routing patterns, family labels and attributed-name extraction. Never accepted person aliases.',
    ReplyAddressRules,PACKAGE/'reply_addresses.yaml')
CONTACT_FILTERS=DefaultList('contact-filters','Contact exclusion rules',
    'Recognize mailing lists, services and invalid addresses. Existing archive YAML replacement/extension remains supported.',
    ContactFilterConfiguration,PACKAGE/'contact_filters.yaml',legacy=_contacts,legacy_write=_save_contacts)
PROVIDER_DOMAINS=DefaultList('provider-domains','Public email provider domains',
    'Domains that should not create institution memberships. Legacy identity plugin additions still apply.',
    ProviderDomains,PACKAGE/'provider_domains.yaml')
ENTITY_RULES=DefaultList('entity-rules','Person / organization overrides',
    'Explicit person and organization names and service mailbox patterns, applied before automatic SBook classification.',
    EntityRules,PACKAGE/'entity_rules.yaml')
DISPLAY=DefaultList('display-policy','Display policy',
    'Application-wide display defaults such as the search highlight color.',
    ApplicationConfiguration,PACKAGE/'configuration.yaml',archive_supported=False)
MESSAGE_PATTERNS=DefaultList('message-patterns','Quoted-message date patterns',
    'Application-wide date-recovery regexes. Each expression must capture exactly one date.',
    MessagePatternConfig,PACKAGE/'message_patterns.yaml',archive_supported=False)
LOCAL_SOURCES=DefaultList('local-source-rules','Local source-file rules',
    'Application-wide exclusions and bounded source-probe settings, available before an archive is selected.',
    LocalSourceRules,PACKAGE/'local_source_rules.yaml',archive_supported=False)


def registered_lists() -> tuple[DefaultList[Any],...]:
    """The heterogeneous schema registry is the editor's closed API boundary."""
    return (OWNERS,REPLY_ADDRESSES,CONTACT_FILTERS,PROVIDER_DOMAINS,ENTITY_RULES,DISPLAY,MESSAGE_PATTERNS,LOCAL_SOURCES)


def default_list(key: str) -> DefaultList[Any]:
    for definition in registered_lists():
        if definition.key==key:
            return definition
    raise ValueError('Unknown customizable default list')
