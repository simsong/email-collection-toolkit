# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""Scan results and the infected-message headers used by API producers."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

ScanStatus = Literal["clean", "infected", "not-scanned", "unscannable", "scanner-error"]

DETECTION_HEADER = "X-ClamAV-Detection"
ENGINE_HEADER = "X-ClamAV-Engine-Version"
DEFINITIONS_HEADER = "X-ClamAV-Definitions-Version"


class ScanEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    status: ScanStatus
    detail: str = ""
    engine_version: str | None = None
    signature_version: str | None = None


class ScanFailure(RuntimeError):
    def __init__(self, evidence: ScanEvidence) -> None:
        super().__init__(evidence.detail)
        self.evidence = evidence
