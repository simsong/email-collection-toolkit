# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""Registered file-message processor."""
from mailarchiver.processing.builtin import FilingProcessor


def create_plugin() -> FilingProcessor:
    return FilingProcessor()
