# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Validate the settings consumed by the packaged processing plugins.
# These schemas mirror each processor's supported limits and identity options.
# The host validates merged installation and archive settings before processing.
# Configuration boundaries translate schema failures into repair instructions.
# Processor code shares the limit models to keep runtime interpretation consistent.
# Third-party namespaces retain their own configuration contracts.
"""Typed settings for built-in processor configuration."""
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from .mime_tree import MimeLimits

MAX_TEXT_BYTES = "max_text_bytes"
SIGNATURE_LINES = "signature_lines"
DEFAULT_MAX_TEXT_BYTES = 8 * 1024 * 1024


class TextSettings(BaseModel):
    model_config = ConfigDict(extra="allow")
    max_text_bytes: int = Field(default=DEFAULT_MAX_TEXT_BYTES, gt=0, strict=True)


class IdentitySettings(TextSettings):
    signature_lines: int = Field(default=12, ge=1, le=100, strict=True)
    provider_domains: list[Annotated[str, StringConstraints(strict=True, min_length=1)]] = Field(default_factory=list)


class AttachedMessageLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    max_depth: int | None = Field(default=None, gt=0, strict=True)


def settings_model(name: str) -> type[BaseModel] | None:
    """Select only namespaces whose semantics are owned by the packaged host."""
    if name == "identity-evidence":
        return IdentitySettings
    if name in {"text-index", "html-text", "rtf-text"}:
        return TextSettings
    if name == "mime":
        return MimeLimits
    if name == "attached-message":
        return AttachedMessageLimits
    return None
