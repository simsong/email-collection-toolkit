/* Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. */
/* Add desktop dialogs to the shared reader without forking the search page.
 * Native menus call the same accessible About, Preferences and Updates dialogs.
 * Preferences are validated and saved by Rust outside the email archive.
 * Message text size applies immediately and is restored on the next launch.
 * Update availability and errors come from the actual native updater client.
 * All displayed metadata uses textContent; no mail or URLs become executable HTML.
 */
(() => {
  if (window !== window.top) return;
  window.addEventListener("DOMContentLoaded", () => {
    const dialog = document.createElement("dialog");
    dialog.id = "rust-shell-dialog";
    dialog.setAttribute("aria-labelledby", "rust-shell-heading");
    document.body.append(dialog);
    const style = document.createElement("link");
    style.rel = "stylesheet";
    style.href = "rust-shell.css";
    document.head.append(style);
    const apply = status => document.documentElement.style.setProperty("--rust-message-font-size", `${status.preferences.message_font_size}px`);
    const api = window.pywebview.api;
    const text = (tag, value) => {
      const node = document.createElement(tag);
      node.textContent = value;
      return node;
    };
    const show = async action => {
      if (dialog.open) dialog.close();
      dialog.replaceChildren();
      const heading = text("h2", action === "about" ? "About Email Collection Toolkit" : action === "preferences" ? "Preferences" : "Software Updates");
      heading.id = "rust-shell-heading";
      const body = document.createElement("section");
      const error = text("p", "");
      error.setAttribute("role", "alert");
      const footer = document.createElement("footer");
      const close = text("button", "Close");
      close.addEventListener("click", () => dialog.close());
      footer.append(close);
      dialog.append(heading, body, error, footer);
      dialog.showModal();
      try {
        const status = await api.shell_status();
        if (!dialog.open || !body.isConnected) return;
        if (action === "about") {
          body.append(text("p", `Version ${status.version}\n${status.platform} · ${status.architecture}\nRust / Wry desktop reader`), text("p", "Copyright © 2026 Simson L. Garfinkel.\nLicensed under GPL-2.0-only."), text("p", "Search and message viewing run in Rust. Import, recovery and identity services use the project Python engine during migration."));
          const capabilities=await api.engine_status();
          const antivirus=await api.antivirus();
          const health=text("p",antivirus.detail);
          health.id="rust-antivirus-status";
          body.append(health,text("p",antivirus.warning || ""));
          if (capabilities.available && capabilities.write_available) {
            const refresh=text("button","Update virus definitions");
            refresh.addEventListener("click",async()=>{
              refresh.disabled=true;
              try { await api.refresh_definitions(); body.append(text("p","Updating definitions in the background. Failures will appear in the main window.")); }
              catch(failure){error.textContent=failure.message; refresh.disabled=false;}
            });
            footer.append(refresh);
          }
        } else if (action === "preferences") {
          const sizeLabel = text("label", "Message text size ");
          const size = document.createElement("input");
          size.type = "number"; size.min = "10"; size.max = "28";
          size.value = String(status.preferences.message_font_size);
          sizeLabel.append(size);
          const updateLabel = document.createElement("label");
          const automatic = document.createElement("input");
          automatic.type = "checkbox";
          automatic.checked = status.preferences.automatic_updates;
          automatic.disabled = !status.updates_available;
          updateLabel.append(automatic, document.createTextNode(" Automatically check for updates"));
          body.append(sizeLabel, updateLabel, text("p", status.update_detail));
          const save = text("button", "Save");
          save.addEventListener("click", async () => {
            if (!size.reportValidity()) return;
            save.disabled = true;
            try {
              apply(await api.preferences_save({message_font_size:Number(size.value), automatic_updates:automatic.checked}));
              dialog.close();
            } catch (failure) { error.textContent = failure.message; }
            finally { save.disabled = false; }
          });
          footer.append(save);
        } else {
          body.append(text("p", status.update_detail));
          if (status.updates_available) {
            await api.check_updates();
            dialog.close();
          }
        }
      } catch (failure) { error.textContent = failure.message; }
    };
    window.__rustShellAction = action => { void show(action); };
    api.shell_status().then(apply).catch(error => console.error(error));
    window.addEventListener("keydown", event => {
      if ((event.ctrlKey || event.metaKey) && event.key === ",") {
        event.preventDefault(); void show("preferences");
      }
    });
  });
})();
