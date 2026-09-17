# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""Registered queue-content processor."""
from mailarchiver.processing.builtin import ContentHandoffProcessor


def create_plugin() -> ContentHandoffProcessor:
    return ContentHandoffProcessor()
