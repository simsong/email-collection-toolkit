/* Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. */
/* Drive the actual native webview through its shipped search form and rows.
 * A CLI-created synthetic archive supplies the expected message and body.
 * Native IPC and Rust workers answer every query without substituted results.
 * Wait for visible content and a completed search before requesting a snapshot.
 * Failures return through the same origin-checked IPC to fail the native process.
 * This driver is embedded only with the explicit native-smoke Cargo feature.
 */
window.addEventListener("DOMContentLoaded", async () => {
  const send = (method, args = []) => window.ipc.postMessage(JSON.stringify({id: 0, method, args}));
  const wait = async (label, predicate) => {
    const deadline = Date.now() + 30000;
    while (!predicate()) {
      if (Date.now() > deadline) throw new Error(`Timed out: ${label}; status=${document.getElementById("result-status").textContent}; error=${document.getElementById("error").textContent}; staged=${JSON.stringify(window.__ectStageStatus)}; advances=${JSON.stringify(window.__ectStageAdvances)}; visible=${document.visibilityState}; focused=${document.hasFocus()}`);
      await new Promise(resolve => setTimeout(resolve, 50));
    }
  };
  try {
    const input = document.getElementById("search");
    await wait("startup", () => !input.disabled && document.querySelector(".tabulator"));
    const api=window.pywebview.api, status=api.search_status, advance=api.search_advance;
    window.__ectStageAdvances=[];
    api.search_status=async(...args)=>{const result=await status(...args);window.__ectStageStatus=result;return result;};
    api.search_advance=async(...args)=>{window.__ectStageAdvances.push(args);return advance(...args);};
    const capabilities = await window.pywebview.api.engine_status();
    await wait("native menu capabilities", () => window.__rustMenuState);
    if (window.__rustMenuState.writes !== Boolean(capabilities.available && capabilities.write_available)
      || window.__rustMenuState.history !== Boolean(capabilities.available)) throw new Error("Native menu capability mismatch");
    input.value = "observatory";
    document.getElementById("search-form").requestSubmit();
    await wait("search", () => document.getElementById("result-status").textContent === "1 message");
    const row = document.querySelector("#result-list .result");
    if (!row || !row.textContent.includes("Observatory planning")) throw new Error("Expected search result missing");
    row.click();
    await wait("message display", () => document.getElementById("message-subject").textContent === "Observatory planning"
      && document.getElementById("body-view").textContent.includes("Meet at the observatory on Friday."));
    if (!document.getElementById("error").hidden) throw new Error(document.getElementById("error").textContent);
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    send("native_smoke_ready");
  } catch (error) {
    send("native_smoke_failed", [String(error)]);
  }
});
