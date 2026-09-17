# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""Registered clamav processor."""
from mailarchiver.processing.builtin import ClamAVProcessor


def create_plugin() -> ClamAVProcessor:
    return ClamAVProcessor()
