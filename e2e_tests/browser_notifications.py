# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Deliver the native status event at the headless browser transport boundary.
# Browser fixtures call real application APIs but do not create native windows.
# After acknowledging a real backend change, they deliver the missing native push.
# The shipped consumer still reads actual files, databases and application state.
# Assertions await rendered results; this helper supplies no polling or fake state.
# Actual Cocoa and WebView2 delivery remain separate native acceptance checks.
"""Headless transport for acknowledged native status changes."""
from playwright.sync_api import Page

STATUS_CHANGED_EVENT = "mailarchiver-status-changed"


def notify_status(*pages: Page) -> None:
    for page in pages:
        page.evaluate("event => window.dispatchEvent(new Event(event))", STATUS_CHANGED_EVENT)
