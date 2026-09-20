/* Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. */
"use strict";

class ArchiveGroup extends MatcherGroup {
  constructor(row) {
    super(String(row.id), row.label, row.addresses.map(address => ({id: String(address.address_id), email: address.address,
      firstUse: address.first_use?.slice(0, 10) || "", lastUse: address.last_use?.slice(0, 10) || "", messages: address.messages, signatureMessages: address.signature_messages || 0})));
    this.rowData = row;
  }
  copy() { return new ArchiveGroup(this.rowData); }
  get firstUse() { return this.rowData.first_use?.slice(0, 10) || ""; }
  get lastUse() { return this.rowData.last_use?.slice(0, 10) || ""; }
  get messages() { return this.rowData.messages; }
  get signatureMessages() { return this.rowData.signature_messages || 0; }
}

// Both real pickers inherit the prototype's disclosure, sorting, selection and drag controls.
function archivePicker(Base) {
  return class extends Base {
    constructor(root, kind) {
      super(root, []);
      this.kind = kind;
      this.request = 0;
      this.filters.push(this.get("name-filter"));
      this.get("name-filter").addEventListener("input", () => this.filterChanged());
      this.get("refresh").addEventListener("click", () => this.reload());
      this.get("rename").addEventListener("click", () => this.persist({
        operation: this.kind === "name" ? "rename-person" : "rename-organization",
        subject: Number(this.selection?.groupId), name: this.get("canonical-name").value,
      }));
      document.title = kind === "name" ? "Name matcher" : "Institution matcher";
      if (kind === "institution") {
        root.querySelector(".intro").textContent = "Institutions are grouped by parent domain. Subdomains belong to their parent institution.";
        root.querySelector(".demo-note").textContent = "Domain grouping is automatic during ingest. Authoritative matching is not yet connected.";
      }
      void this.reload();
    }
    visible() {
      const groups = [...this.model.groups];
      const key = this.sortKey;
      if (key) {
        const compare = (a, b) => {
          const left = key === "identity" ? (a.label || a.email) : a[key];
          const right = key === "identity" ? (b.label || b.email) : b[key];
          return this.sortDirection * (typeof left === "number" ? left - right : left.localeCompare(right));
        };
        groups.sort(compare);
        groups.forEach(group => group.addresses.sort(compare));
      }
      return groups;
    }
    filterChanged() { void this.reload(); }
    async reload() {
      const request = ++this.request;
      const [mailbox, domain, start, end, name] = this.queries();
      this.get("date-error").hidden = !(start && end && start > end);
      if (start && end && start > end) { this.model = new MatcherModel([]); this.render(); return; }
      try {
        const page = await window.pywebview.api.query({mailbox, domain, name, start: start || null, end: end || null});
        if (request !== this.request) return;
        this.model = new MatcherModel(page.groups.map(row => new ArchiveGroup(row)));
        this.expanded = new Set(this.model.groups.map(group => group.id));
        this.selection = null;
        this.render();
        const visible = page.groups.reduce((sum, group) => sum + group.addresses.length, 0);
        this.get("summary").textContent = `${page.groups.length} canonical entries · ${visible} of ${page.total_addresses} addresses`;
        this.announce("Loaded archive identities. Select a canonical entry to rename it; edits save immediately.");
      } catch (error) { this.announce(`Could not load identities: ${error.message || error}`); }
    }
    async persist(decision) {
      if (this.busy) return;
      this.busy = true;
      this.render();
      try {
        await window.pywebview.api.update(decision);
        await this.reload();
        this.announce("Saved to the archive. Manual edits survive processor reruns.");
      } catch (error) { this.announce(`Not saved: ${error.message || error}`); }
      finally { this.busy = false; this.render(); }
    }
    move(selection, targetId) {
      if (this.kind !== "name" || !selection || selection.groupId === targetId) return;
      void this.persist({operation: selection.addressId ? "move-address" : "merge-person",
        subject: Number(selection.addressId || selection.groupId), target: Number(targetId)});
    }
    change() {
      if (this.kind === "name" && this.selection?.addressId) void this.persist({
        operation: "separate-address", subject: Number(this.selection.addressId),
      });
    }
    row(group, address = null) {
      const row = super.row(group, address);
      this.cell(row, (address || group).signatureMessages.toLocaleString("en-US"), "number");
      if (this.kind === "institution") row.draggable = false;
      return row;
    }
    renderSelection() {
      super.renderSelection();
      const name = this.selection && this.model.find(this.selection.groupId)?.label;
      this.get("canonical-name").value = name || "";
      this.get("rename").disabled = this.busy || !name;
      if (this.kind === "institution") {
        this.get("destination").disabled = true;
        this.get("move").disabled = true;
        this.get("separate").disabled = true;
      }
    }
  };
}

const ArchiveNameMatcherWindow = archivePicker(NameMatcherWindow);
const ArchiveInstitutionMatcherWindow = archivePicker(InstitutionMatcherWindow);
let archivePickerStarted = false;
function startArchivePicker() {
  if (archivePickerStarted || !window.pywebview?.api?.query) return;
  archivePickerStarted = true;
  const kind = new URLSearchParams(location.search).get("kind") === "institution" ? "institution" : "name";
  const Type = kind === "name" ? ArchiveNameMatcherWindow : ArchiveInstitutionMatcherWindow;
  window.archivePicker = new Type(document.getElementById("matcher"), kind);
}
window.addEventListener("pywebviewready", startArchivePicker);
startArchivePicker();
