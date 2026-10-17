# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Registered attached-message processor.

Purpose: Expose the built-in attached-message promotion processor through its manifest
factory.

How it works:
Construct the shared implementation; the runtime owns invocation and result publication.

Fundamental dependents:

- ``processing.runtime``: manifest-driven dispatch requires this factory entry point.
- ``processing.production``: its pipeline depends on intact nested-message bytes and parent
  provenance.
"""
from mailarchiver.processing.builtin import AttachedMessageProcessor


def create_plugin() -> AttachedMessageProcessor:
    return AttachedMessageProcessor()
