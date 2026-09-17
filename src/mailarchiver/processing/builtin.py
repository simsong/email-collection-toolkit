# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""Production processors. Workers derive typed results; the host owns publication."""
from __future__ import annotations

import hashlib
import re
import subprocess
from datetime import datetime
from email.utils import getaddresses

from striprtf.striprtf import rtf_to_text

from ..encoding import decode_text
from ..message import ParsedMessage, decoded_header, decoded_message_header, parse_message
from ..search import html_text, suggested_addresses
from .api import Emission, Handoff, ProcessingObject, ProcessingResult, PromotedMessage
from .contracts import AddressEvidence, Filing, HeaderMetadata, MimeInventory, ScanEvidence, TextContent
from .mime import extract_parts, read_headers

SIGNATURE_LINES = "signature_lines"
EMAIL = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?")


def parsed_input(item: ProcessingObject, *, header_only: bool = False) -> ParsedMessage:
    metadata = item.source_metadata
    policy = item.application.policy
    if policy is None:
        raise ValueError("production processor requires an application policy")
    with item.message_ref.open() as source:
        if header_only:
            read_headers(source)
            end = source.tell()
            source.seek(0)
            raw = source.read(end)
        else:
            raw = source.read()
    parsed = parse_message(raw, metadata.source_path if metadata else None,
                           metadata.prior_date if metadata else None,
                           metadata.source_date if metadata else None, policy.earliest_year)
    if header_only:
        fallback = hashlib.sha256(raw).hexdigest()
        parsed = parsed.model_copy(update={"sha256": item.message_ref.sha256,
            "message_id": item.message_ref.sha256 if parsed.message_id == fallback else parsed.message_id})
    return parsed


class ClamAVProcessor:
    def process(self, item: ProcessingObject) -> ProcessingResult:
        policy = item.application.policy
        if policy is None:
            raise ValueError("scanner policy is missing")
        if policy.scan_policy == "not-scanned":
            return ProcessingResult(scan=ScanEvidence(status="not-scanned", detail="explicit import policy"))
        if policy.scanner_configuration is None:
            raise ValueError("on-demand scanner is not ready")
        result = subprocess.run([policy.scanner_executable, f"--config-file={policy.scanner_configuration}",
            "--stream", str(item.message_ref.path)], capture_output=True, check=False, timeout=item.remaining_seconds)
        if result.returncode not in (0, 1):
            raise RuntimeError(result.stderr.decode("utf-8", "replace") or f"clamdscan exit {result.returncode}")
        if result.returncode == 1:
            return ProcessingResult(outcome="abort-message", scan=ScanEvidence(status="infected"),
                filing=Filing(parsed=parsed_input(item, header_only=True), mailbox=item.archive.mailbox("INFECTED", None)))
        return ProcessingResult(scan=ScanEvidence(status="clean"))


class FilingProcessor:
    def process(self, item: ProcessingObject) -> ProcessingResult:
        policy = item.application.policy
        if policy is None or item.scan_provenance not in ("clean", "not-scanned"):
            raise ValueError("filing requires a successful ingest scan decision")
        parsed = parsed_input(item)
        if parsed.autosave:
            return ProcessingResult(outcome="abort-message", filing=Filing(parsed=parsed))
        role = "Sender" if policy.owners.matches(parsed.sender) else "Archive"
        mailbox = item.archive.mailbox(datetime.fromisoformat(parsed.date_utc).year, role)
        return ProcessingResult(filing=Filing(parsed=parsed, mailbox=mailbox), handoffs=(Handoff(pipeline="message"),))


class HeaderProcessor:
    def process(self, item: ProcessingObject) -> ProcessingResult:
        parsed = parsed_input(item)
        with item.message_ref.open() as source:
            headers = read_headers(source)
            end = source.tell()
            source.seek(0)
            raw = source.read(end)
        suggestions = tuple(suggested_addresses(headers))
        search_headers = "\n".join(decoded_message_header(raw, headers, name) for name in ("From", "To", "Cc", "Subject", "Date"))
        return ProcessingResult(headers=HeaderMetadata(parsed=parsed, suggestions=suggestions, search_headers=search_headers),
            evidence=tuple(AddressEvidence(address=identity.address, name=identity.display_name, kind="header")
                           for identity in suggestions if "@" in identity.address))


class ContentHandoffProcessor:
    def process(self, _item: ProcessingObject) -> ProcessingResult:
        return ProcessingResult(handoffs=(Handoff(pipeline="content"),))


class MimeProcessor:
    def process(self, item: ProcessingObject) -> ProcessingResult:
        workspace = item.application.workspace
        if workspace is None:
            raise ValueError("MIME processor requires an output workspace")
        result = extract_parts(item.message_ref.path, workspace, cancelled=item.check_cancelled)
        plain = any(part.scope == "body" and part.content_type == "text/plain" for part in result.parts)
        html = any(part.scope == "body" and part.content_type == "text/html" for part in result.parts)
        return ProcessingResult(mime=MimeInventory(attachments=tuple(result.attachments)),
            emissions=tuple(Emission(content_ref=part.reference, content_type=part.content_type,
                part_path=part.part_path, scope=part.scope,
                metadata=part.metadata.model_copy(update={"plain_body_exists": plain, "html_body_exists": html,
                                                         "depth": item.content_metadata.depth})) for part in result.parts),
            diagnostics=tuple(result.diagnostics))


class HtmlProcessor:
    def process(self, item: ProcessingObject) -> ProcessingResult:
        if item.scope == "body" and item.content_metadata.plain_body_exists:
            return ProcessingResult()
        text = html_text(decode_text(item.content_ref.path.read_bytes(), item.content_metadata.charset).value)
        return synthetic_text(item, text)


class RtfProcessor:
    def process(self, item: ProcessingObject) -> ProcessingResult:
        if item.scope == "body" and (item.content_metadata.plain_body_exists or item.content_metadata.html_body_exists):
            return ProcessingResult()
        text = rtf_to_text(item.content_ref.path.read_bytes().decode("latin-1"), errors="replace")
        return synthetic_text(item, text)


def synthetic_text(item: ProcessingObject, text: str) -> ProcessingResult:
    return ProcessingResult(emissions=(Emission(content_ref=item.create_content(text.encode("utf-8")),
        content_type="text/plain", part_path=item.part_path, scope=item.scope, synthetic=True,
        metadata=item.content_metadata.model_copy(update={"charset": "utf-8"})),))


class TextProcessor:
    def process(self, item: ProcessingObject) -> ProcessingResult:
        return ProcessingResult(text=TextContent(text=decode_text(item.content_ref.path.read_bytes(), item.content_metadata.charset).value))


class IdentityProcessor:
    def process(self, item: ProcessingObject) -> ProcessingResult:
        settings = item.get_my_config()
        count = settings.get(SIGNATURE_LINES, 12)
        if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 100:
            raise ValueError("signature_lines must be an integer from 1 through 100")
        text = decode_text(item.content_ref.path.read_bytes(), item.content_metadata.charset).value
        signature = text.rsplit("\n-- \n", 1)[-1] if "\n-- \n" in text else "\n".join(text.splitlines()[-count:])
        evidence: list[AddressEvidence] = []
        for line in signature.splitlines():
            if line.lstrip().startswith(">") or re.match(r"(?i)^\s*(from|to|cc|bcc|subject|date):", line):
                continue
            for match in EMAIL.finditer(line):
                address = match.group().lower().rstrip(".")
                named = next((decoded_header(name) for name, candidate in getaddresses([line])
                              if candidate.lower() == address and name), "")
                evidence.append(AddressEvidence(address=address, name=named, kind="signature"))
        return ProcessingResult(evidence=tuple(evidence))


class AttachedMessageProcessor:
    def process(self, item: ProcessingObject) -> ProcessingResult:
        policy = item.application.policy
        if policy is None:
            raise ValueError("attachment processing policy is missing")
        if item.content_metadata.depth >= policy.max_message_depth:
            return ProcessingResult(outcome="abort-part", diagnostics=("attached-message depth limit reached; parent bytes retained",))
        return ProcessingResult(promotions=(PromotedMessage(content_ref=item.content_ref, part_path=item.part_path),))
