# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""Registered identity-evidence processor."""
from mailarchiver.processing.builtin import IdentityProcessor


def create_plugin() -> IdentityProcessor:
    return IdentityProcessor()
