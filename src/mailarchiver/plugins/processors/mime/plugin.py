# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""Registered mime processor."""
from mailarchiver.processing.builtin import MimeProcessor


def create_plugin() -> MimeProcessor:
    return MimeProcessor()
