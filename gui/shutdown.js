/* Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. */
// Display the desktop application's confirmed shutdown state over any GUI page.
// Python injects this script into loaded webviews after arming the Quit deadline.
// A full-window modal dims existing content and prevents further interaction.
// Existing and late-arriving dialogs close so they cannot cover this notice.
// Repeated delivery is harmless, and Escape cannot dismiss the shutdown notice.
// Inline style properties allow the same overlay under each page's strict CSP.
// A shared stylesheet clears inherited modal backdrops before showing the tint.
// This display never calls the bridge or participates in shutdown completion.
(() => {
  if (!document.body || document.getElementById("shutdown-overlay")) return;
  const overlay = document.createElement("dialog");
  overlay.id = "shutdown-overlay";
  const closeOtherDialogs = () => document.querySelectorAll("dialog[open]").forEach(dialog => {
    if (dialog !== overlay) dialog.close();
  });
  closeOtherDialogs();
  new MutationObserver(closeOtherDialogs).observe(document.body, {
    subtree: true, childList: true, attributes: true, attributeFilter: ["open"],
  });
  overlay.setAttribute("aria-label", "Shutting down");
  overlay.style.cssText = "position:fixed;inset:0;margin:0;width:100vw;height:100vh;max-width:none;max-height:none;box-sizing:border-box;border:0;padding:0;background:rgba(128,128,128,0.2);color:rgba(0,0,0,0.5);";
  const message = document.createElement("div");
  message.textContent = "Shutting down…";
  message.setAttribute("role", "status");
  message.setAttribute("aria-live", "assertive");
  message.style.cssText = "height:100%;display:flex;align-items:center;justify-content:center;text-align:center;font:600 clamp(24px,5vw,64px) system-ui,sans-serif;";
  overlay.append(message);
  overlay.addEventListener("cancel", event => event.preventDefault());
  for (const name of ["keydown", "keypress", "keyup"]) {
    overlay.addEventListener(name, event => { event.preventDefault(); event.stopPropagation(); }, true);
  }
  document.body.append(overlay);
  const stylesheet = document.createElement("link");
  stylesheet.rel = "stylesheet";
  stylesheet.href = window.mailArchiverShutdownStylesheet || new URL("shutdown.css", document.baseURI).href;
  stylesheet.addEventListener("load", () => overlay.showModal(), {once: true});
  stylesheet.addEventListener("error", () => overlay.showModal(), {once: true});
  document.head.append(stylesheet);
})();
