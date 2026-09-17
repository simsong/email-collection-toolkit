# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""Registered headers processor."""
from mailarchiver.processing.builtin import HeaderProcessor


def create_plugin() -> HeaderProcessor:
    return HeaderProcessor()
