/* Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
 * Edit all registered YAML lists through one revision-aware bridge.
 * List and scope changes explicitly load their current resolved configuration.
 * Save/reset send the displayed revision so concurrent edits cannot be lost.
 * Busy controls prevent selection races and preserve failed edits for correction.
 * No background refresh replaces text while the user is editing it.
 */
"use strict";
const byId = id => document.getElementById(id);
let current = null, catalog = [], busy = false, initialized = false;
function controls() {
  for (const id of ["list", "scope", "reload"]) byId(id).disabled = busy || !catalog.length;
  for (const id of ["yaml", "save", "reset"]) byId(id).disabled = busy || !current;
  byId("scope").querySelector('[value="archive"]').disabled = !catalog.find(item => item.key === byId("list").value)?.archive_supported;
}
function render(state) {
  current = state;
  byId("yaml").value = state.text;
  byId("description").textContent = state.description;
  byId("origin").textContent = `Source: ${state.origin} · Revision: ${state.revision.slice(0, 12)}`;
  byId("paths").textContent = `Application: ${state.packaged_path}\nPersonal: ${state.personal_path}`;
  byId("setup").hidden = !state.needs_customization;
  byId("reset").textContent = state.scope === "personal" ? "Reset to application template" : "Copy current personal defaults";
}
async function perform(action) {
  if (busy) return;
  busy = true; controls(); byId("error").hidden = true; byId("status").textContent = "";
  try { await action(); }
  catch (error) { byId("error").textContent = String(error?.message || error); byId("error").hidden = false; }
  finally { busy = false; controls(); }
}
async function load() {
  await perform(async () => {
    const key = byId("list").value;
    if (!catalog.find(item => item.key === key).archive_supported) byId("scope").value = "personal";
    // Failed loading must never leave another list's revision available to Save.
    current = null;
    render(await window.pywebview.api.load({key, scope: byId("scope").value}));
  });
}
async function save(reset) {
  if (!current) return;
  await perform(async () => {
    render(await window.pywebview.api.update({key: current.key, scope: current.scope,
      text: byId("yaml").value, revision: current.revision, reset}));
    byId("status").textContent = "Saved.";
  });
}
async function initialize() {
  if (initialized || !window.pywebview?.api?.catalog) return;
  initialized = true;
  try {
    catalog = await window.pywebview.api.catalog();
    for (const item of catalog) byId("list").add(new Option(item.title, item.key));
    byId("list").addEventListener("change", load); byId("scope").addEventListener("change", load);
    byId("save").addEventListener("click", () => save(false)); byId("reset").addEventListener("click", () => save(true));
    byId("reload").addEventListener("click", load);
    await load();
  } catch (error) { byId("error").textContent = String(error); byId("error").hidden = false; }
}
window.addEventListener("pywebviewready", initialize);
initialize();
