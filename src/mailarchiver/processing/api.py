# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""Typed API v2 processor boundary, independent of GUI and production ingest."""
from __future__ import annotations

from pathlib import Path
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator

from ..plugin_configuration import ConfigScope, ConfigValues, ConfigWrite, PluginConfiguration, ReadScope

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


class ApplicationContext(Model):
    """Headless service identity; writes are requested through typed results."""
    api_version: Literal[2] = 2


class ProcessingObject(Model):
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

    _configuration: PluginConfiguration | None = PrivateAttr(default=None)

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


class Emission(Model):
    content_ref: ContentReference
    content_type: str
    part_path: tuple[int, ...] = ()
    scope: Scope = "body"
    synthetic: bool = False


class Handoff(Model):
    pipeline: Pipeline


class ProcessingResult(Model):
    outcome: Outcome = "continue"
    emissions: tuple[Emission, ...] = ()
    handoffs: tuple[Handoff, ...] = ()
    diagnostics: tuple[str, ...] = ()
    config_writes: tuple[ConfigWrite, ...] = ()


class PluginSpec(Model):
    manifest: ProcessorManifest
    directory: Path


class InvocationRequest(Model):
    plugin: PluginSpec
    item: ProcessingObject
    configuration: PluginConfiguration = Field(default_factory=PluginConfiguration)


class InvocationResponse(Model):
    result: ProcessingResult | None = None
    error: str | None = None


class PluginStatistics(Model):
    kind: str
    invocations: int
    total: float
    shortest: float | None
    longest: float | None
    average: float | None
    errors: int


class RunReport(Model):
    completed: int
    pending: int
    failed: int
    aborted: int
    statistics: tuple[PluginStatistics, ...]
