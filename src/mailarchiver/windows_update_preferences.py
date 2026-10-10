# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Present update preferences using the application's existing webview toolkit.
# Store channel and automatic-check choices through the shared controller.
# Expose only update status and explicitly chosen update operations to the page.
# Native WinSparkle dialogs retain download and installation confirmation.
# The page contains no remote content and displays diagnostics as plain text.
"""Windows update settings for the common Python update service."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

import webview

from .updates import (PREVIEW_STREAM_LABEL, RELEASE_STREAM_LABEL, UPDATE_STREAM_HELP,
                      UpdateChannel, UpdateService)

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Update Settings</title>
<style>body{font:16px system-ui;margin:28px}label{display:block;margin:18px 0}select,button{font:inherit;padding:7px}#status{white-space:pre-wrap}</style></head>
<body><h1>Updates</h1><p id="version"></p>
<label>Update stream <select id="channel"><option value="release">__RELEASE_STREAM_LABEL__</option><option value="preview">__PREVIEW_STREAM_LABEL__</option></select></label>
<p>__UPDATE_STREAM_HELP__</p>
<label><input type="checkbox" id="automatic"> Check automatically each day</label>
<p>Downloading and installation require confirmation.</p>
<button id="save">Save settings</button> <button id="check">Check for Updates…</button>
<p id="status" role="status"></p><script>
const status = document.getElementById('status');
async function refresh(){const s=await pywebview.api.state();
document.getElementById('version').textContent=`Version ${s.version} (build ${s.build || 'source'})`;
document.getElementById('channel').value=s.channel;document.getElementById('automatic').checked=s.automatic_checks;
status.textContent=s.detail + '\\nLast checked: ' + (s.last_checked || 'Not yet');}
window.addEventListener('pywebviewready',()=>{refresh();setInterval(refresh,10000)});
document.getElementById('save').onclick=async()=>{try{await pywebview.api.save(document.getElementById('channel').value,document.getElementById('automatic').checked);await refresh()}catch(e){status.textContent=String(e)}};
document.getElementById('check').onclick=async()=>{await pywebview.api.check();await refresh()};
</script></body></html>""".replace("__RELEASE_STREAM_LABEL__", RELEASE_STREAM_LABEL).replace(
    "__PREVIEW_STREAM_LABEL__", PREVIEW_STREAM_LABEL).replace("__UPDATE_STREAM_HELP__", UPDATE_STREAM_HELP)


class UpdatePreferencesApi:
    def __init__(self, service: UpdateService, save: Callable[[UpdateChannel, bool], None]) -> None:
        self._service = service
        self._save = save

    def state(self) -> dict[str, Any]:
        return self._service.status.model_dump(mode="json")

    def save(self, channel: str, automatic: bool) -> None:
        if channel not in {"release", "preview"} or not isinstance(automatic, bool):
            raise ValueError("Invalid update preferences")
        selected: UpdateChannel = "preview" if channel == "preview" else "release"
        previous = self._service.status.channel, self._service.status.automatic_checks
        self._service.configure(selected, automatic)
        try:
            self._save(selected, self._service.status.automatic_checks)
        except OSError:
            self._service.configure(*previous)
            raise

    def check(self) -> None:
        self._service.check()


def show_windows_update_preferences(service: UpdateService, save: Callable[[UpdateChannel, bool], None]) -> None:
    webview.create_window("Update Settings", html=PAGE, js_api=UpdatePreferencesApi(service, save),
                          width=580, height=470)
