/* Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. */
/* The search window's Search / Sources tabs. Sources lists every imported directory or
   file and every IMAP account with a checkbox (included in Rescan Imports…) and, for
   IMAP, a Config… dialog for server, port, security, username and password. Edits go
   through the Python bridge, which holds the archive writer lease; passwords are only
   passed on to be verified and stored in the system keychain. */
"use strict";

let sourcesInitialized = false;
window.addEventListener("pywebviewready", () => {
  if (sourcesInitialized) return;
  sourcesInitialized = true;
  const api = window.pywebview.api;
  const byId = id => document.getElementById(id);
  const tabs = {search: byId("tab-search"), sources: byId("tab-sources")};
  const searchView = [document.querySelector("#app > .toolbar"), document.querySelector("#app > .workspace")];
  const panel = byId("sources-panel");
  const dialog = byId("imap-config-dialog");
  let editing = null;

  function selectTab(name, focus = false) {
    for (const [key, tab] of Object.entries(tabs)) {
      const selected = key === name;
      tab.setAttribute("aria-selected", String(selected));
      tab.tabIndex = selected ? 0 : -1;
      if (selected && focus) tab.focus();
    }
    for (const element of searchView) element.hidden = name !== "search";
    panel.hidden = name !== "sources";
    if (name === "sources") void renderSources();
  }
  tabs.search.addEventListener("click", () => selectTab("search"));
  tabs.sources.addEventListener("click", () => selectTab("sources"));
  for (const tab of Object.values(tabs)) tab.addEventListener("keydown", event => {
    if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    event.preventDefault();
    selectTab(tabs.search.getAttribute("aria-selected") === "true" ? "sources" : "search", true);
  });

  async function renderSources() {
    let sources = [];
    try { sources = await api.sources(); }
    catch (failure) { window.mailArchiverNotice(`Could not read sources: ${failure.message || failure}`); return; }
    const list = byId("source-list");
    list.replaceChildren(...sources.map(sourceRow));
    byId("sources-empty").hidden = sources.length > 0;
  }

  function sourceRow(source) {
    const item = document.createElement("li");
    item.dataset.sourceId = source.id;
    const check = document.createElement("input");
    check.type = "checkbox";
    check.checked = source.enabled;
    check.setAttribute("aria-label", `Include ${source.label} in Rescan Imports`);
    check.addEventListener("change", async () => {
      check.disabled = true;
      try { await api.set_source_enabled(source.id, check.checked); }
      catch (failure) {
        check.checked = !check.checked;
        window.mailArchiverNotice(failure.message || String(failure));
      } finally { check.disabled = false; }
    });
    const name = document.createElement("span");
    name.className = "source-name";
    name.textContent = source.kind === "imap" ? `IMAP: ${source.label}` : source.label;
    const detail = document.createElement("span");
    detail.className = "source-detail";
    detail.textContent = source.detail;
    if (!source.available) {
      const missing = document.createElement("span");
      missing.className = "source-unavailable";
      missing.textContent = " (not available)";
      detail.append(missing);
    }
    const actions = document.createElement("span");
    if (source.kind === "imap") {
      const config = document.createElement("button");
      config.type = "button";
      config.textContent = "Config…";
      config.addEventListener("click", () => openConfig(source));
      actions.append(config);
    }
    item.append(check, name, actions, detail);
    return item;
  }

  function openConfig(source) {
    editing = source;
    byId("imap-config-server").value = source.server || "";
    byId("imap-config-port").value = String(source.port || 993);
    byId("imap-config-tls").value = source.tls || "implicit";
    byId("imap-config-username").value = source.username || "";
    byId("imap-config-password").value = "";
    byId("imap-config-error").textContent = "";
    dialog.showModal();
  }
  byId("imap-config-cancel").addEventListener("click", () => dialog.close());
  dialog.addEventListener("close", () => { byId("imap-config-password").value = ""; editing = null; });
  byId("imap-config-form").addEventListener("submit", async event => {
    event.preventDefault();
    if (!editing) return;
    const save = byId("imap-config-save");
    save.disabled = true;
    byId("imap-config-error").textContent = "";
    try {
      await api.save_imap_source(editing.id, byId("imap-config-server").value, Number(byId("imap-config-port").value),
        byId("imap-config-username").value, byId("imap-config-tls").value, byId("imap-config-password").value);
      dialog.close();
      await renderSources();
    } catch (failure) {
      byId("imap-config-error").textContent = failure.message || String(failure);
    } finally {
      byId("imap-config-password").value = "";
      save.disabled = false;
    }
  });
  window.addEventListener("mailarchiver-status-changed", () => { if (!panel.hidden) void renderSources(); });
});
