/* Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. */
/* Drive the actual native webview through its shipped search form and rows.
 * A CLI-created synthetic archive supplies the expected message and body.
 * Native IPC and Rust workers answer every query without substituted results.
 * Verify search, preferences and live sandboxed workflow editors on WKWebView.
 * Failures return through the same origin-checked IPC to fail the native process.
 * This driver is embedded only with the explicit native-smoke Cargo feature.
 */
window.addEventListener("DOMContentLoaded", async () => {
  if(location.pathname === "/opening.html") return;
  if(window !== window.top) {
    if(!["/identity.html","/options.html","/ingests.html"].includes(location.pathname)) return;
    let reported=false;
    const report=async()=>{
      if(reported) return;
      reported=true;
      try {
        if(!window.pywebview?.api) throw new Error("Editor capability port was not initialized");
        let isolated=false;
        try { void parent.pywebview.api; } catch(error) { isolated=error.name === "SecurityError"; }
        if(!isolated) throw new Error("Editor can access the parent API");
        let evalBlocked=false;
        try { window.eval("1+1"); } catch(error) { evalBlocked=error.name === "EvalError"; }
        if(!evalBlocked) throw new Error("Editor policy permits eval");
        const deadline=Date.now()+10000;
        const ready=()=>location.pathname === "/options.html" ? !document.getElementById("save").disabled :
          location.pathname === "/identity.html" ? document.getElementById("status").textContent.startsWith("Loaded archive identities") :
          document.getElementById("history-count").textContent === "1 run";
        while(!ready()) {
          if(Date.now()>deadline) throw new Error(`Editor did not load: ${document.body.textContent}`);
          await new Promise(resolve=>setTimeout(resolve,50));
        }
        parent.postMessage({type:"ect-native-editor",path:location.pathname},"*");
      } catch(error) { parent.postMessage({type:"ect-native-editor",path:location.pathname,error:String(error)},"*"); }
    };
    if(window.pywebview?.api) void report();
    else window.addEventListener("pywebviewready",report,{once:true});
    window.setTimeout(()=>{ if(!reported) void report(); },10000);
    return;
  }
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
    window.__rustShellAction("about");
    const dialog=document.getElementById("rust-shell-dialog");
    await wait("About health",()=>document.getElementById("rust-antivirus-status") || dialog.querySelector('[role="alert"]').textContent);
    const refresh=[...dialog.querySelectorAll("button")].some(button=>button.textContent==="Update virus definitions");
    if (refresh !== Boolean(capabilities.available && capabilities.write_available)) throw new Error("Definition refresh capability mismatch");
    dialog.close();
    window.__rustShellAction("preferences");
    await wait("Preferences loaded",()=>dialog.querySelector('input[type="number"]'));
    const size=dialog.querySelector('input[type="number"]');
    const edited=Number(size.value)===18?16:18;
    size.value=String(edited);
    [...dialog.querySelectorAll("button")].find(button=>button.textContent==="Save").click();
    await wait("Preferences saved",()=>!dialog.open);
    if(document.documentElement.style.getPropertyValue("--rust-message-font-size")!==`${edited}px`)
      throw new Error("Saved message font size was not applied");
    const saved=await api.shell_status();
    if(saved.preferences.message_font_size!==edited) throw new Error("Native preferences did not persist");
    window.__rustShellAction("preferences");
    await wait("Preferences reopened",()=>dialog.querySelector('input[type="number"]'));
    if(Number(dialog.querySelector('input[type="number"]').value)!==edited) throw new Error("Reopened Preferences show stale settings");
    dialog.close();
    if(capabilities.available && capabilities.write_available) {
      for(const [action,path] of [[()=>api.open_options(),"/options.html"],
        [()=>api.open_picker("name"),"/identity.html"],[()=>api.open_ingest_window(),"/ingests.html"]]) {
        let result;
        const received=event=>{
          const frame=document.querySelector(".rust-workflow iframe");
          if(event.source===frame?.contentWindow && event.data?.type==="ect-native-editor" && event.data.path===path)
            result=event.data;
        };
        window.addEventListener("message",received);
        try {
          await action();
          await wait(`native editor ${path}`,()=>result);
          if(result.error) throw new Error(result.error);
        } finally {
          window.removeEventListener("message",received);
          document.querySelector(".rust-workflow")?.close();
        }
      }
    }
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    send("native_smoke_ready");
  } catch (error) {
    send("native_smoke_failed", [String(error)]);
  }
});
