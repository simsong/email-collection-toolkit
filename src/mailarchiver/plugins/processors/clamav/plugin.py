# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Registered clamav processor.

Purpose: Expose the built-in antivirus admission processor through its manifest factory.

How it works:
Construct the shared implementation; the runtime owns invocation and result publication.

Fundamental dependents:

- ``processing.runtime``: manifest-driven dispatch requires this factory entry point.
- ``processing.production``: its pipeline depends on typed scan evidence and quarantine decisions.
"""
from mailarchiver.processing.builtin import ClamAVProcessor


def create_plugin() -> ClamAVProcessor:
    return ClamAVProcessor()
