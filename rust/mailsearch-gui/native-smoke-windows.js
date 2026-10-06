/* Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. */
/* Drive the shared interface inside real Windows WebView2.
 * All results come from native IPC and the production archive reader.
 * Pause acknowledgements to prove selection and cancellation during searches.
 * Verify both preview windows, paging, six sort orders and native-shell dialogs.
 * Return assertions through the origin-checked smoke channel before capture.
 * This driver is absent from ordinary builds and uses only synthetic mail.
 */
window.addEventListener("DOMContentLoaded", async () => {
  const send = (method, args = []) => window.ipc.postMessage(JSON.stringify({id:0, method, args}));
  const el = id => document.getElementById(id);
  const wait = async (label, predicate) => {
    const deadline = Date.now() + 15000;
    while (!predicate()) {
      if (Date.now() > deadline) throw new Error(`${label}: ${el("result-status").textContent}; ${el("error").textContent}`);
      await new Promise(resolve => setTimeout(resolve, 20));
    }
  };
  const check = (value, label) => { if (!value) throw new Error(label); };
  const submit = value => { el("search").value = value; el("search-form").requestSubmit(); };
  try {
    await wait("startup", () => !el("search").disabled && document.querySelector(".tabulator"));
    const api = window.pywebview.api;
    const capabilities=await api.engine_status();
    check(capabilities.write_available===false,"Windows writer capability must be false");
    await wait("read-only controls",()=>[...document.querySelectorAll("[data-engine-write]")].every(control=>control.disabled));
    check(!el("processing-dialog").open,"unsupported processing prompt opened");
    try { await api.new_archive(); throw new Error("Windows creation was offered"); }
    catch(error) { check(String(error).includes("unavailable") || String(error).includes("not supported"),"unexpected creation rejection"); }
    const advance = api.search_advance;
    let hold = true, release = null;
    const stages = [];
    api.search_advance = async (...args) => {
      stages.push(args[1]);
      if (hold && args[1] === 2) {
        hold = false;
        await new Promise(resolve => { release = resolve; });
      }
      return advance(...args);
    };
    submit("observatory");
    await wait("both painted windows", () => release && state.results.length === 1024);
    check(stages.includes(1) && stages.includes(2), "missing preview stage");
    if (window.__ectCloseSmoke) {
      check((await api.shell_status()).preferences.message_font_size === 18, "preference not restored on restart");
      send("native_smoke_close_ready"); return;
    }
    document.querySelector("#result-list .result").click();
    await wait("selection during search", () => el("body-view").textContent.includes("Jupiter"));
    const selected = state.selected;
    release();
    await wait("completion", () => el("result-status").textContent.includes("1,600 messages"));
    check(state.selected === selected, "completion lost selection");
    for (const count of [1536, 1600]) {
      const scroller = document.querySelector("#result-list .tabulator-tableholder");
      scroller.scrollTop = scroller.scrollHeight;
      scroller.dispatchEvent(new Event("scroll"));
      await wait("scroll paging", () => state.results.length === count);
    }
    check(new Set(state.results.map(row => row.message_pk)).size === 1600, "duplicate/missing rows");
    for (const sort of ["date", "subject", "sender"]) {
      for (const direction of ["ascending", "descending"]) {
        const started = await api.search_start("observatory", sort, direction);
        let status;
        do {
          status = await api.search_status(started.generation);
          check(!status.error, `sort error ${status.error}`);
          if (status.window) await api.search_advance(started.generation, status.window);
          await new Promise(resolve => setTimeout(resolve, 5));
        } while (!status.complete);
        const ids = [];
        while (ids.length < 1600) {
          const page = await api.search_page(started.generation, ids.length, 512);
          check(page.results.length > 0, "incomplete sort page");
          ids.push(...page.results.map(row => row.message_pk));
        }
        const expected = [1, ...Array.from({length:1599}, (_,i) => i+4)];
        if (direction === "descending") expected.reverse();
        check(JSON.stringify(ids) === JSON.stringify(expected), `${sort} ${direction} order`);
      }
    }
    // Replace an unfinished real search, then release its delayed acknowledgement.
    hold = true; release = null; submit("observatory");
    await wait("held replacement", () => release !== null);
    submit("zzzznomatch");
    await wait("replacement", () => el("result-status").textContent === "0 messages");
    release();
    submit("");
    await new Promise(resolve => setTimeout(resolve, 100));
    check(state.results.length === 0, "stale results after clear");
    submit("observatory");
    await wait("final search", () => el("result-status").textContent.includes("1,600 messages"));
    document.querySelector("#result-list .result").click();
    await wait("final display", () => el("body-view").textContent.includes("Jupiter"));
    window.__rustShellAction("about");
    await wait("about", () => el("rust-shell-dialog").textContent.includes("Version"));
    el("rust-shell-dialog").close();
    window.__rustShellAction("preferences");
    await wait("preferences", () => el("rust-shell-dialog").querySelector('input[type="number"]'));
    const size = el("rust-shell-dialog").querySelector('input[type="number"]');
    size.value = "18";
    [...el("rust-shell-dialog").querySelectorAll("button")].find(button => button.textContent === "Save").click();
    await wait("save", () => !el("rust-shell-dialog").open);
    check((await api.shell_status()).preferences.message_font_size === 18, "preference not saved");
    check(getComputedStyle(el("body-view").querySelector(".plain") || el("body-view")).fontSize === "18px", "preference did not change rendered font");
    const previousWidth = window.innerWidth;
    send("native_smoke_resize");
    await wait("native resize", () => window.innerWidth !== previousWidth);
    el("body-view").dispatchEvent(new KeyboardEvent("keydown", {key:"f", ctrlKey:true, bubbles:true}));
    await wait("Ctrl-F handler", () => !el("message-find").hidden);
    el("message-find-query").value = "Jupiter";
    el("message-find-query").dispatchEvent(new Event("input", {bubbles:true}));
    await wait("find match", () => el("message-find-status").textContent.includes("1"));
    check(el("error").hidden, el("error").textContent);
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    send("native_smoke_ready");
  } catch (error) { send("native_smoke_failed", [String(error)]); }
});
