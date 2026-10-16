# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

# Purpose: Expose the built-in MIME expansion processor through its manifest factory.
# How it works:
# Construct the shared implementation; the runtime owns invocation and result publication.
# Fundamental dependents:
# - processing.runtime: manifest-driven dispatch requires this factory entry point.
# - processing.production: its pipeline depends on bounded part discovery and attached-message
#   handoffs.

"""Registered mime processor."""
from mailarchiver.processing.builtin import MimeProcessor


def create_plugin() -> MimeProcessor:
    return MimeProcessor()
