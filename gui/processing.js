/* Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. */
"use strict";

window.addEventListener("pywebviewready", () => {
  const api = window.pywebview.api;
  const dialog = document.getElementById("processing-dialog");
  const error = document.getElementById("processing-error");
  function showError(message) {
    const notice = document.getElementById("error");
    notice.textContent = message; notice.hidden = false;
  }
  async function showWork(always = false) {
    if (!api.processing_work) return;
    try {
      const work = await api.processing_work();
      if (work.active || (!always && !(work.ingest || work.content || work.source_roots.length))) return;
      document.getElementById("processing-detail").textContent =
        `${work.ingest} ingest jobs, ${work.content} message/content jobs, ${work.failed || 0} failed jobs; ${work.source_roots.length} unfinished source imports. Saved import settings will be used.`;
      error.textContent = "";
      document.getElementById("continue-ingest").checked = true;
      document.getElementById("continue-content").checked = true;
      if (!dialog.open) dialog.showModal();
    } catch (failure) { showError(`Could not read processing status: ${failure.message || failure}`); }
  }
  document.getElementById("processing-open").addEventListener("click", () => showWork(true));
  document.getElementById("processing-later").addEventListener("click", () => dialog.close());
  document.getElementById("processing-continue").addEventListener("click", async event => {
    if (!document.getElementById("continue-ingest").checked && !document.getElementById("continue-content").checked) {
      dialog.close(); return;
    }
    event.target.disabled = true;
    try {
      if (!await api.resume_processing(document.getElementById("continue-ingest").checked,
        document.getElementById("continue-content").checked)) throw new Error("Processing could not start. Check About for details; another writer may be active.");
      dialog.close();
    } catch (failure) { error.textContent = failure.message || String(failure); }
    finally { event.target.disabled = false; }
  });
  for (const kind of ["name", "institution"]) document.getElementById(`${kind}-picker`).addEventListener("click", async () => {
    try { await api.open_picker(kind); }
    catch (failure) { showError(failure.message || String(failure)); }
  });
  if (!new URLSearchParams(location.search).has("message")) void showWork();
});
