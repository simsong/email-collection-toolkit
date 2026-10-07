/* Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. */
/* Drive the actual native webview through its shipped search form and rows.
 * A CLI-created synthetic archive supplies the expected message and body.
 * Native IPC and Rust workers answer every query without substituted results.
 * Verify search, preferences and sandboxed editor Save/Move/Drag/Separate/Reopen on WKWebView.
 * Native HTML drag events use the shipped handlers; no backend responses are replaced.
 * Editor mutation phases operate only on the integration test's disposable archive.
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
        const phase=await new Promise((resolve,reject)=>{
          const timer=setTimeout(()=>{window.removeEventListener("message",receive);reject(new Error("Missing native editor test phase"));},10000);
          function receive(event) {
            if(event.source!==parent || event.data?.type!=="ect-native-editor-phase"
              || !["read","mutate","verify"].includes(event.data.phase)) return;
            clearTimeout(timer);window.removeEventListener("message",receive);resolve(event.data.phase);
          }
          window.addEventListener("message",receive);
          parent.postMessage({type:"ect-native-editor",path:location.pathname,ready:true},"*");
        });
        const wait=async(label,predicate)=>{
          const deadline=Date.now()+10000;
          while(!predicate()) {
            if(Date.now()>deadline) throw new Error(`Editor ${label}: ${document.body.textContent}`);
            await new Promise(resolve=>setTimeout(resolve,50));
          }
        };
        const get=id=>document.getElementById(id);
        const set=(id,value)=>{get(id).value=value;get(id).dispatchEvent(new Event("input",{bubbles:true}));};
        if(phase!=="read" && location.pathname==="/options.html") {
          const expected="bob@example.test\nnative-harness@example.test";
          if(phase==="mutate") {
            set("owner-include",expected);
            get("owner-form").requestSubmit();
            await wait("owner Save",()=>!get("saved").hidden && !get("save").disabled);
          }
          if(get("owner-include").value!==expected || get("owner-exclude").value!=="")
            throw new Error("Owner Save/Reopen lost rules");
        }
        if(phase!=="read" && location.pathname==="/identity.html") {
          const label="Native Harness Alice";
          const row=email=>[...document.querySelectorAll("tr.address")].find(item=>item.querySelector(".row-label").textContent===email);
          const group=name=>[...document.querySelectorAll("tr.group")].find(item=>item.querySelector(".row-label").textContent===name);
          const saved=predicate=>wait("identity Save",()=>get("status").textContent.startsWith("Saved to the archive") && predicate());
          if(phase==="mutate") {
            const alice=group("Alice");
            if(!alice) throw new Error("Original Alice identity missing");
            alice.click();set("canonical-name",label);get("rename").click();
            await saved(()=>group(label));
            row("bob@example.test").click();
            get("destination").value=group(label).dataset.groupId;
            get("destination").dispatchEvent(new Event("change",{bubbles:true}));
            if(get("move").disabled) throw new Error("Move unavailable for selected address");
            get("move").click();
            await saved(()=>row("bob@example.test").dataset.groupId===group(label).dataset.groupId);
            row("bob@example.test").click();
            if(get("separate").disabled) throw new Error("Make separate unavailable after Move");
            get("separate").click();
            await saved(()=>row("bob@example.test").dataset.groupId!==group(label).dataset.groupId);
            // Exercise both address moves and whole-person merges through the
            // real HTML drag handlers in the opaque WKWebView editor frame.
            const drag=source=>{
              const target=group(label),dataTransfer=new DataTransfer();
              if(!source?.draggable || !target) throw new Error("Identity drag controls unavailable");
              source.dispatchEvent(new DragEvent("dragstart",{bubbles:true,cancelable:true,dataTransfer}));
              const over=new DragEvent("dragover",{bubbles:true,cancelable:true,dataTransfer});
              target.dispatchEvent(over);
              // WebKit does not grant synthetic events the native drag session's
              // effectAllowed write permission. Verify its real handlers and data;
              // physical pointer/copy-mask acceptance remains a separate gate.
              if(!over.defaultPrevented || !target.classList.contains("drop-target")
                || dataTransfer.getData("text/plain")!==source.querySelector(".row-label").textContent)
                throw new Error(`Identity drag was not accepted by the target: prevented=${over.defaultPrevented}, target=${target.classList.contains("drop-target")}, allowed=${dataTransfer.effectAllowed}, text=${dataTransfer.getData("text/plain")}, connected=${source.isConnected}/${target.isConnected}`);
              target.dispatchEvent(new DragEvent("drop",{bubbles:true,cancelable:true,dataTransfer}));
              source.dispatchEvent(new DragEvent("dragend",{bubbles:true,dataTransfer}));
            };
            for(const wholePerson of [false,true]) {
              const bob=row("bob@example.test");
              drag(wholePerson ? bob.previousElementSibling : bob);
              await saved(()=>row("bob@example.test").dataset.groupId===group(label).dataset.groupId);
              if(document.querySelector(".drop-target")) throw new Error("Identity drop target was not cleared");
              row("bob@example.test").click();get("separate").click();
              await saved(()=>row("bob@example.test").dataset.groupId!==group(label).dataset.groupId);
            }
          }
          const alice=row("alice@example.test"),bob=row("bob@example.test");
          if(!group(label) || !alice || !bob || alice.dataset.groupId!==group(label).dataset.groupId
            || alice.dataset.groupId===bob.dataset.groupId) throw new Error("Identity edits did not persist");
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
    // First drag prepares verified files on the Rust worker; the next supplies
    // an opaque token to Cocoa, never a file/link pathname supplied by the page.
    const fileWell=document.getElementById("message-file-well");
    if(!row.draggable || !fileWell.draggable) throw new Error("Native file drag controls missing");
    const drag=element=>{
      const dataTransfer=new DataTransfer();
      const event=new DragEvent("dragstart",{bubbles:true,cancelable:true,dataTransfer});
      element.dispatchEvent(event);
      return {dataTransfer,prevented:event.defaultPrevented};
    };
    if(!drag(row).prevented) throw new Error("First drag did not defer for file preparation");
    await wait("EML drag prepared",()=>/^Message-\d+\.eml$/.test(document.getElementById("message-file-name").textContent));
    const eml=drag(fileWell).dataTransfer.getData("text/plain");
    if(!eml.startsWith("mailarchiver-export:")) throw new Error("EML drag did not supply a registered token");
    input.value="from:alice@example.test";document.getElementById("search-form").requestSubmit();
    await wait("all drag messages",()=>document.getElementById("result-status").textContent==="2 messages");
    const cards=[...document.querySelectorAll("#result-list .result")];
    cards[0].click();
    // Let the ordinary row click's queued selection callback finish before the
    // modifier click, matching separate user input events rather than one JS task.
    await new Promise(resolve=>setTimeout(resolve,0));
    cards[1].dispatchEvent(new MouseEvent("click",{bubbles:true,metaKey:true,ctrlKey:true}));
    await wait("multiple drag selection",()=>!document.getElementById("message-selection-summary").hidden);
    if(!drag(fileWell).prevented) throw new Error("First ZIP drag did not defer for preparation");
    await wait("ZIP drag prepared",()=>document.getElementById("message-file-name").textContent==="Email Collection Toolkit Messages (2).zip");
    const zip=drag(fileWell).dataTransfer.getData("text/plain");
    if(!zip.startsWith("mailarchiver-export:") || zip===eml) throw new Error("ZIP drag token missing or reused");
    send("native_smoke_drag",[eml,zip]);
    await wait("Cocoa file URL writers",()=>window.__rustDragVerified);
    input.value="observatory";document.getElementById("search-form").requestSubmit();
    await wait("restore search",()=>document.getElementById("result-status").textContent==="1 message");
    document.querySelector("#result-list .result").click();
    await wait("restore message",()=>!document.getElementById("message-content").hidden
      && document.getElementById("message-subject").textContent==="Observatory planning"
      && document.getElementById("body-view").textContent.includes("Meet at the observatory on Friday."));
    const attachmentOpen=[...document.querySelectorAll("#attachment-list button")].find(button=>button.textContent==="Open");
    if(!attachmentOpen) throw new Error("Native attachment control missing");
    attachmentOpen.click();
    await wait("attachment confirmation",()=>document.querySelector('dialog[aria-label="Open attachment"]'));
    const confirmation=document.querySelector('dialog[aria-label="Open attachment"]');
    if(!confirmation.textContent.includes("native-acceptance.txt may contain active or unrecognized content"))
      throw new Error("Attachment confirmation lost its filename or warning");
    [...confirmation.querySelectorAll("button")].find(button=>button.textContent==="Cancel").click();
    await wait("attachment Cancel",()=>!confirmation.isConnected);
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
    const phase=new URLSearchParams(window.__rustWindowParameters || location.search).get("native-editors") || "read";
    if(phase!=="read" && !(capabilities.available && capabilities.write_available))
      throw new Error("Native editor acceptance requires the real writable helper");
    if(capabilities.available && capabilities.write_available) {
      const actions=[];
      for(const [action,path] of [[()=>api.open_options(),"/options.html"],[()=>api.open_picker("name"),"/identity.html"]]) {
        actions.push([action,path,phase]);
        if(phase==="mutate") actions.push([action,path,"verify"]);
      }
      if(phase==="read") actions.push([()=>api.open_ingest_window(),"/ingests.html","read"]);
      for(const [action,path,editorPhase] of actions) {
        let result;
        const received=event=>{
          const frame=document.querySelector(".rust-workflow iframe");
          if(event.source===frame?.contentWindow && event.data?.type==="ect-native-editor" && event.data.path===path) {
            if(event.data.ready) frame.contentWindow.postMessage({type:"ect-native-editor-phase",phase:editorPhase},"*");
            else result=event.data;
          }
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
