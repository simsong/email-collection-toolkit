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


def _validation_summary(error: ValidationError) -> str:
    """Return deterministic validation text without embedding raw input values."""
    issues = error.errors(include_input=False, include_url=False)
    return f"{len(issues)} validation error(s) for {error.title}\n" + "\n".join(
        f"{issue[VALIDATION_LOCATION]}\n  {issue['msg']}" for issue in issues
    )


def _format_exception(error: BaseException) -> str:
    """Format tracebacks while omitting unbounded ValidationError input values."""
    if not isinstance(error, ValidationError):
        return "".join(traceback.format_exception(error, chain=False))
    return "".join(traceback.format_tb(error.__traceback__)) + (
        f"{type(error).__name__}: {_validation_summary(error)}\n"
    )


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
            f"Original envelope prefix: {normalization.original_envelope[:ENVELOPE_PREVIEW_BYTES]!r}"
        )


def format_failure(error: BaseException) -> str:
    """Retain worker frames, exception notes/chains, and Pydantic validator origins."""
    summary = _validation_summary(error) if isinstance(error, ValidationError) else str(error)
    detail = f"{type(error).__name__}: {summary}\n\n" + _format_exception(error)
    pending = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if current is not error:
            detail += "\n" + _format_exception(current)
        if isinstance(current, ValidationError):
            for issue in current.errors(include_input=False, include_url=False):
                cause = issue.get(VALIDATION_CONTEXT, {}).get(VALIDATION_ERROR)
                if isinstance(cause, BaseException):
                    detail += f"\nValidator origin for {issue[VALIDATION_LOCATION]!r}:\n"
                    detail += "".join(traceback.format_exception(cause))
        cause = current.__cause__ or (None if current.__suppress_context__ else current.__context__)
        if cause is not None:
            pending.append(cause)
    return detail
