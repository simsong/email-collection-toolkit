/* Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. */
"use strict";
const byId = id => document.getElementById(id);
const importing = new URLSearchParams(window.location.search).has("import");
let current = null;
let saving = false;
let dirty = false;
let initialized = false;

function render(state, replace = false) {
  if (!dirty || replace) {
    current = state;
    byId("owner-include").value = state.include.join("\n");
    byId("owner-exclude").value = state.exclude.join("\n");
  } else {
    current.editable = state.editable;
  }
  byId("changed").hidden = importing || !state.changed_since_import;
  byId("unknown").hidden = importing || state.import_rules_known;
  controls();
}

function controls() {
  for (const id of ["save", "owner-include", "owner-exclude"]) {
    byId(id).disabled = saving || !current?.editable;
  }
}

async function refreshOptions() {
  if (saving) return;
  try { render(await window.pywebview.api.status()); }
  catch (error) { showError(error); }
}

function showError(error) {
  byId("error").textContent = String(error?.message || error);
  byId("error").hidden = false;
}

async function saveOptions() {
  if (saving || !current) return;
  saving = true;
  controls();
  byId("error").hidden = true;
  try {
    const state = await window.pywebview.api.update(byId("owner-include").value, byId("owner-exclude").value, current.revision);
    dirty = false;
    render(state, true);
    byId("saved").hidden = importing;
  } catch (error) { showError(error); }
  finally { saving = false; controls(); }
}

function initialize() {
  if (initialized || !window.pywebview?.api?.status) return;
  initialized = true;
  if (importing) {
    byId("save").textContent = "Continue";
    byId("cancel").hidden = false;
    byId("persistence").textContent = "These rules will be saved as defaults when you confirm the import.";
  }
  for (const id of ["owner-include", "owner-exclude"]) {
    byId(id).addEventListener("input", () => { dirty = true; byId("saved").hidden = true; });
  }
  byId("cancel").addEventListener("click", () => window.pywebview.api.cancel());
  byId("owner-form").addEventListener("submit", event => { event.preventDefault(); saveOptions(); });
  refreshOptions();
}
window.addEventListener("pywebviewready", initialize);
window.setInterval(() => { initialize(); if (initialized) refreshOptions(); }, 1000);
