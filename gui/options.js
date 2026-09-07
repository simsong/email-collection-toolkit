/* Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. */
"use strict";
const byId = id => document.getElementById(id);
let current = null;
let saving = false;
let initialized = false;

function render(state) {
  const selected = new Set(Array.from(byId("owners").selectedOptions, option => option.value));
  current = state;
  byId("owners").replaceChildren(...state.names.map(name => {
    const option = document.createElement("option");
    option.value = name;
    option.textContent = name;
    option.selected = selected.has(name);
    return option;
  }));
  byId("changed").hidden = !state.changed_since_import;
  byId("unknown").hidden = state.import_names_known;
  controls();
}

function controls() {
  byId("add").disabled = saving || !current?.editable;
  byId("remove").disabled = saving || !current?.editable || !byId("owners").selectedOptions.length;
  byId("add-form").querySelector("button[type=submit]").disabled = saving || !current?.editable;
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

async function updateOptions(additions, removed) {
  if (saving || !current) return;
  saving = true;
  controls();
  byId("error").hidden = true;
  try {
    render(await window.pywebview.api.update(additions, removed, current.revision));
    byId("add-form").hidden = true;
    byId("new-names").value = "";
  } catch (error) { showError(error); }
  finally { saving = false; await refreshOptions(); }
}

function initialize() {
  if (initialized || !window.pywebview?.api?.status) return;
  initialized = true;
  byId("owners").addEventListener("change", controls);
  byId("add").addEventListener("click", () => {
    byId("add-form").hidden = false;
    byId("new-names").focus();
  });
  byId("cancel-add").addEventListener("click", () => { byId("add-form").hidden = true; });
  byId("remove").addEventListener("click", () => updateOptions("", Array.from(byId("owners").selectedOptions, option => option.value)));
  byId("add-form").addEventListener("submit", event => {
    event.preventDefault();
    if (byId("new-names").value.trim()) updateOptions(byId("new-names").value, []);
  });
  refreshOptions();
}
window.addEventListener("pywebviewready", initialize);
window.setInterval(() => { initialize(); if (initialized) refreshOptions(); }, 1000);
