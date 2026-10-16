# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

# Purpose: Expose the built-in deferred content scheduling processor through its manifest
# factory.
# How it works:
# Construct the shared implementation; the runtime owns invocation and result publication.
# Fundamental dependents:
# - processing.runtime: manifest-driven dispatch requires this factory entry point.
# - processing.production: its pipeline depends on durable content handoffs after message
#   admission.

"""Registered queue-content processor."""
from mailarchiver.processing.builtin import ContentHandoffProcessor


def create_plugin() -> ContentHandoffProcessor:
    return ContentHandoffProcessor()
