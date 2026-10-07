/* Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. */
/* Connect the shared ECT frontend to Rust readers and supervised archive services.
 * Each promise has a request ID, so concurrent frontend operations stay distinct.
 * Native IPC and the headless test transport both use this exact adapter.
 * The original frontend retains its stale-response and message-selection guards.
 * Unsupported controls are disabled and the experiment's limits stay visible.
 * No archive data is stored in JavaScript persistence or sent to remote services.
 */
(() => {
  if (window !== window.top || location.pathname === "/opening.html") return;
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
  // Startup has no archive toolbar or reader API; expose only its native actions.
  if (location.pathname === "/welcome.html") {
    window.pywebview = {api: Object.fromEntries(
      ["welcome_open", "welcome_new", "welcome_status", "quit"].map(name => [name, (...args) => invoke(name, args)])
    )};
    return;
  }
  const names = ["status", "activate", "search", "search_start", "search_status", "search_advance", "search_page", "search_cancel", "message", "part", "request_previews", "take_previews",
    "suggestions", "ingest_overview", "saved_filter_sets", "save_filter_set", "rename_filter_set", "delete_filter_set", "mailbox_tree", "attachment", "open_link", "copy_source_path", "save_message", "save_attachment", "prepare_drag", "open_attachment", "open_message_window", "new_search_window", "open_archive", "open_recent",
    "shell_status", "preferences_save", "check_updates", "engine_status", "processing_work", "resume_processing", "history", "antivirus", "options_status", "options_update", "identity_query", "identity_update", "job_status", "stop_import", "prepare_import", "start_import", "new_archive", "refresh_definitions"];
  const api = Object.fromEntries(names.map(name => [name, (...args) => invoke(name, args)]));
  const engineStatus=api.engine_status;
  let capabilities;
  api.engine_status=()=>capabilities ||= engineStatus();
  const processingWork=api.processing_work, ingestOverview=api.ingest_overview;
  api.processing_work=async()=>{
    const status=await api.engine_status();
    return status.available && status.write_available ? processingWork() :
      {available:false,active:false,ingest:0,content:0,failed:0,source_roots:[]};
  };
  api.ingest_overview=async()=>(await api.engine_status()).available ? ingestOverview() : {status:null};
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
  async function requireWriting() {
    const status=await api.engine_status();
    if(!status.available || !status.write_available) throw new Error(status.detail || status.write_detail || "Archive writing is unavailable.");
  }
  const newArchive=api.new_archive;
  api.new_archive=async()=>{await requireWriting(); return newArchive();};
  const element = (tag, text) => { const node=document.createElement(tag); if(text) node.textContent=text; return node; };
  function modal(title) {
    const dialog=element("dialog"); dialog.className="rust-workflow no-print";
    dialog.setAttribute("aria-label",title);
    const heading=element("h2",title), close=element("button","Close"); close.type="button";
    close.addEventListener("click",()=>dialog.close());
    dialog.append(heading,close); document.body.append(dialog);
    dialog.addEventListener("close",()=>dialog.remove(),{once:true}); dialog.showModal(); return dialog;
  }
  const openAttachment=api.open_attachment;
  api.open_attachment=async(message, part, confirmed=false)=>{
    const result=await openAttachment(message,part,confirmed);
    if(!result?.requires_confirmation) return result;
    const accepted=await new Promise(resolve=>{
      const dialog=modal("Open attachment");
      dialog.querySelector("button").textContent="Cancel";
      dialog.append(element("p",`${result.filename} may contain active or unrecognized content. Open it anyway?`));
      const open=element("button","Open"); open.type="button";
      let accepted=false;
      open.addEventListener("click",()=>{accepted=true; dialog.close();});
      dialog.addEventListener("close",()=>resolve(accepted),{once:true});
      dialog.append(open);
      dialog.querySelector("button").focus();
    });
    return accepted ? openAttachment(message,part,true) : {requires_confirmation:false,cancelled:true};
  };
  function panel(page, title, methods, query="") {
    const dialog=modal(title), frame=element("iframe"); frame.title=title;
    frame.setAttribute("sandbox","allow-scripts allow-forms");
    frame.src=page+query;
    frame.addEventListener("load",()=>{
      const channel=new MessageChannel();
      channel.port1.onmessage=async event=>{
        const request=event.data;
        if(!request || !Number.isSafeInteger(request.id) || !Array.isArray(request.args)) return;
        const reply={id:request.id};
        try {
          if(typeof request.method!=="string" || !Object.hasOwn(methods,request.method)) throw new Error("Editor method is not allowed.");
          reply.result=await methods[request.method](...request.args);
        }catch(error){reply.error=error.message || String(error);}
        channel.port1.postMessage(reply);
      };
      dialog.addEventListener("close",()=>channel.port1.close(),{once:true});
      // Only this opaque-origin child receives the capability port.
      frame.contentWindow.postMessage({type:"ect-editor",methods:Object.keys(methods)},"*",[channel.port2]);
    },{once:true});
    dialog.append(frame); return true;
  }
  api.open_picker=async kind=>{await requireWriting(); return panel("identity.html",kind==="institution"?"Institutions":"Names and addresses",{
    query: filters=>api.identity_query(kind,filters), update: decision=>api.identity_update(decision)
  },"?kind="+(kind==="institution"?"institution":"name"));};
  api.open_options=async()=>{await requireWriting(); return panel("options.html","Owner emails",{status:api.options_status,update:api.options_update});};
  api.open_ingest_window=async()=>panel("ingests.html","Import history",{
    history:api.history,antivirus:api.antivirus,
    can_import_directory:async()=>(await api.engine_status()).write_available && !(await api.job_status()).active,
    import_directory:()=>api.import_directory(),
    install_antivirus:()=>api.open_link("https://www.clamav.net/downloads")
  });
  api.import_directory=async()=>{
    await requireWriting();
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
      if(method!=="open_ingest_window") button.dataset.engineWrite="true";
      button.addEventListener("click",()=>Promise.resolve(api[method]()).catch(notice)); toolbar.append(button);
    }
    const stop=element("button","Stop after current message"); stop.hidden=true; stop.type="button";
    stop.addEventListener("click",()=>api.stop_import().catch(notice)); toolbar.append(stop);
    const badge=element("span","Rust desktop"); badge.className="status"; toolbar.append(badge);
    const controls=[...toolbar.querySelectorAll("[data-engine-control],#name-picker,#institution-picker,#processing-open")];
    for(const control of toolbar.querySelectorAll("#name-picker,#institution-picker,#processing-open")) control.dataset.engineWrite="true";
    controls.forEach(control=>{control.disabled=true;});
    let generation=0, polling=false;
    api.engine_status().then(status=>{
      controls.forEach(control=>{
        control.disabled=!status.available || (control.dataset.engineWrite==="true" && !status.write_available);
        control.title=control.disabled?(status.detail || status.write_detail || ""):"";
      });
      badge.textContent=status.available?(status.write_available?"Rust preview":"Rust read-only"):"Archive engine unavailable";
      if(!status.available) return;
      window.setInterval(async()=>{
        if(polling) return; polling=true;
        try {
          const job=await api.job_status(); stop.hidden=!job.active || job.kind!=="import"; stop.disabled=job.stopping;
          if(job.generation!==generation) {
            generation=job.generation;
            if(job.error) notice(new Error(job.error));
            if(job.warning) notice(new Error(job.warning));
            // Explicit user search refreshes the catalog after a completed writer run.
            document.getElementById("search-form").requestSubmit();
          }
        }catch(failure){notice(failure);}finally{polling=false;}
      },1000);
    }).catch(notice);
    window.dispatchEvent(new Event("pywebviewready"));
  });
})();
