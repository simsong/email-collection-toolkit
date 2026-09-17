# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""Registered html-text processor."""
from mailarchiver.processing.builtin import HtmlProcessor


def create_plugin() -> HtmlProcessor:
    return HtmlProcessor()
