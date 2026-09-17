# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""Registered text-index processor."""
from mailarchiver.processing.builtin import TextProcessor


def create_plugin() -> TextProcessor:
    return TextProcessor()
