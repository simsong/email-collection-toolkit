# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""Registered attached-message processor."""
from mailarchiver.processing.builtin import AttachedMessageProcessor


def create_plugin() -> AttachedMessageProcessor:
    return AttachedMessageProcessor()
