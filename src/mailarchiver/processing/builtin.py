# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""In-process production plugins derive typed results; the host owns publication."""
from __future__ import annotations

import hashlib
import re
import subprocess
from datetime import UTC, datetime
from functools import lru_cache
from email.utils import getaddresses

from pydantic import BaseModel, ConfigDict, Field

from striprtf.striprtf import rtf_to_text

from ..encoding import decode_text
from ..message import MetadataDefect, ParsedMessage, decoded_header, decoded_message_header, parse_message
from ..search import html_text, suggested_addresses
from .api import Emission, Handoff, ProcessingObject, ProcessingResult, PromotedMessage
from .contracts import AddressEvidence, Filing, HeaderMetadata, MimeInventory, ScanEvidence, ScanFailure, ScanStatus, TextContent
from .mime import ExtractionLimitError, MimeLimits, extract_parts, read_headers

SIGNATURE_LINES = "signature_lines"
EMAIL = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?")
QUARANTINE_UNKNOWN_DATE = "quarantine-unknown"
MAX_TEXT_BYTES = "max_text_bytes"
DEFAULT_MAX_TEXT_BYTES = 8 * 1024 * 1024


def text_limit(item: ProcessingObject) -> int:
    limit = item.get_my_config().get(MAX_TEXT_BYTES, DEFAULT_MAX_TEXT_BYTES)
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ValueError("max_text_bytes must be a positive integer")
    return limit


def bounded_text(item: ProcessingObject) -> bytes:
    limit = text_limit(item)
    with item.content_ref.open() as source:
        data = source.read(limit + 1)
    item.check_cancelled()
    if len(data) > limit:
        raise ExtractionLimitError(f"derived text exceeds {limit} input bytes; original content retained; raise max_text_bytes and retry")
    return data


def bounded_result(item: ProcessingObject, text: str) -> None:
    if len(text.encode("utf-8")) > text_limit(item):
        raise ExtractionLimitError("derived UTF-8 text exceeds max_text_bytes; original content retained; raise limit and retry")


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
        versions = ScanEvidence(status="not-scanned")
        try:
            versions = scanner_versions(policy.scanner_executable, str(policy.scanner_configuration))
            result = subprocess.run([policy.scanner_executable, f"--config-file={policy.scanner_configuration}",
                "--stream", str(item.message_ref.path)], capture_output=True, check=False, timeout=item.remaining_seconds)
        except (OSError, subprocess.TimeoutExpired, TimeoutError) as error:
            raise ScanFailure(ScanEvidence(status="scanner-error", detail=f"{type(error).__name__}: {error}",
                                           engine_version=versions.engine_version, signature_version=versions.signature_version)) from error
        evidence = scan_result(result.returncode, result.stdout, result.stderr, versions)
        if evidence.status in ("scanner-error", "unscannable"):
            raise ScanFailure(evidence)
        if evidence.status == "infected":
            return ProcessingResult(outcome="abort-message", scan=evidence,
                filing=Filing(parsed=quarantine_metadata(item), mailbox=item.archive.mailbox("INFECTED", None)))
        return ProcessingResult(scan=evidence)


@lru_cache(maxsize=16)
def scanner_versions(executable: str, configuration: str) -> ScanEvidence:
    """Read daemon version once per run's configuration; unknown stays explicit."""
    try:
        result = subprocess.run([executable, f"--config-file={configuration}", "--version"],
                                capture_output=True, check=False, timeout=5)
        match = re.search(r"ClamAV ([^/\s]+)/([^/\s]+)", result.stdout.decode("utf-8", "replace"))
        if result.returncode == 0 and match:
            return ScanEvidence(status="not-scanned", engine_version=match[1], signature_version=match[2])
    except (OSError, subprocess.TimeoutExpired):
        pass
    return ScanEvidence(status="not-scanned")


def scan_result(returncode: int, stdout: bytes, stderr: bytes, versions: ScanEvidence) -> ScanEvidence:
    """Interpret actual scanner responses; heuristic limits are not clean scans."""
    detail = (stdout + b"\n" + stderr).decode("utf-8", "replace").strip()[-4096:]
    status: ScanStatus = "clean" if returncode == 0 else "infected" if returncode == 1 else "scanner-error"
    detections = re.findall(r": (.+) FOUND(?:\r?\n|$)", stdout.decode("utf-8", "replace"))
    if returncode == 1 and detections and all(name.startswith(("Heuristics.Limits.Exceeded", "Heuristics.Encrypted")) for name in detections):
        status = "unscannable"
    return ScanEvidence(status=status, detail=detail or f"clamdscan exit {returncode}",
                        engine_version=versions.engine_version, signature_version=versions.signature_version)


def quarantine_metadata(item: ProcessingObject) -> ParsedMessage:
    """A positive scan must file original bytes even when metadata is unusable."""
    try:
        return parsed_input(item, header_only=True)
    except Exception as error:
        policy = item.application.policy
        year = policy.earliest_year if policy is not None else 1900
        return ParsedMessage(message_id=item.message_ref.sha256, sha256=item.message_ref.sha256,
            sender="", recipients=[], subject="", date_utc=datetime(year, 1, 1, tzinfo=UTC).isoformat(),
            date_source=QUARANTINE_UNKNOWN_DATE, autosave=False,
            defects=[MetadataDefect(field="quarantine metadata", detail=f"{type(error).__name__}: {error}"),
                     MetadataDefect(field="date", detail="Unknown message date; catalog/envelope placeholder is not an observed date")])


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
        result = extract_parts(item.message_ref.path, workspace, cancelled=item.check_cancelled,
                               limits=MimeLimits.model_validate(item.get_my_config()))
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
        data = bounded_text(item)
        text = html_text(decode_text(data, item.content_metadata.charset).value)
        return synthetic_text(item, text)


class RtfProcessor:
    def process(self, item: ProcessingObject) -> ProcessingResult:
        if item.scope == "body" and (item.content_metadata.plain_body_exists or item.content_metadata.html_body_exists):
            return ProcessingResult()
        data = bounded_text(item)
        text = rtf_to_text(data.decode("latin-1"), errors="replace")
        return synthetic_text(item, text)


def synthetic_text(item: ProcessingObject, text: str) -> ProcessingResult:
    bounded_result(item, text)
    return ProcessingResult(emissions=(Emission(content_ref=item.create_content(text.encode("utf-8")),
        content_type="text/plain", part_path=item.part_path, scope=item.scope, synthetic=True,
        metadata=item.content_metadata.model_copy(update={"charset": "utf-8"})),))


class TextProcessor:
    def process(self, item: ProcessingObject) -> ProcessingResult:
        data = bounded_text(item)
        text = decode_text(data, item.content_metadata.charset).value
        bounded_result(item, text)
        return ProcessingResult(text=TextContent(text=text))


class IdentityProcessor:
    def process(self, item: ProcessingObject) -> ProcessingResult:
        settings = item.get_my_config()
        count = settings.get(SIGNATURE_LINES, 12)
        if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 100:
            raise ValueError("signature_lines must be an integer from 1 through 100")
        data = bounded_text(item)
        text = decode_text(data, item.content_metadata.charset).value
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


class AttachedMessageLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    max_depth: int | None = Field(default=None, gt=0, strict=True)


class AttachedMessageProcessor:
    def process(self, item: ProcessingObject) -> ProcessingResult:
        policy = item.application.policy
        if policy is None:
            raise ValueError("attachment processing policy is missing")
        limit = AttachedMessageLimits.model_validate(item.get_my_config()).max_depth or policy.max_message_depth
        if item.content_metadata.depth >= limit:
            raise ExtractionLimitError("attached-message depth limit reached; parent retained; raise plugins.attached-message.max_depth and retry")
        return ProcessingResult(promotions=(PromotedMessage(content_ref=item.content_ref, part_path=item.part_path),))
