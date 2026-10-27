# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Windows application preferences for worker limits and update settings.

Purpose: Present Windows controls for shared worker and update policies.

How it works:
Expose a restricted webview bridge that validates worker limits and atomically persists
explicit choices through the controller, retaining launch-only CLI overrides.

Fundamental dependents:

- ``gui_app``: Windows update settings actions depend on this preferences window.
- ``winsparkle``: installed discovery follows the settings edited through this bridge.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any
from importlib import import_module

import webview

from .updates import (PREVIEW_STREAM_LABEL, RELEASE_STREAM_LABEL, UPDATE_STREAM_HELP,
                      UpdateChannel, UpdateService)
from .worker_preferences import WorkerPreferences, WorkerSettings

WORKERS_KEY = "workers"
PreferencesSave = Callable[[UpdateChannel, bool, WorkerPreferences | None], None]

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Preferences</title>
<style>body{font:16px system-ui;margin:28px}label{display:block;margin:18px 0}select,button{font:inherit;padding:7px}#status,#update-status{white-space:pre-wrap}</style></head>
<body><h1>Preferences</h1><section id="workers-section" hidden><h2>Maximum number of workers</h2>
<fieldset id="workers-controls"><legend>Concurrent importing and saved-mail processing</legend>
<label><input type="radio" name="workers-mode" value="half"> Half the CPUs (<span id="half-cpus"></span>)</label>
<label><input type="radio" name="workers-mode" value="all"> All CPUs (<span id="all-cpus"></span>)</label>
<label><input type="radio" name="workers-mode" value="custom"> Custom
<input id="worker-count" type="number" min="1" step="1" aria-label="Custom worker count"></label></fieldset>
<p id="workers-note"></p><p id="workers-help"></p></section><h2>Updates</h2><p id="version"></p>
<label>Update stream <select id="channel"><option value="release">__RELEASE_STREAM_LABEL__</option><option value="preview">__PREVIEW_STREAM_LABEL__</option></select></label>
<p>__UPDATE_STREAM_HELP__</p>
<label><input type="checkbox" id="automatic"> Check automatically each day</label>
<p>Downloading and installation require confirmation.</p>
<p>Valid changes save automatically. Close this window when finished.</p>
<button id="check" disabled>Check for Updates…</button>
<p id="update-status" role="status"></p><p id="status" role="status"></p><script>
const status = document.getElementById('status');
let workerSettings=null, ready=false, closing=false, saveQueue=Promise.resolve(), saveRevision=0;
async function refresh(){const s=await pywebview.api.state();
if(!ready){
if(s.workers){workerSettings=s.workers;const w=workerSettings;
document.getElementById('workers-section').hidden=false;
document.getElementById('half-cpus').textContent=w.half;document.getElementById('all-cpus').textContent=w.cpus;
document.querySelector(`input[name="workers-mode"][value="${w.preferences.mode}"]`).checked=true;
const input=document.getElementById('worker-count');input.max=w.maximum;input.value=Math.min(w.preferences.custom_count,w.maximum);
document.getElementById('workers-controls').disabled=w.command_line!==null;
document.getElementById('workers-note').textContent=w.override_note;
document.getElementById('workers-help').textContent=`1–${w.maximum} workers (at most twice ${w.cpus} CPUs). Applies when starting or continuing work.`;}
document.getElementById('channel').value=s.channel;document.getElementById('automatic').checked=s.automatic_checks;
ready=true;document.body.dataset.ready='true';}
document.getElementById('version').textContent=`Version ${s.version} (build ${s.build || 'source'})`;
document.getElementById('check').disabled=closing || !s.available || ['checking','installing'].includes(s.phase);
document.getElementById('update-status').textContent=s.detail + '\\nLast checked: ' + (s.last_checked || 'Not yet');}
function showError(error){status.textContent=String(error);}
async function savePreferences(){
const revision=++saveRevision;
try{
if(!ready)throw new Error('Preferences are still loading.');
const editable=workerSettings && workerSettings.command_line===null;
const input=document.getElementById('worker-count');
if(editable && (!input.value || !input.reportValidity()))throw new Error(`Enter a whole number from 1 to ${workerSettings.maximum}.`);
const values=[document.getElementById('channel').value,document.getElementById('automatic').checked,
editable ? document.querySelector('input[name="workers-mode"]:checked').value : null,editable ? Number(input.value) : null];
saveQueue=saveQueue.catch(()=>{}).then(()=>pywebview.api.save(...values));
const accepted=await saveQueue;
if(revision===saveRevision){document.getElementById('automatic').checked=accepted;status.textContent='Settings saved.';}
}catch(error){if(revision===saveRevision)showError(error);throw error;}}
window.savePreferencesAndClose=async()=>{
if(closing)return;closing=true;
let enabled=[],revision=saveRevision;
try {if(!ready)await refresh();const pending=savePreferences();revision=saveRevision;
enabled=[...document.querySelectorAll('input,select,button')].filter(control=>!control.disabled);
enabled.forEach(control=>{control.disabled=true;});await pending;await pywebview.api.close_saved();}
catch(error){closing=false;enabled.forEach(control=>{control.disabled=false;});if(revision===saveRevision)showError(error);}};
window.addEventListener('pywebviewready',()=>{refresh().catch(showError);setInterval(()=>refresh().catch(showError),10000)});
document.querySelectorAll('input,select').forEach(control=>control.addEventListener('change',()=>{
savePreferences().catch(()=>{});}));
document.getElementById('check').onclick=async()=>{const revision=saveRevision+1;
try{await savePreferences();if(revision!==saveRevision)return;await pywebview.api.check();await refresh()}
catch(error){if(revision===saveRevision)showError(error)}};
</script></body></html>""".replace("__RELEASE_STREAM_LABEL__", RELEASE_STREAM_LABEL).replace(
    "__PREVIEW_STREAM_LABEL__", PREVIEW_STREAM_LABEL).replace("__UPDATE_STREAM_HELP__", UPDATE_STREAM_HELP)


class UpdatePreferencesApi:
    def __init__(self, service: UpdateService, save: Callable[[UpdateChannel, bool], None],
                 workers: Callable[[], WorkerSettings] | None = None, save_preferences: PreferencesSave | None = None,
                 quitting: Callable[[], bool] = lambda: False) -> None:
        self.window: Any = None
        self._close_ready = False
        self._quitting = quitting
        self._service = service
        self._save = save
        self._workers = workers
        self._save_preferences = save_preferences

    def state(self) -> dict[str, Any]:
        state = self._service.status.model_dump(mode="json")
        if self._workers is not None:
            state[WORKERS_KEY] = self._workers().model_dump(mode="json")
        return state

    def save(self, channel: str, automatic: bool, worker_mode: str | None = None, worker_count: int | None = None) -> bool:
        if channel not in {"release", "preview"} or not isinstance(automatic, bool):
            raise ValueError("Invalid update preferences")
        selected: UpdateChannel = "preview" if channel == "preview" else "release"
        workers = None
        if worker_mode is not None or worker_count is not None:
            if self._workers is None or self._save_preferences is None:
                raise ValueError("Worker preferences are unavailable")
            settings = self._workers()
            if settings.command_line is not None:
                raise ValueError(settings.override_note)
            if worker_mode is None or worker_count is None:
                raise ValueError("Choose a worker mode and custom count")
            workers = WorkerPreferences.from_input(worker_mode, worker_count, settings.cpus)
        previous = self._service.status.channel, self._service.status.automatic_checks
        self._service.configure(selected, automatic)
        try:
            if self._save_preferences is not None:
                self._save_preferences(selected, self._service.status.automatic_checks, workers)
            else:
                self._save(selected, self._service.status.automatic_checks)
        except (OSError, ValueError):
            self._service.configure(*previous)
            raise
        return self._service.status.automatic_checks

    def close_requested(self) -> bool:
        """Veto first Close, then let WebView2 flush edits without blocking its UI thread."""
        if self._close_ready or self._quitting() or not self.window.events.loaded.is_set():
            return True
        native = self.window.native

        def flush() -> None:
            if not native.IsDisposed:
                native.webview.CoreWebView2.ExecuteScriptAsync("window.savePreferencesAndClose()")

        native.BeginInvoke(import_module("System").Action(flush))
        return False

    def close_saved(self) -> None:
        self._close_ready = True
        if self.window is not None:
            self.window.destroy()

    def check(self) -> None:
        self._service.check()


def show_windows_update_preferences(service: UpdateService, save: Callable[[UpdateChannel, bool], None],
                                   workers: Callable[[], WorkerSettings] | None = None, save_preferences: PreferencesSave | None = None,
                                   quitting: Callable[[], bool] = lambda: False) -> Any:
    from .gui_app import WindowBridge

    api = UpdatePreferencesApi(service, save, workers, save_preferences, quitting)
    window = webview.create_window("Preferences", html=PAGE,
        js_api=WindowBridge(api, ("state", "save", "check", "close_saved")), width=620, height=780)
    if window is None:
        raise RuntimeError("Could not create Preferences window")
    api.window = window
    window.events.closing += api.close_requested
    return window
