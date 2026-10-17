# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

# Purpose: Define typed nonsecret accounts and remote message observations.
# How it works:
# Validate connection settings and preserve mailbox epochs, Gmail identities, labels and
# wire-normalization evidence and download totals.
# Fundamental dependents:
# - archive_config: persisted IMAP accounts use these validation rules.
# - imap_source: discovery and acquisition share these provider identity types.

"""Typed operational configuration and provider observations for read-only IMAP."""
from __future__ import annotations

from typing import Literal
from datetime import datetime
from hashlib import sha256
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ImapAccount(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(default_factory=lambda: uuid4().hex, pattern=r"^[a-zA-Z0-9_-]+$")
    kind: Literal["imap"] = "imap"
    server: str = Field(min_length=1)
    port: int = Field(default=993, ge=1, le=65535)
    username: str = Field(min_length=1)
    tls: Literal["implicit", "starttls"] = "implicit"
    enabled: bool = True

    @field_validator("server", "username")
    @classmethod
    def no_controls(cls, value: str) -> str:
        if any(not character.isprintable() for character in value):
            raise ValueError("connection fields cannot contain control characters")
        return value

    @field_validator("server")
    @classmethod
    def hostname_only(cls, value: str) -> str:
        if any(character.isspace() or character in "/@?#" for character in value):
            raise ValueError("server must be a hostname or IP address, without a URL or credentials")
        return value

    @property
    def credential_ref(self) -> str:
        endpoint = "\x00".join((self.server.casefold(), str(self.port), self.tls))
        return f"keyring://mail-archiver/imap/{self.id}/{sha256(endpoint.encode()).hexdigest()}"


class ImapMailbox(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str
    wire_name: str
    delimiter: str | None = "/"
    flags: tuple[str, ...] = ()
    uid_validity: int = Field(default=0, ge=0)
    uid_next: int = Field(default=1, ge=1)


class ImapDownload(BaseModel):
    """Wire provenance before the explicitly selected IMAP line-ending conversion."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    normalization: Literal["crlf-to-lf"] = "crlf-to-lf"
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_byte_length: int = Field(ge=0)
    crlf_pairs: int = Field(ge=0)


class ImapMessageMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    uid_validity: int = Field(gt=0)
    uid: int = Field(gt=0)
    mailbox: str = ""
    gmail_id: str | None = Field(default=None, pattern=r"^[0-9]+$")
    labels: tuple[str, ...] = ()
    internal_date: datetime | None = None
    download: ImapDownload | None = None


class ImapTotals(BaseModel):
    archived: int = 0
    duplicates: int = 0
    excluded: int = 0
    errors: int = 0
