# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

# Purpose: Expose the built-in RTF text extraction processor through its manifest factory.
# How it works:
# Construct the shared implementation; the runtime owns invocation and result publication.
# Fundamental dependents:
# - processing.runtime: manifest-driven dispatch requires this factory entry point.
# - processing.production: its pipeline depends on searchable text derived from RTF parts.

"""Registered rtf-text processor."""
from mailarchiver.processing.builtin import RtfProcessor


def create_plugin() -> RtfProcessor:
    return RtfProcessor()
