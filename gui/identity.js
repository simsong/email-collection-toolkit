/* Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. */
"use strict";

class ArchiveGroup extends MatcherGroup {
  constructor(row) {
    super(String(row.id), row.label, row.addresses.map(address => ({id: String(address.address_id), email: address.address,
      firstUse: address.first_use?.slice(0, 10) || "", lastUse: address.last_use?.slice(0, 10) || "", messages: address.messages, signatureMessages: address.signature_messages || 0, phoneNumbers: address.phone_numbers || []})));
    this.rowData = row;
  }
  copy() { return new ArchiveGroup(this.rowData); }
  get firstUse() { return this.rowData.first_use?.slice(0, 10) || ""; }
  get lastUse() { return this.rowData.last_use?.slice(0, 10) || ""; }
  get messages() { return this.rowData.messages; }
  get signatureMessages() { return this.rowData.signature_messages || 0; }
  get phoneNumbers() { return this.rowData.phone_numbers || []; }
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
      this.get("show-non-people").addEventListener("change", () => this.reload());
      this.get("rename").addEventListener("click", () => this.persist({
        operation: this.kind === "name" ? "rename-person" : "rename-organization",
        subject: Number(this.selection?.groupId), name: this.get("canonical-name").value,
      }));
      document.title = kind === "name" ? "Name matcher" : "Institution matcher";
      if (kind === "institution") {
        root.classList.add("institution-picker");
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
      if (start && end && start > end) {
        this.loading = false; this.loadError = ""; this.loaded = true;
        this.model = new MatcherModel([]); this.render(); return;
      }
      this.loading = true;
      this.loadError = "";
      this.render();
      this.announce("Loading archive identities… Please wait.");
      try {
        const page = await window.pywebview.api.query({mailbox, domain, name, start: start || null, end: end || null,
          include_non_people: this.get("show-non-people").checked});
        if (request !== this.request) return;
        this.loaded = true;
        this.model = new MatcherModel(page.groups.map(row => new ArchiveGroup(row)));
        this.expanded = new Set(this.model.groups.map(group => group.id));
        this.selection = null;
        this.render();
        const visible = page.groups.reduce((sum, group) => sum + group.addresses.length, 0);
        this.get("summary").textContent = `${page.groups.length} canonical entries · ${visible} of ${page.total_addresses} addresses`;
        if (page.excluded_addresses) this.get("summary").textContent += ` · ${page.excluded_addresses} non-person addresses hidden`;
        this.announce("Loaded archive identities. Select a canonical entry to rename it; edits save immediately.");
      } catch (error) {
        if (request !== this.request) return;
        this.loadError = `Could not load identities: ${error.message || error}. Use Refresh to retry.`;
        this.announce(this.loadError);
      } finally {
        if (request === this.request) { this.loading = false; this.renderLoading(); }
      }
    }
    renderLoading() {
      const message = this.openingSearch || this.searchError || (this.loading ? "Loading archive identities… Please wait." : this.loadError || "");
      this.get("identity-loading").textContent = message;
      this.get("identity-loading").hidden = !message;
      this.root.querySelector(".matrix").setAttribute("aria-busy", String(Boolean(this.loading || this.openingSearch)));
      this.get("refresh").disabled = Boolean(this.loading);
      this.get("empty").hidden = Boolean(this.loading || this.loadError) || !this.loaded || this.model.groups.length > 0;
      if (!this.loaded) this.get("summary").textContent = this.loading ? "Loading identities…" : "Identities unavailable";
      this.renderSelection();
    }
    render() { super.render(); this.renderLoading(); }
    async persist(decision) {
      if (this.busy || this.loading) return;
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
    async search(group, address, action) {
      if (this.openingSearch) return;
      const [mailbox, domain, start, end, name] = this.queries();
      const target = address || group;
      const label = address ? address.email : group.label;
      const count = target[action === "header" ? "messages" : "signatureMessages"];
      const description = action === "name" ? `search for ${label}` :
        `${count} ${action === "header" ? "header" : "signature"} message${count === 1 ? "" : "s"} for ${label}`;
      this.openingSearch = `Opening ${description}… Please wait.`;
      this.searchError = "";
      this.announce(this.openingSearch);
      this.renderLoading();
      try {
        await window.pywebview.api.search({group_id: Number(group.id), address_id: address ? Number(address.id) : null,
          action, filters: {mailbox, domain, name, start: start || null, end: end || null,
            include_non_people: this.get("show-non-people").checked}});
        this.announce("Opened search in a new window.");
      } catch (error) {
        this.searchError = `Could not open search: ${error.message || error}. Click the name or count to retry.`;
        this.announce(this.searchError);
      } finally { this.openingSearch = ""; this.renderLoading(); }
    }
    row(group, address = null) {
      const row = super.row(group, address);
      this.cell(row, (address || group).signatureMessages.toLocaleString("en-US"), "number");
      const label = row.querySelector(".row-label");
      label.title = `Double-click to search ${label.textContent}`;
      label.addEventListener("dblclick", () => void this.search(group, address, "name"));
      label.addEventListener("keydown", event => {
        if (event.key === "Enter") { event.preventDefault(); void this.search(group, address, "name"); }
      });
      row.querySelectorAll("td.number").forEach((cell, index) => {
        const count = (address || group)[index ? "signatureMessages" : "messages"];
        const button = document.createElement("button");
        button.type = "button"; button.className = "message-count"; button.textContent = cell.textContent;
        button.disabled = count === 0;
        button.setAttribute("aria-label", `Show ${count} ${index ? "signature" : "header"} messages for ${label.textContent}`);
        button.addEventListener("click", event => {
          event.stopPropagation(); void this.search(group, address, index ? "signature" : "header");
        });
        cell.replaceChildren(button);
      });
      const phones = (address || group).phoneNumbers;
      const phoneCell = this.cell(row, phones.length === 1 ? phones[0].display : "", "phone-column");
      phoneCell.title = "Phone candidates observed in sender signatures; ownership is not verified.";
      if (phones.length > 1) {
        const details = document.createElement("details"); details.className = "phone-numbers";
        const summary = document.createElement("summary"); summary.textContent = `${phones.length} phone numbers`;
        summary.setAttribute("aria-label", `${phones.length} phone numbers for ${label.textContent}`);
        const list = document.createElement("ul");
        for (const phone of phones) {
          const item = document.createElement("li"); item.textContent = phone.display; list.append(item);
        }
        details.append(summary, list);
        details.addEventListener("click", event => event.stopPropagation());
        phoneCell.append(details);
      }
      if (this.kind === "institution") row.draggable = false;
      return row;
    }
    renderSelection() {
      super.renderSelection();
      const name = this.selection && this.model.find(this.selection.groupId)?.label;
      this.get("canonical-name").value = name || "";
      this.get("rename").disabled = this.busy || this.loading || !name;
      if (this.loading) {
        for (const id of ["destination", "move", "separate"]) this.get(id).disabled = true;
      }
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
