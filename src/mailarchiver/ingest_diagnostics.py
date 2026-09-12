"""Local import failure evidence without copying whole messages or traceback locals."""

import hashlib
import traceback

from pydantic import ValidationError

from .plugin_api import SourceReference
from .mbox_framing import MboxNormalization

MESSAGE_PREVIEW_BYTES = 4096
ENVELOPE_PREVIEW_BYTES = 512
VALIDATION_CONTEXT = "ctx"
VALIDATION_ERROR = "error"
VALIDATION_LOCATION = "loc"
VALIDATION_MESSAGE = "msg"
VALIDATION_TYPE = "type"


def add_message_context(
    error: BaseException, source: SourceReference, cursor: str, raw: bytes, envelope: bytes | None,
    normalization: MboxNormalization | None = None,
) -> None:
    """Attach exact provenance/hash and an escaped, bounded prefix of the failing input."""
    preview = raw[:MESSAGE_PREVIEW_BYTES]
    error.add_note(
        f"Source: {source.model_dump_json()}\n"
        f"Source cursor (byte offset for local MBOX): {cursor!r}\n"
        f"Message SHA-256: {hashlib.sha256(raw).hexdigest()}; bytes={len(raw)}\n"
        f"Message prefix ({len(preview)}/{len(raw)} bytes): {preview!r}\n"
        f"MBOX envelope prefix (up to {ENVELOPE_PREVIEW_BYTES} bytes): "
        f"{None if envelope is None else envelope[:ENVELOPE_PREVIEW_BYTES]!r}"
    )
    if normalization is not None:
        error.add_note(
            f"MBOX normalization: {normalization.rule}; source payload SHA-256: {normalization.source_raw_sha256}\n"
            f"Original envelope prefix: {normalization.original_envelope[:ENVELOPE_PREVIEW_BYTES]!r}\n"
            f"Quoted envelope prefix: {normalization.quoted_envelope[:ENVELOPE_PREVIEW_BYTES]!r}"
        )


def _exception_summary(error: BaseException) -> str:
    if isinstance(error, ValidationError):
        issues = error.errors(include_input=False, include_context=False, include_url=False)
        return f"ValidationError: {error.title}\n" + "\n".join(
            f"{issue[VALIDATION_LOCATION]!r}: {issue[VALIDATION_MESSAGE]} [{issue[VALIDATION_TYPE]}]"
            for issue in issues
        )
    return f"{type(error).__name__}: {error}"


def format_failure(error: BaseException) -> str:
    """Retain exception chains and validator frames without Pydantic's input_value dump."""
    detail = _exception_summary(error) + "\n\n"
    pending = [("", error)]
    seen: set[int] = set()
    while pending:
        label, current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        detail += label + "Traceback (most recent call last):\n"
        detail += "".join(traceback.format_tb(current.__traceback__))
        detail += _exception_summary(current) + "\n"
        detail += "".join(note + "\n" for note in getattr(current, "__notes__", ()))
        if isinstance(current, ValidationError):
            for issue in current.errors(include_input=False, include_url=False):
                cause = issue.get(VALIDATION_CONTEXT, {}).get(VALIDATION_ERROR)
                if isinstance(cause, BaseException):
                    pending.append((f"\nValidator origin for {issue[VALIDATION_LOCATION]!r}:\n", cause))
        cause = current.__cause__ or (None if current.__suppress_context__ else current.__context__)
        if cause is not None:
            label = "Caused by" if current.__cause__ is not None else "During handling of"
            pending.append((f"\n{label}:\n", cause))
    return detail
