/* Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. */
/* Connect a sandboxed workflow page to its Rust parent through a private port.
 * The parent transfers the port only to its selected opaque-origin iframe.
 * Editor scripts receive promise methods without access to the parent document.
 * Request IDs pair concurrent calls with results or visible service errors.
 * The parent checks every call against the specific editor's method allowlist.
 * Standalone Python editor windows keep their existing native bridge.
 */
(() => {
  if(window === window.parent) return;
  const pending=new Map();
  let initialized=false, nextId=0;
  window.addEventListener("message",event=>{
    if(initialized || event.source!==window.parent || event.data?.type!=="ect-editor" ||
       !Array.isArray(event.data.methods) || !event.data.methods.every(name=>typeof name==="string") || event.ports.length!==1) return;
    initialized=true;
    const port=event.ports[0];
    port.onmessage=event=>{
      const reply=event.data, item=pending.get(reply?.id);
      if(!item) return;
      pending.delete(reply.id);
      if(reply.error) item.reject(new Error(reply.error));
      else item.resolve(reply.result);
    };
    window.pywebview={api:Object.fromEntries(event.data.methods.map(method=>[method,(...args)=>new Promise((resolve,reject)=>{
      if(pending.size>=64){reject(new Error("Editor is busy.")); return;}
      const id=++nextId; pending.set(id,{resolve,reject}); port.postMessage({id,method,args});
    })]))};
    window.dispatchEvent(new Event("pywebviewready"));
  });
})();
