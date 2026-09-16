# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""Executable framework fixture, not a production processor."""
from mailarchiver.processing.api import ProcessingResult, Emission

class Plugin:
    def process(self, item):
        return ProcessingResult(emissions=(Emission(content_ref=item.content_ref, content_type="text/plain", part_path=(1,)), Emission(content_ref=item.content_ref, content_type="application/octet-stream", part_path=(2,), scope="attachment")))

def create_plugin():
    return Plugin()
