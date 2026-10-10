/* Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. */
/* Present native archive opening as a responsive foreground operation.
 * The Rust worker probes read-only and announces when SQLite recovery is needed.
 * A monotonic elapsed counter makes long recovery visible without a time limit.
 * Abort signals the native supervisor immediately, independently of the worker.
 * No successful reply can open the reader after this page has accepted Abort.
 * Errors leave this page visible with a diagnostic and only a Close action.
 */
document.addEventListener("DOMContentLoaded", () => {
  const heading=document.getElementById("heading"), status=document.getElementById("status");
  const elapsed=document.getElementById("elapsed"), detail=document.getElementById("detail");
  const abort=document.getElementById("abort"), close=document.getElementById("close");
  let aborted=false, started, timer;
  const send=method=>window.ipc.postMessage(JSON.stringify({id:1,method,args:[]}));
  const tick=()=>{
    const seconds=Math.floor((performance.now()-started)/1000);
    elapsed.textContent=`Elapsed: ${String(Math.floor(seconds/60)).padStart(2,"0")}:${String(seconds%60).padStart(2,"0")}`;
  };
  window.__rustOpening=()=>{
    if(aborted) return;
    heading.textContent="Recovery in progress…";
    status.textContent="The archive requires database recovery before it can be opened.";
    started=performance.now(); elapsed.hidden=false; tick(); timer=setInterval(tick,250);
  };
  window.__rustReply=reply=>{
    if(reply.id!==1) return;
    clearInterval(timer);
    if(!reply.error && !aborted) { window.location.replace("index.html"); return; }
    heading.textContent=aborted?"Recovery aborted":"Archive cannot be opened";
    status.textContent=aborted?"The archive has not been opened.":"Opening or recovery failed. The archive cannot be opened.";
    detail.textContent=aborted?"":reply.error;
    abort.hidden=true; close.hidden=false; close.focus();
  };
  abort.addEventListener("click",()=>{
    aborted=true; abort.disabled=true; status.textContent="Aborting… The archive will remain unopened.";
    send("opening_abort");
  });
  close.addEventListener("click",()=>send("quit"));
  send("opening_start");
});
