# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""Typed API v2 processor boundary, independent of GUI and production ingest."""
from __future__ import annotations

from pathlib import Path
import re
import hashlib
from uuid import uuid4
import time
import subprocess
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator

from ..plugin_configuration import ConfigScope, ConfigValues, ConfigWrite, PluginConfiguration, ReadScope
from ..layout import mbox_path
from .contracts import (
    AddressEvidence, ContentMetadata, Filing, HeaderMetadata, MailboxReference,
    MimeInventory, ProcessingPolicy, ScanEvidence, ScanFailure, SourceMetadata, TextContent,
)

Pipeline = Literal["ingest", "message", "content"]
Scope = Literal["body", "attachment"]
Outcome = Literal["continue", "abort-part", "abort-message", "fail-import"]
RAW_MESSAGE = "application/x-mailarchiver-raw-message"
MIME_TYPE = re.compile(r"[a-z0-9!#$%&'+.^_`|~-]+/[a-z0-9!#$%&'+.^_`|~-]+", re.ASCII)


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProcessorManifest(Model):
    api_version: Literal[2]
    plugin_type: Literal["processor"]
    kind: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    name: str = Field(min_length=1)
    implementation_version: str = Field(min_length=1)
    entrypoint: str
    pipeline: Pipeline
    subscribes: tuple[str, ...] = Field(min_length=1)
    emits: tuple[str, ...] = ()
    emits_mime_parts: bool = False
    rank: int = Field(gt=0)
    scope: Literal["body", "attachment", "both"] = "both"
    timeout_seconds: float = Field(default=60, gt=0, allow_inf_nan=False)
    requires: tuple[str, ...] = ()

    @field_validator("subscribes", "emits")
    @classmethod
    def normalized_types(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(values)) != len(values) or any(
            MIME_TYPE.fullmatch(value) is None
            for value in values
        ):
            raise ValueError("types must be unique normalized MIME types without parameters or wildcards")
        return values


class ContentReference(Model):
    """A whole-message or part file, streamed by plugins rather than copied in JSON."""
    path: Path
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    def open(self):
        """Open immutable input bytes; callers own the returned stream."""
        return self.path.open("rb")


class ArchiveContext(Model):
    path: Path

    def mailbox(self, year: int | Literal["INFECTED"], role: Literal["Sender", "Archive"] | None) -> MailboxReference:
        if year == "INFECTED" and role is None:
            return MailboxReference(path=mbox_path(self.path, "INFECTED1.mbox"), category="INFECTED")
        if not isinstance(year, int) or role is None or not 1 <= year <= 9999:
            raise ValueError("mailbox requires a year and role, or INFECTED with no role")
        category = "Sent" if role == "Sender" else "Archive"
        return MailboxReference(path=mbox_path(self.path, f"{year}-{category}1.mbox"), category=category)


class ApplicationContext(Model):
    """Headless service identity; writes are requested through typed results."""
    api_version: Literal[2] = 2
    policy: ProcessingPolicy | None = None
    workspace: Path | None = None


class ProcessingObject(Model):
    job_id: int | None = Field(default=None, gt=0, strict=True)
    application: ApplicationContext = ApplicationContext()
    archive: ArchiveContext
    message_id: str
    message_ref: ContentReference
    content_ref: ContentReference
    content_type: str
    pipeline: Pipeline
    part_path: tuple[int, ...] = ()
    scope: Scope = "body"
    synthetic: bool = False
    parent_message_id: str | None = None
    scan_provenance: str | None = None
    producer: str | None = None
    source_metadata: SourceMetadata | None = None
    content_metadata: ContentMetadata = ContentMetadata()
    generation: str = ""

    _configuration: PluginConfiguration | None = PrivateAttr(default=None)
    _deadline: float | None = PrivateAttr(default=None)
    _cancelled: Callable[[], None] | None = PrivateAttr(default=None)

    def bind_deadline(self, deadline: float, cancelled: Callable[[], None] | None = None) -> None:
        self._deadline = deadline
        self._cancelled = cancelled

    def check_cancelled(self) -> None:
        if self._cancelled is not None:
            self._cancelled()
        if self._deadline is not None and time.monotonic() >= self._deadline:
            raise TimeoutError("processor timeout")

    @property
    def remaining_seconds(self) -> float:
        """Use this budget for blocking I/O; check_cancelled in long processing loops."""
        self.check_cancelled()
        return max(0.001, self._deadline - time.monotonic()) if self._deadline is not None else 60

    def bind_configuration(self, configuration: PluginConfiguration) -> None:
        """Host binding for this invocation's registered plugin namespace."""
        self._configuration = configuration

    def get_my_config(self, *, scope: ReadScope = "effective") -> ConfigValues:
        if self._configuration is None:
            raise RuntimeError("plugin configuration is available only during an invocation")
        return self._configuration.get_my_config(scope=scope)

    def write_my_config(self, values: ConfigValues, *, scope: ConfigScope = "archive") -> None:
        if self._configuration is None:
            raise RuntimeError("plugin configuration is available only during an invocation")
        self._configuration.write_my_config(values, scope=scope)

    def create_content(self, data: bytes) -> ContentReference:
        """Create a worker output; the host snapshots accepted output before cleanup."""
        if self.application.workspace is None:
            raise RuntimeError("content output is available only during an invocation")
        path = self.application.workspace / str(uuid4())
        path.write_bytes(data)
        return ContentReference(path=path, sha256=hashlib.sha256(data).hexdigest())


class Emission(Model):
    content_ref: ContentReference
    content_type: str
    part_path: tuple[int, ...] = ()
    scope: Scope = "body"
    synthetic: bool = False
    metadata: ContentMetadata = ContentMetadata()


class PromotedMessage(Model):
    content_ref: ContentReference
    part_path: tuple[int, ...]


class Handoff(Model):
    pipeline: Pipeline


class ProcessingResult(Model):
    outcome: Outcome = "continue"
    emissions: tuple[Emission, ...] = ()
    handoffs: tuple[Handoff, ...] = ()
    diagnostics: tuple[str, ...] = ()
    config_writes: tuple[ConfigWrite, ...] = ()
    scan: ScanEvidence | None = None
    filing: Filing | None = None
    headers: HeaderMetadata | None = None
    text: TextContent | None = None
    evidence: tuple[AddressEvidence, ...] = ()
    mime: MimeInventory | None = None
    promotions: tuple[PromotedMessage, ...] = ()


class PluginSpec(Model):
    manifest: ProcessorManifest
    directory: Path


class InvocationResponse(Model):
    result: ProcessingResult | None = None
    error: str | None = None
    timed_out: bool = False
    _cause: Exception | None = PrivateAttr(default=None)

    @classmethod
    def failure(cls, error: Exception) -> InvocationResponse:
        response = cls(error=f"{type(error).__name__}: {error}",
                       result=ProcessingResult(scan=error.evidence) if isinstance(error, ScanFailure) else None)
        cause: BaseException | None = error
        seen: set[int] = set()
        timed_out = False
        while cause is not None and id(cause) not in seen:
            seen.add(id(cause))
            timed_out |= isinstance(cause, (TimeoutError, subprocess.TimeoutExpired))
            cause = cause.__cause__
        response = response.model_copy(update={"timed_out": timed_out})
        response._cause = error
        return response

    @property
    def cause(self) -> Exception | None:
        return self._cause


class PluginStatistics(Model):
    kind: str
    invocations: int
    total: float
    shortest: float | None
    longest: float | None
    average: float | None
    errors: int
    timeouts: int = 0

    def summary(self) -> str:
        def duration(value: float | None) -> str:
            return "n/a" if value is None else f"{value:.3f}s"
        return (f"processor {self.kind}: invocations={self.invocations} errors={self.errors} timeouts={self.timeouts} "
                f"shortest={duration(self.shortest)} longest={duration(self.longest)} "
                f"average={duration(self.average)} total={duration(self.total if self.invocations else None)}")


class RunReport(Model):
    completed: int
    pending: int
    failed: int
    aborted: int
    statistics: tuple[PluginStatistics, ...]
