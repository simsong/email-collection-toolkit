/* Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. */
"use strict";

let processingInitialized = false;
window.addEventListener("pywebviewready", async () => {
  if (processingInitialized) return;
  processingInitialized = true;
  const api = window.pywebview.api;
  const capabilities = await api.status();
  if (capabilities.write_available === false) {
    document.querySelector(".archive-tools").hidden = true;
    for (const id of ["name-picker", "institution-picker", "processing-open"]) {
      document.getElementById(id).hidden = true;
    }
    return;
  }
  const dialog = document.getElementById("processing-dialog");
  const error = document.getElementById("processing-error");
  let statusIds = [];
  function showError(message) {
    window.mailArchiverNotice(message);
  }
  async function showWork(always = false) {
    if (!api.processing_work) return;
    try {
      const work = await api.processing_work();
      if (work.available === false) return;
      if (work.active || (!always && !(work.ingest || work.content || work.source_roots.length))) return;
      document.getElementById("processing-detail").textContent =
        `${work.ingest} ingest jobs, ${work.content} message/content jobs, ${work.failed || 0} failed jobs; ${work.source_roots.length} unfinished source imports. Saved import settings will be used.`;
      statusIds = (work.imports || []).flatMap(item => item.status_ids);
      const imports = document.getElementById("processing-imports");
      imports.replaceChildren();
      for (const item of work.imports || []) {
        const section = document.createElement("section");
        const heading = document.createElement("h3");
        heading.textContent = item.sources.join("; ");
        const detail = document.createElement("p");
        detail.textContent = `Import started: ${new Date(item.started_at).toLocaleString()}\n` +
          `${item.interruption_estimated ? "Last saved progress (interruption time unknown)" : "Last interrupted"}: ${new Date(item.interrupted_at).toLocaleString()}\n` +
          `Messages imported: ${item.archived.toLocaleString()} · Duplicates ignored: ${item.duplicates.toLocaleString()}`;
        section.append(heading, detail);
        imports.append(section);
      }
      error.textContent = "";
      if (!dialog.open) dialog.showModal();
    } catch (failure) { showError(`Could not read processing status: ${failure.message || failure}`); }
  }
  document.getElementById("processing-open").addEventListener("click", () => showWork(true));
  document.getElementById("processing-later").addEventListener("click", () => dialog.close());
  document.getElementById("processing-abort").addEventListener("click", async event => {
    event.target.disabled = true;
    try {
      if (!api.abort_processing || !await api.abort_processing(statusIds)) throw new Error("Saved import could not be stopped.");
      dialog.close();
    } catch (failure) { error.textContent = failure.message || String(failure); }
    finally { event.target.disabled = false; }
  });
  document.getElementById("processing-continue").addEventListener("click", async event => {
    event.target.disabled = true;
    window.mailArchiverProcessingStarting(true);
    try {
      if (!await api.resume_processing(true, true)) throw new Error("Processing could not start. Check About for details; another writer may be active.");
      dialog.close();
    } catch (failure) { error.textContent = failure.message || String(failure); }
    finally {
      event.target.disabled = false;
      window.mailArchiverProcessingStarting(false);
    }
  });
  for (const kind of ["name", "institution"]) document.getElementById(`${kind}-picker`).addEventListener("click", async () => {
    try { await api.open_picker(kind); }
    catch (failure) { showError(failure.message || String(failure)); }
  });
  const parameters = new URLSearchParams(window.__rustWindowParameters || location.search);
  if (!["message", "query", "identity-messages"].some(key => parameters.has(key))) void showWork();
});
