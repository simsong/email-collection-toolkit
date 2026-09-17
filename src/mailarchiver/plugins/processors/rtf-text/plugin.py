# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""Registered rtf-text processor."""
from mailarchiver.processing.builtin import RtfProcessor


def create_plugin() -> RtfProcessor:
    return RtfProcessor()
