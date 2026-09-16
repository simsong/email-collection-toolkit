# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""Executable framework fixture, not a production processor."""
from mailarchiver.processing.api import ProcessingResult

class Plugin:
    def process(self, item):
        with item.message_ref.open() as source:
            assert source.read(1)
        return ProcessingResult(diagnostics=("observed body",))

def create_plugin():
    return Plugin()
