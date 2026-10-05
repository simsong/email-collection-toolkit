/* Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. */
/* Connect the shared ECT frontend to Rust readers and supervised archive services.
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
    "suggestions", "ingest_overview", "saved_filter_sets", "save_filter_set", "rename_filter_set", "delete_filter_set", "mailbox_tree", "attachment", "open_link", "copy_source_path", "save_message", "save_attachment", "open_attachment", "open_message_window", "new_search_window", "open_archive", "open_recent",
    "shell_status", "preferences_save", "check_updates", "engine_status", "processing_work", "resume_processing", "history", "antivirus", "options_status", "options_update", "identity_query", "identity_update", "job_status", "stop_import", "prepare_import", "start_import", "new_archive", "refresh_definitions"];
  const api = Object.fromEntries(names.map(name => [name, (...args) => invoke(name, args)]));
  async function copy(text) {
    const field = document.createElement("textarea");
    field.value = text;
    document.body.append(field); field.select();
    const copied = document.execCommand("copy"); field.remove();
    if (!copied) throw new Error("Clipboard copy unavailable; select the text and use Ctrl-C or Command-C.");
    return true;
  }
  api.copy_visible_text = copy;
  api.copy_link = copy;
  window.pywebview = {api};
  window.print = () => { void invoke("print", []).catch(e => window.mailArchiverNotice(e.message)); };
  window.addEventListener("keydown", event => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "o") {
      event.preventDefault(); void api.open_archive().catch(e => window.mailArchiverNotice(e.message));
    }
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "q") {
      event.preventDefault(); void invoke("quit", []);
    }
  });
  function notice(error) { window.mailArchiverNotice?.(error.message || String(error)); }
  const element = (tag, text) => { const node=document.createElement(tag); if(text) node.textContent=text; return node; };
  function modal(title) {
    const dialog=element("dialog"); dialog.className="rust-workflow no-print";
    const heading=element("h2",title), close=element("button","Close"); close.type="button";
    close.addEventListener("click",()=>dialog.close());
    dialog.append(heading,close); document.body.append(dialog);
    dialog.addEventListener("close",()=>dialog.remove(),{once:true}); dialog.showModal(); return dialog;
  }
  function panel(page, title, methods, query="") {
    const dialog=modal(title), frame=element("iframe"); frame.title=title;
    frame.src=page+query;
    frame.addEventListener("load",()=>{
      frame.contentWindow.pywebview={api:methods};
      frame.contentWindow.dispatchEvent(new frame.contentWindow.Event("pywebviewready"));
    },{once:true});
    dialog.append(frame); return true;
  }
  api.open_picker=async kind=>panel("identity.html",kind==="institution"?"Institutions":"Names and addresses",{
    query: filters=>api.identity_query(kind,filters), update: decision=>api.identity_update(decision)
  },"?kind="+(kind==="institution"?"institution":"name"));
  api.open_options=async()=>panel("options.html","Owner emails",{status:api.options_status,update:api.options_update});
  api.open_ingest_window=async()=>panel("ingests.html","Import history",{
    history:api.history,antivirus:api.antivirus,
    can_import_directory:async()=>!(await api.job_status()).active,
    import_directory:()=>api.import_directory(),
    install_antivirus:()=>api.open_link("https://www.clamav.net/downloads")
  });
  api.import_directory=async()=>{
    const defaults=await api.prepare_import(); if(!defaults) return false;
    const dialog=modal("Import mail"), form=element("form"), error=element("p"); error.setAttribute("role","alert");
    form.append(element("p",`Read from: ${defaults.source}`));
    const fields={};
    for(const [key,label] of [["include","Owner email rules (one per line)"],["exclude","Exclude owner rules"]]) {
      const wrapper=element("label",label), field=element("textarea"); field.name=key; field.rows=3;
      field.value=defaults[key].join("\n"); wrapper.append(field); form.append(wrapper); fields[key]=field;
    }
    const scanLabel=element("label"), scan=element("input"); scan.type="checkbox"; scan.checked=true;
    scanLabel.append(scan,document.createTextNode(" Scan with ClamAV"));
    const details=element("p",defaults.antivirus.detail);
    const unscanned=element("p","Without scanning, infected messages and attachments may be retained. Uncheck only if you accept this risk.");
    const attachmentLabel=element("label"), attachments=element("input"); attachments.type="checkbox"; attachments.checked=true;
    attachmentLabel.append(attachments,document.createTextNode(" Index attachment text"));
    const start=element("button","Start import"); start.type="submit";
    form.append(scanLabel,details,unscanned,attachmentLabel,error,start); dialog.append(form);
    form.addEventListener("submit",async event=>{
      event.preventDefault(); start.disabled=true; error.textContent="";
      try {
        await api.start_import({source:defaults.source,include:fields.include.value,exclude:fields.exclude.value,
          revision:defaults.revision,scan_policy:scan.checked?"clamav":"not-scanned",index_attachments:attachments.checked});
        dialog.close(); api.open_ingest_window();
      }catch(failure){error.textContent=failure.message;}finally{start.disabled=false;}
    });
    return true;
  };
  window.addEventListener("DOMContentLoaded", () => {
    const style=element("link"); style.rel="stylesheet"; style.href="rust-workflow.css"; document.head.append(style);
    const toolbar=document.querySelector(".archive-tools");
    for(const [method,label] of [["import_directory","Import…"],["open_options","Owner emails…"],["open_ingest_window","Import history"]]) {
      const button=element("button",label); button.type="button"; button.dataset.engineControl="true";
      button.addEventListener("click",()=>Promise.resolve(api[method]()).catch(notice)); toolbar.append(button);
    }
    const stop=element("button","Stop after current message"); stop.hidden=true; stop.type="button";
    stop.addEventListener("click",()=>api.stop_import().catch(notice)); toolbar.append(stop);
    const badge=element("span","Rust desktop"); badge.className="status"; toolbar.append(badge);
    const controls=[...toolbar.querySelectorAll("[data-engine-control],#name-picker,#institution-picker,#processing-open")];
    controls.forEach(control=>{control.disabled=true;});
    let generation=0, polling=false;
    api.engine_status().then(status=>{
      controls.forEach(control=>{control.disabled=!status.available;control.title=status.available?"":status.detail;});
      badge.textContent=status.available?"Rust preview":"Archive engine unavailable";
      if(!status.available) return;
      window.setInterval(async()=>{
        if(polling) return; polling=true;
        try {
          const job=await api.job_status(); stop.hidden=!job.active || job.kind!=="import"; stop.disabled=job.stopping;
          if(job.generation!==generation) {
            generation=job.generation;
            if(job.error) notice(new Error(job.error));
            // Explicit user search refreshes the catalog after a completed writer run.
            document.getElementById("search-form").requestSubmit();
          }
        }catch(failure){notice(failure);}finally{polling=false;}
      },1000);
    }).catch(notice);
    window.dispatchEvent(new Event("pywebviewready"));
  });
})();
