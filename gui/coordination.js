/* Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. */
// Coordinate asynchronous status reads without relying on timer spacing.
// Concurrent callers join the same operation and await its actual completion.
// Post-command reads wait for an older operation before requesting fresh state.
// Promise settlement releases ownership on both success and failure.
// This helper owns no timer and never treats elapsed time as successful work.
// About, Ingests and options retain their own rendering and error policies.
"use strict";

class RefreshTask {
  constructor(operation) { this.operation = operation; this.pending = null; this.dirty = false; }

  run() {
    if (!this.pending) {
      this.pending = Promise.resolve().then(async () => {
        try {
          let result;
          do { this.dirty = false; result = await this.operation(); } while (this.dirty);
          return result;
        } finally { this.pending = null; }
      });
    }
    return this.pending;
  }

  changed() { this.dirty = true; return this.run(); }

  async fresh() {
    if (this.pending) await this.pending.catch(() => undefined);
    return this.run();
  }
}

function waitForDomState(condition, label = "DOM readiness", timeout = 10000) {
  return new Promise((resolve, reject) => {
    let timer;
    const observer = new MutationObserver(check);
    function finish(callback, value) {
      observer.disconnect();
      clearTimeout(timer);
      window.removeEventListener("mailarchiver-state-changed", check);
      callback(value);
    }
    function check() {
      try { if (condition()) finish(resolve, true); }
      catch (error) { finish(reject, error); }
    }
    observer.observe(document, {subtree:true, childList:true, attributes:true, characterData:true});
    window.addEventListener("mailarchiver-state-changed", check);
    timer = setTimeout(() => finish(reject, new Error(`Timed out: ${label}`)), timeout);
    check();
  });
}
