# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

# Purpose: Expose the built-in identity evidence extraction processor through its manifest
# factory.
# How it works:
# Construct the shared implementation; the runtime owns invocation and result publication.
# Fundamental dependents:
# - processing.runtime: manifest-driven dispatch requires this factory entry point.
# - processing.production: its pipeline depends on observed address and organization evidence.

"""Registered identity-evidence processor."""
from mailarchiver.processing.builtin import IdentityProcessor


def create_plugin() -> IdentityProcessor:
    return IdentityProcessor()
