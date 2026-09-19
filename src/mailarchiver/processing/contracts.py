# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""Typed production services and provenance carried across processor workers."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..scan_evidence import ScanEvidence as ScanEvidence, ScanFailure as ScanFailure, ScanStatus as ScanStatus
from ..message import ParsedMessage
from ..owner_rules import OwnerRules
from ..plugin_api import MailObject, SourceReference
from ..mbox_framing import MboxNormalization
from ..search import IndexedAttachment, SuggestedAddress


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProcessingPolicy(Contract):
    owners: OwnerRules = Field(default_factory=OwnerRules)
    earliest_year: int = 1900
    index_attachments: bool = False
    scan_policy: Literal["clamav", "not-scanned"] = "clamav"
    scanner_session: str | None = Field(default=None, exclude=True)
    # Legacy saved policies remain readable; these fields are no longer executed.
    scanner_configuration: Path | None = None
    scanner_executable: str = "clamdscan"
    max_message_depth: int = Field(default=20, ge=1)


class SourceMetadata(Contract):
    source: SourceReference
    source_file_pk: int
    work_id: str
    cursor: str
    source_path: Path | None = None
    prior_date: datetime | None = None
    source_date: datetime | None = None
    envelope_hex: str | None = None
    normalization: MboxNormalization | None = None
    scan_evidence: ScanEvidence | None = None
    scan_responsibility: Literal["host", "producer"] = "host"

    def mail_object(self, raw: bytes) -> MailObject:
        return MailObject(source=self.source, work_id=self.work_id, cursor=self.cursor, raw=raw,
                          source_date_utc=self.source_date,
                          mbox_envelope=None if self.envelope_hex is None else bytes.fromhex(self.envelope_hex),
                          mbox_normalization=self.normalization)


class ContentMetadata(Contract):
    charset: str | None = None
    filename: str | None = None
    part_id: int = 0
    plain_body_exists: bool = False
    html_body_exists: bool = False
    depth: int = 0


class MailboxReference(Contract):
    path: Path
    category: Literal["Sent", "Archive", "INFECTED"]


class Filing(Contract):
    parsed: ParsedMessage
    mailbox: MailboxReference | None = None


class HeaderMetadata(Contract):
    parsed: ParsedMessage
    suggestions: tuple[SuggestedAddress, ...] = ()
    search_headers: str = ""


class TextContent(Contract):
    text: str


class AddressEvidence(Contract):
    address: str
    name: str = ""
    kind: Literal["header", "signature"]


class MimeInventory(Contract):
    attachments: tuple[IndexedAttachment, ...] = ()
