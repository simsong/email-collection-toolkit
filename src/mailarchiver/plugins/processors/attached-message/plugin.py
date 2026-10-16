# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

# Purpose: Expose the built-in attached-message promotion processor through its manifest
# factory.
# How it works:
# Construct the shared implementation; the runtime owns invocation and result publication.
# Fundamental dependents:
# - processing.runtime: manifest-driven dispatch requires this factory entry point.
# - processing.production: its pipeline depends on intact nested-message bytes and parent
#   provenance.

"""Registered attached-message processor."""
from mailarchiver.processing.builtin import AttachedMessageProcessor


def create_plugin() -> AttachedMessageProcessor:
    return AttachedMessageProcessor()
