# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

# Purpose: Expose the built-in canonical filing processor through its manifest factory.
# How it works:
# Construct the shared implementation; the runtime owns invocation and result publication.
# Fundamental dependents:
# - processing.runtime: manifest-driven dispatch requires this factory entry point.
# - processing.production: its pipeline depends on typed message identity and filing
#   destinations.

"""Registered file-message processor."""
from mailarchiver.processing.builtin import FilingProcessor


def create_plugin() -> FilingProcessor:
    return FilingProcessor()
