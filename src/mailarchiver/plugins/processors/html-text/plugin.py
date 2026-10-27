# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Registered html-text processor.

Purpose: Expose the built-in HTML text extraction processor through its manifest factory.

How it works:
Construct the shared implementation; the runtime owns invocation and result publication.

Fundamental dependents:

- ``processing.runtime``: manifest-driven dispatch requires this factory entry point.
- ``processing.production``: its pipeline depends on searchable text derived from HTML parts.
"""
from mailarchiver.processing.builtin import HtmlProcessor


def create_plugin() -> HtmlProcessor:
    return HtmlProcessor()
