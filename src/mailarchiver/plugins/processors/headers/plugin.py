# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Registered headers processor.

Purpose: Expose the built-in header projection processor through its manifest factory.

How it works:
Construct the shared implementation; the runtime owns invocation and result publication.

Fundamental dependents:

- ``processing.runtime``: manifest-driven dispatch requires this factory entry point.
- ``processing.production``: its pipeline depends on normalized header and address evidence.
"""
from mailarchiver.processing.builtin import HeaderProcessor


def create_plugin() -> HeaderProcessor:
    return HeaderProcessor()
