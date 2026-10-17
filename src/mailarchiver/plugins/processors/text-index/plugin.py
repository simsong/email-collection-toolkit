# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Registered text-index processor.

Purpose: Expose the built-in plain-text indexing processor through its manifest factory.

How it works:
Construct the shared implementation; the runtime owns invocation and result publication.

Fundamental dependents:

- ``processing.runtime``: manifest-driven dispatch requires this factory entry point.
- ``processing.production``: its pipeline depends on derived text for the rebuildable content
  index.
"""
from mailarchiver.processing.builtin import TextProcessor


def create_plugin() -> TextProcessor:
    return TextProcessor()
