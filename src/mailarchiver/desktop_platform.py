# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Adapt the shared Python reader to native Windows desktop services.
# Clipboard writes occur on the window's STA thread through its existing host.
# External files are explicit user exports, never original source messages.
# Require WebView2 before creating windows; never fall back to Internet Explorer.
# Keep platform imports lazy so the same reader continues to run on macOS.
"""Small native adapters for the Python webview desktop."""

from __future__ import annotations

import ctypes
from importlib import import_module
import os
from pathlib import Path
import subprocess
import sys
from typing import Any
import webbrowser


def windows_webview() -> str:
    """Initialize the installed renderer, rejecting insecure legacy fallback."""
    backend = import_module("webview.guilib").initialize("edgechromium")
    if backend.renderer != "edgechromium":
        raise RuntimeError("Microsoft Edge WebView2 Runtime is required. Install it from Microsoft and retry.")
    return str(backend.renderer)


def startup_error(message: str) -> None:
    """Make startup failures visible even when launched without a console."""
    if sys.platform == "win32":
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.MessageBoxW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
        user32.MessageBoxW.restype = ctypes.c_int
        user32.MessageBoxW(None, message, "Email Collection Toolkit", 0x10)
    elif sys.stderr is not None:
        print(message, file=sys.stderr)


def copy_windows_text(window: Any, text: str) -> None:
    """Marshal Unicode clipboard access onto the existing WinForms STA thread."""
    if window is None or window.native is None:
        raise ValueError("Clipboard requires an open reader window")
    system = import_module("System")
    forms = import_module("System.Windows.Forms")
    window.native.Invoke(system.Action(lambda: forms.Clipboard.SetText(text) if text else forms.Clipboard.Clear()))


def refresh_windows_menu(window: Any, menu: Any, has_document: bool) -> None:
    """Replace pywebview's menu strip rather than accumulating native controls."""
    system = import_module("System")
    forms = import_module("System.Windows.Forms")

    def replace() -> None:
        native = window.native
        for control in tuple(native.Controls):
            if isinstance(control, forms.MenuStrip):
                native.Controls.Remove(control)
                control.Dispose()
        native.set_window_menu(menu)
        for control in native.Controls:
            if not isinstance(control, forms.MenuStrip):
                continue
            for heading in control.Items:
                for item in heading.DropDownItems:
                    if item.Text == "New Search Window":
                        item.Enabled = has_document
                    key = {"New": forms.Keys.N, "Open…": forms.Keys.O, "Close": forms.Keys.W}.get(str(item.Text))
                    if key is not None:
                        item.ShortcutKeys = forms.Keys(int(forms.Keys.Control) | int(key))
    window.native.Invoke(system.Action(replace))


def open_export(path: Path) -> None:
    """Open an explicitly exported attachment with the platform's registered app."""
    if sys.platform == "win32":
        os.startfile(str(path))
    elif sys.platform == "darwin":
        subprocess.Popen(["/usr/bin/open", str(path)], close_fds=True)
    else:
        subprocess.Popen(["xdg-open", str(path)], close_fds=True)


def open_external_url(destination: str) -> None:
    """Open a URL already validated by the reader's shared scheme allowlist."""
    if not webbrowser.open(destination):
        raise ValueError("could not open external link")
