/* Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. */
"use strict";

// These view models have no archive, name-matching, or organization policy.
class MatcherMessage {
  constructor(id, date) { Object.assign(this, {id, date}); Object.freeze(this); }
}

class MatcherAddress {
  constructor(id, email, observations) {
    const ordered = [...observations].sort((a, b) => a.date.localeCompare(b.date));
    Object.assign(this, {id, email, observations: Object.freeze(ordered),
      firstUse: ordered[0]?.date || "", lastUse: ordered.at(-1)?.date || "",
      messages: new Set(ordered.map(message => message.id)).size});
    Object.freeze(this);
  }
  inPeriod(start, end) {
    return new MatcherAddress(this.id, this.email, this.observations.filter(message =>
      (!start || message.date >= start) && (!end || message.date <= end)));
  }
  matches(mailbox, domain) {
    const address = this.email.toLowerCase();
    const at = address.lastIndexOf("@");
    return address.slice(0, at).includes(mailbox)
      && address.slice(at + 1).includes(domain);
  }
}

class MatcherGroup {
  constructor(id, label, addresses) { Object.assign(this, {id, label, addresses}); }
  copy() { return new MatcherGroup(this.id, this.label, [...this.addresses]); }
  get firstUse() { return this.addresses.map(address => address.firstUse).sort()[0] || ""; }
  get lastUse() { return this.addresses.map(address => address.lastUse).sort().at(-1) || ""; }
  get messages() { return new Set(this.addresses.flatMap(address => address.observations.map(message => message.id))).size; }
}

class MatcherSelection {
  constructor(groupId, addressId = null) { Object.assign(this, {groupId, addressId}); }
}

class MatcherMove {
  constructor(source, targetId) { Object.assign(this, {source, targetId}); }
}

class MatcherModel {
  constructor(groups) { this.groups = groups.map(group => group.copy()); }
  find(id) { return this.groups.find(group => group.id === id); }
  move(selection, targetId) {
    const source = this.find(selection.groupId);
    const target = this.find(targetId);
    if (!source || !target || source === target) return false;
    const addresses = selection.addressId === null ? [...source.addresses]
      : source.addresses.filter(address => address.id === selection.addressId);
    if (!addresses.length) return false;
    target.addresses.push(...addresses);
    source.addresses = source.addresses.filter(address => !addresses.includes(address));
    this.groups = this.groups.filter(group => group.addresses.length);
    return true;
  }
  separate(selection) {
    const source = this.find(selection.groupId);
    const address = source?.addresses.find(item => item.id === selection.addressId);
    if (!address || source.addresses.length < 2) return false;
    let suffix = 1;
    while (this.find(`separate-${address.id}-${suffix}`)) suffix += 1;
    const group = new MatcherGroup(`separate-${address.id}-${suffix}`, address.email, []);
    this.groups.push(group);
    return this.move(selection, group.id);
  }
}

// Subclasses can supply labels, records, and an async matcher returning MatcherMove[].
// The base window owns filtering, disclosure, selection, moving, undo, and rendering.
class MatcherWindow {
  constructor(root, groups, {title = "Matcher", identityHeading = "Canonical entry / email address",
    entryLabel = "canonical entry", intro = "Group email addresses under a canonical entry.",
    actionLabel = "Run authoritative matcher", matcher = null} = {}) {
    this.root = root;
    this.initial = groups.map(group => group.copy());
    this.model = new MatcherModel(groups);
    this.matcher = matcher;
    this.entryLabel = entryLabel;
    this.sortKey = null;
    this.sortDirection = 1;
    this.history = [];
    this.selection = null;
    this.dragging = null;
    this.busy = false;
    this.expanded = new Set(groups.slice(0, 1).map(group => group.id));
    this.filters = ["mailbox-filter", "domain-filter", "start-date", "end-date"].map(id => this.get(id));
    root.querySelector("h1").textContent = title;
    document.title = `${title} · Prototype`;
    root.querySelector(".intro").textContent = intro;
    this.get("run-matcher").textContent = actionLabel;
    this.get("identity-heading").textContent = identityHeading;
    root.querySelectorAll("[data-sort]").forEach(button => button.addEventListener("click", () => {
      const key = button.dataset.sort;
      this.sortDirection = this.sortKey === key ? -this.sortDirection : (key === "messages" ? -1 : 1);
      this.sortKey = key;
      this.render();
    }));
    this.filters.forEach(input => input.addEventListener("input", () => this.filterChanged()));
    this.get("clear-filters").addEventListener("click", () => {
      this.filters.forEach(input => { input.value = ""; });
      this.filterChanged();
      this.filters[0].focus();
    });
    this.get("undo").addEventListener("click", () => this.undo());
    this.get("reset").addEventListener("click", () => this.reset());
    this.get("run-matcher").addEventListener("click", () => this.runMatcher());
    this.get("destination").addEventListener("change", () => {
      this.get("move").disabled = this.busy || !this.get("destination").value;
    });
    this.get("move").addEventListener("click", () => this.move(this.selection, this.get("destination").value));
    this.get("separate").addEventListener("click", () => this.change(
      () => this.model.separate(this.selection), "Address separated. Undo restores its previous canonical name."));
    this.render();
  }
  get(id) { return this.root.querySelector(`#${id}`); }
  queries() { return this.filters.map(input => input.value.trim().toLowerCase()); }
  visible() {
    const [mailbox, domain, start, end] = this.queries();
    this.get("date-error").hidden = !(start && end && start > end);
    if (start && end && start > end) return [];
    const groups = this.model.groups.map(group => new MatcherGroup(group.id, group.label,
      group.addresses.filter(address => address.matches(mailbox, domain))
        .map(address => address.inPeriod(start, end)).filter(address => address.messages)))
      .filter(group => group.addresses.length);
    if (this.sortKey) {
      const compare = (a, b) => {
        const left = this.sortKey === "identity" ? (a.label || a.email) : a[this.sortKey];
        const right = this.sortKey === "identity" ? (b.label || b.email) : b[this.sortKey];
        return this.sortDirection * (typeof left === "number" ? left - right : left.localeCompare(right))
          || a.id.localeCompare(b.id);
      };
      groups.sort(compare);
      groups.forEach(group => group.addresses.sort(compare));
    }
    return groups;
  }
  filterChanged() {
    this.expanded = new Set(this.visible().map(group => group.id));
    this.selection = null;
    this.render();
  }
  announce(message) { this.get("status").textContent = message; }
  checkpoint() { return this.model.groups.map(group => group.copy()); }
  change(operation, message) {
    if (this.busy) return;
    const before = this.checkpoint();
    if (!operation()) return;
    this.history.push(before);
    this.selection = null;
    this.render();
    this.announce(message);
  }
  move(selection, targetId) {
    if (!selection) return;
    const source = this.model.find(selection.groupId);
    const target = this.model.find(targetId);
    if (!source || !target) return;
    const count = selection.addressId === null ? source.addresses.length : 1;
    this.change(() => {
      const changed = this.model.move(selection, targetId);
      if (changed) this.expanded.add(targetId);
      return changed;
    }, `Moved ${count} address${count === 1 ? "" : "es"} to ${target.label}. Undo is available.`);
  }
  undo() {
    if (this.busy || !this.history.length) return;
    const previousIds = new Set(this.model.groups.map(group => group.id));
    this.model = new MatcherModel(this.history.pop());
    // Reveal restored source entries so the reversal is visible after a merge.
    for (const group of this.model.groups) if (!previousIds.has(group.id)) this.expanded.add(group.id);
    this.selection = null;
    this.render();
    this.announce("Last change undone.");
  }
  reset() {
    this.change(() => {
      this.model = new MatcherModel(this.initial);
      this.filters.forEach(input => { input.value = ""; });
      this.expanded = new Set(this.initial.slice(0, 1).map(group => group.id));
      return true;
    }, "Synthetic data restored. You can undo this reset.");
  }
  async runMatcher() {
    if (this.busy || !this.matcher) return;
    const before = this.checkpoint();
    this.busy = true;
    this.render();
    this.announce("Running demo matcher…");
    try {
      const moves = await this.matcher(new MatcherModel(before));
      // Apply to a separate model: errors cannot leave a partially applied batch.
      const next = new MatcherModel(before);
      let count = 0;
      for (const move of moves) {
        if (next.move(move.source, move.targetId)) {
          this.expanded.add(move.targetId);
          count += 1;
        }
      }
      if (count) { this.history.push(before); this.model = next; this.selection = null; }
      this.announce(count ? `Applied ${count} predefined demo matches. Undo reverses the whole batch. The authoritative algorithm is not connected.`
        : "No remaining demo matches. The authoritative algorithm is not connected.");
    } catch (error) {
      this.announce(`Matcher failed; no changes applied. ${error.message}`);
    } finally { this.busy = false; this.render(); }
  }
  select(selection) {
    this.selection = selection;
    this.root.querySelectorAll("tbody tr").forEach(row => {
      const selected = row.dataset.groupId === selection.groupId
        && (row.dataset.addressId || null) === selection.addressId;
      row.classList.toggle("selected", selected);
      row.querySelector(".row-label").setAttribute("aria-pressed", String(selected));
    });
    this.renderSelection();
  }
  renderSelection() {
    const source = this.selection && this.model.find(this.selection.groupId);
    const address = source?.addresses.find(item => item.id === this.selection.addressId);
    this.get("selection-label").textContent = address?.email || (source ? `${source.label} · all ${source.addresses.length} addresses` : "Select a row to move it");
    const destination = this.get("destination");
    destination.replaceChildren(new Option(`Choose ${this.entryLabel}…`, ""));
    for (const group of this.model.groups) {
      if (group !== source) destination.add(new Option(group.label, group.id));
    }
    destination.disabled = this.busy || !source;
    this.get("move").disabled = true;
    this.get("separate").disabled = this.busy || !address || source.addresses.length < 2;
  }
  cell(row, text, className = "") {
    const cell = row.insertCell();
    cell.textContent = text;
    cell.className = className;
    return cell;
  }
  row(group, address = null) {
    const row = document.createElement("tr");
    row.className = address ? "address" : "group";
    row.dataset.groupId = group.id;
    if (address) row.dataset.addressId = address.id;
    const selection = new MatcherSelection(group.id, address?.id || null);
    const identity = document.createElement("div");
    identity.className = "identity";
    this.cell(row, "").append(identity);
    if (!address) {
      const disclosure = document.createElement("button");
      const opened = this.expanded.has(group.id);
      disclosure.className = "disclosure";
      disclosure.textContent = opened ? "▾" : "▸";
      disclosure.setAttribute("aria-label", `${opened ? "Collapse" : "Expand"} ${group.label}`);
      disclosure.setAttribute("aria-expanded", String(opened));
      disclosure.addEventListener("click", event => {
        event.stopPropagation();
        if (opened) this.expanded.delete(group.id); else this.expanded.add(group.id);
        this.render();
        this.get("matcher-rows").querySelectorAll("tr.group").forEach(item => {
          if (item.dataset.groupId === group.id) item.querySelector(".disclosure").focus();
        });
      });
      identity.append(disclosure);
    }
    const label = document.createElement("button");
    label.className = "row-label";
    label.textContent = address ? address.email : group.label;
    label.title = label.textContent;
    label.setAttribute("aria-pressed", "false");
    identity.append(label);
    if (!address) {
      const count = document.createElement("span");
      count.className = "member-count";
      const total = this.model.find(group.id).addresses.length;
      count.textContent = group.addresses.length === total ? String(total) : `${group.addresses.length}/${total}`;
      count.title = `${group.addresses.length} visible of ${total} addresses`;
      identity.append(count);
    }
    const stats = address || group;
    this.cell(row, stats.firstUse);
    this.cell(row, stats.lastUse);
    this.cell(row, stats.messages.toLocaleString("en-US"), "number");
    row.addEventListener("click", () => this.select(selection));
    row.draggable = !this.busy;
    row.addEventListener("dragstart", event => {
      if (this.busy) { event.preventDefault(); return; }
      this.dragging = selection;
      this.select(selection);
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", label.textContent);
    });
    row.addEventListener("dragend", () => this.endDrag());
    row.addEventListener("dragover", event => {
      if (!this.busy && this.dragging && this.dragging.groupId !== group.id) {
        event.preventDefault();
        event.dataTransfer.dropEffect = "move";
        row.classList.add("drop-target");
      }
    });
    row.addEventListener("dragleave", event => {
      if (!row.contains(event.relatedTarget)) row.classList.remove("drop-target");
    });
    row.addEventListener("drop", event => {
      event.preventDefault();
      if (this.dragging) this.move(this.dragging, group.id);
      this.endDrag();
    });
    return row;
  }
  endDrag() {
    this.dragging = null;
    this.root.querySelectorAll(".drop-target").forEach(row => row.classList.remove("drop-target"));
  }
  render() {
    for (const id of ["start-date", "end-date"]) {
      this.get(id).classList.toggle("has-value", Boolean(this.get(id).value));
    }
    const visible = this.visible();
    this.root.querySelectorAll("[data-sort]").forEach(button => {
      button.setAttribute("aria-label", button.textContent);
      const active = button.dataset.sort === this.sortKey;
      button.closest("th").setAttribute("aria-sort", active ? (this.sortDirection === 1 ? "ascending" : "descending") : "none");
    });
    const rows = this.get("matcher-rows");
    rows.replaceChildren();
    for (const group of visible) {
      rows.append(this.row(group));
      if (this.expanded.has(group.id)) for (const address of group.addresses) rows.append(this.row(group, address));
    }
    this.get("empty").hidden = visible.length > 0;
    const count = visible.reduce((sum, group) => sum + group.addresses.length, 0);
    const total = this.model.groups.reduce((sum, group) => sum + group.addresses.length, 0);
    this.get("summary").textContent = `${visible.length} canonical entries · ${count} of ${total} addresses`;
    this.get("undo").disabled = this.busy || !this.history.length;
    this.get("reset").disabled = this.busy;
    this.get("run-matcher").disabled = this.busy || !this.matcher;
    if (this.selection) this.select(this.selection); else this.renderSelection();
  }
}
