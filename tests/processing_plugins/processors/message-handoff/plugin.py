# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""Executable framework fixture, not a production processor."""
from mailarchiver.processing.api import ProcessingResult, Handoff

class Plugin:
    def process(self, item):
        return ProcessingResult(handoffs=(Handoff(pipeline="content"),))

def create_plugin():
    return Plugin()
