"""Objective-C superclass dispatch used by the native quit delegate."""
from typing import Protocol

class _TerminationDelegate(Protocol):
    def applicationShouldTerminate_(self, app: object) -> int: ...

def super(cls: type, instance: object) -> _TerminationDelegate: ...
