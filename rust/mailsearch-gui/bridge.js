/* Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. */
/* Connect the unchanged ECT frontend to its read-only Rust command worker.
 * Each promise has a request ID, so concurrent frontend operations stay distinct.
 * Native IPC and the headless test transport both use this exact adapter.
 * The original frontend retains its stale-response and message-selection guards.
 * Unsupported controls are disabled and the experiment's limits stay visible.
 * No archive data is stored in JavaScript persistence or sent to remote services.
 */
(() => {
  if (window !== window.top) return;
  const pending = new Map();
  let nextId = 0;
  window.__rustReply = reply => {
    const item = pending.get(reply.id);
    if (!item) return;
    pending.delete(reply.id);
    if (reply.error) item.reject(new Error(reply.error));
    else item.resolve(reply.result);
  };
  function invoke(method, args) {
    return new Promise((resolve, reject) => {
      if (pending.size >= 64) { reject(new Error("Reader is busy; wait for the current search.")); return; }
      const id = ++nextId;
      pending.set(id, {resolve, reject});
      const request = JSON.stringify({id, method, args});
      if (window.__rustTestTransport) {
        window.__rustTestTransport(request).then(window.__rustReply).catch(error => {
          pending.delete(id); reject(error);
        });
      } else window.ipc.postMessage(request);
    });
  }
  const names = ["status", "activate", "search", "search_start", "search_status", "search_advance", "search_page", "search_cancel", "message", "part", "request_previews", "take_previews",
    "suggestions", "ingest_overview", "saved_filter_sets", "open_message_window"];
  const api = Object.fromEntries(names.map(name => [name, (...args) => invoke(name, args)]));
  async function copy(text) {
    const field = document.createElement("textarea");
    field.value = text;
    document.body.append(field); field.select();
    const copied = document.execCommand("copy"); field.remove();
    if (!copied) throw new Error("Clipboard copy unavailable; select the text and use Command-C.");
    return true;
  }
  api.copy_visible_text = copy;
  api.copy_link = copy;
  window.pywebview = {api};
  window.addEventListener("keydown", event => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "q") {
      event.preventDefault(); void invoke("quit", []);
    }
  });
  window.addEventListener("DOMContentLoaded", () => {
    const notice = document.createElement("span");
    notice.className = "status";
    notice.textContent = "Rust preview · read-only · text display";
    notice.title = "Words, phrases and address/subject filters are supported. HTML is shown as text. Attachment viewing and exports are not yet available.";
    notice.style.marginLeft = "auto";
    document.querySelector(".archive-tools").append(notice);
    document.getElementById("ingest-status-line").style.display = "none";
    document.querySelector(".workspace").style.height = "calc(100% - 84px)";
    for (const id of ["search-attachments", "show-original-folders", "name-picker", "institution-picker", "processing-open", "save-message", "print-message", "link-open"]) {
      const control = document.getElementById(id);
      control.disabled = true;
      control.title = "Not yet available in the read-only Rust experiment";
    }
    document.getElementById("search").placeholder = 'Search mail — from:alice@example.com subject:"annual report"';
    const help = document.getElementById("search-help-template").content;
    for (const row of help.querySelectorAll("tr")) {
      if (/date:|before:|after:/.test(row.textContent)) row.remove();
    }
    for (const paragraph of help.querySelectorAll("p")) {
      if (/Dates cover|Select.*Search attachments|Combine operators/.test(paragraph.textContent)) paragraph.remove();
    }
    window.dispatchEvent(new Event("pywebviewready"));
  });
})();
