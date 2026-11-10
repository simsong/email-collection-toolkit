/* Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. */

/* Display the append-by-run ingest status history in an independent window. */
"use strict";

let statuses = [];
let selectedStatusId = new URLSearchParams(window.location.search).get("status");
let initialized = false;
let importing = false;
let starting = false;
let imapAccounts = [];
const historyRefresh = new RefreshTask(readHistory);
const byId = id => document.getElementById(id);
const integer = value => Number(value || 0).toLocaleString();
const stateLabel = status => status.timed_out ? "Timeout" : status.state;
const processingMode = status => !status.concurrent_processing && !(status.state !== "running" && (status.processed_messages || status.files_processed))
  && (status.activity_kind === "processing" || status.processing_active
    || ["recovering archived content", "recovering pending imports", "processing content"].includes(status.phase));
const processingActive = status => processingMode(status) && status.state === "running";
const phaseLabel = status => ({
  "recovering archived content": "Processing saved mail",
  "recovering pending imports": "Finishing saved imports",
  "processing content": "Processing message bodies and attachments",
})[status.phase] || status.phase;
const processorLabel = processor => ({
  headers: "Reading message headers", "queue-content": "Scheduling body and attachment processing",
  mime: "Extracting message bodies and attachments", "html-text": "Extracting readable HTML text",
  "text-index": "Updating search index", "identity-evidence": "Extracting names and addresses",
  "attached-message": "Processing attached messages", "rtf-text": "Extracting readable document text",
  preparing: "Preparing saved message",
})[processor] || processor;

window.addEventListener("pywebviewready", initialize);
window.setTimeout(() => {
  if (!initialized) showError("The native application bridge did not start.");
}, 1500);

async function initialize() {
  if (initialized) return;
  initialized = true;
  byId("import-directory").addEventListener("click", importDirectory);
  byId("suspend-import").addEventListener("click", () => controlImport("suspend_import"));
  byId("resume-import").addEventListener("click", () => controlImport("resume_import"));
  byId("update-imap-password").addEventListener("click", () => {
    byId("imap-account").replaceChildren(...imapAccounts.map(account => {
      const option = document.createElement("option");
      option.value = account.id;
      option.textContent = `${account.username} on ${account.server}:${account.port}`;
      return option;
    }));
    byId("imap-password-status").textContent = "";
    byId("cancel-imap-password").textContent = "Cancel";
    byId("imap-password-dialog").showModal();
    byId("imap-password").focus();
  });
  byId("cancel-imap-password").addEventListener("click", () => byId("imap-password-dialog").close());
  byId("imap-password-dialog").addEventListener("cancel", event => {
    if (byId("save-imap-password").disabled) event.preventDefault();
  });
  byId("imap-password-dialog").addEventListener("close", () => { byId("imap-password").value = ""; });
  byId("imap-password-form").addEventListener("submit", updateImapPassword);
  byId("install-antivirus").addEventListener("click", () => window.pywebview.api.install_antivirus());
  await refreshHistory();

}

window.refreshHistory = refreshHistory;
window.selectIngest = statusId => {
  selectedStatusId = statusId;
  render();
};

function refreshHistory() { return historyRefresh.run(); }

function ingestReply(method) {
  const operation = window.pywebview.api[method];
  if (!operation) return Promise.resolve(undefined);
  const action = {antivirus: "checking antivirus status", history: "reading import status",
    can_import_directory: "checking import availability", suspend_import: "suspending the import",
    resume_import: "resuming the import"}[method] || "updating this window";
  return new Promise((resolve, reject) => {
    const deadline = window.setTimeout(() => reject(new Error(
      `No confirmation from the application while ${action}. The import may still be running or changing state. `
      + "Return to this window to refresh status before repeating the command.")), 15000);
    Promise.resolve().then(() => operation()).then(resolve, reject).finally(() => window.clearTimeout(deadline));
  });
}

async function readHistory() {
  try {
    const antivirus = await ingestReply("antivirus");
    const history = await ingestReply("history");
    const canImport = await ingestReply("can_import_directory");
    byId("antivirus-warning").hidden = !antivirus || antivirus.configured;
    byId("antivirus-detail").textContent = antivirus?.detail || "";
    const previousLatestId = statuses[0]?.status_id;
    statuses = history.statuses || [];
    const newRun = previousLatestId && statuses[0] && previousLatestId !== statuses[0].status_id;
    if (newRun || (starting && !history.starting) || !statuses.some(status => status.status_id === selectedStatusId)) {
      selectedStatusId = statuses[0]?.status_id || null;
    }
    starting = Boolean(history.starting);
    imapAccounts = history.imap_accounts || [];
    byId("configuration-warning").textContent = (history.warnings || []).join("\n");
    byId("configuration-warning").hidden = !history.warnings?.length;
    byId("update-imap-password").hidden = imapAccounts.length === 0;
    byId("update-imap-password").disabled = Boolean(history.active) || importing;
    byId("suspend-import").disabled = importing || !history.active || history.suspending;
    byId("resume-import").disabled = importing || !history.can_resume;
    byId("resume-import").textContent = statuses[0]?.state === "paused" ? "Resume" : "Continue Import";
    byId("import-control-status").textContent = history.suspending
      ? "Suspending import… waiting for current work to finish and save its checkpoint."
      : statuses[0]?.state === "paused" ? "Download paused. Reconnect to the network, then choose Resume."
      : history.can_resume ? "Import is suspended or unfinished. Continue Import resumes saved work." : "";
    byId("import-control-status").hidden = !byId("import-control-status").textContent;
    byId("starting-import").hidden = !starting;
    render(history.errors || []);
    byId("import-directory").disabled = importing || !canImport;
  } catch (error) {
    showError(`Could not read ingest history: ${error}`);
  }
}

async function updateImapPassword(event) {
  event.preventDefault();
  if (byId("save-imap-password").disabled) return;
  byId("imap-account").disabled = true;
  byId("imap-password").disabled = true;
  byId("save-imap-password").disabled = true;
  byId("cancel-imap-password").disabled = true;
  byId("imap-password-status").textContent = "Testing login…";
  try {
    if (!await window.pywebview.api.update_imap_password(byId("imap-account").value, byId("imap-password").value)) {
      throw new Error("The password could not be saved.");
    }
    byId("imap-password-status").textContent = "Login succeeded. Password saved. Choose Continue Import to resume.";
    byId("cancel-imap-password").textContent = "Close";
  } catch (error) {
    byId("imap-password-status").textContent = String(error?.message || error);
  } finally {
    byId("imap-password").value = "";
    byId("imap-account").disabled = false;
    byId("imap-password").disabled = false;
    byId("save-imap-password").disabled = false;
    byId("cancel-imap-password").disabled = false;
  }
}

async function importDirectory() {
  if (importing) return;
  importing = true;
  byId("import-directory").disabled = true;
  byId("error").hidden = true;
  try {
    await window.pywebview.api.import_directory();
  } catch (error) {
    showError(`Could not import directory: ${String(error?.message || error)}`);
  } finally {
    importing = false;
    await historyRefresh.fresh();
  }
}

async function controlImport(method) {
  if (importing) return;
  importing = true;
  byId("suspend-import").disabled = true;
  byId("resume-import").disabled = true;
  byId("error").hidden = true;
  byId("import-control-status").textContent = method === "suspend_import" ? "Suspending import…" : "Continuing import…";
  byId("import-control-status").hidden = false;
  try {
    if (!await ingestReply(method)) throw new Error("The import state changed. Please try again.");
  } catch (error) { showError(String(error?.message || error)); }
  finally { importing = false; await historyRefresh.fresh(); }
}

function render(errors = []) {
  byId("history-count").textContent = `${integer(statuses.length)} run${statuses.length === 1 ? "" : "s"}`;
  const list = byId("history-list");
  list.replaceChildren(...statuses.map(historyRow));
  const errorBox = byId("history-errors");
  errorBox.hidden = errors.length === 0;
  errorBox.textContent = errors.map(error => `${error.filename}: ${error.detail}`).join("\n");
  const selected = statuses.find(status => status.status_id === selectedStatusId);
  renderDetail(selected || null);
}

function historyRow(status) {
  const row = document.createElement("button");
  row.type = "button";
  row.className = `history-row${status.status_id === selectedStatusId ? " selected" : ""}`;
  row.setAttribute("role", "option");
  row.setAttribute("aria-selected", String(status.status_id === selectedStatusId));
  const dot = document.createElement("span");
  dot.className = `state-dot ${status.state}`;
  dot.setAttribute("aria-hidden", "true");
  dot.textContent = ["failed", "disk-full", "stale"].includes(status.state) ? "X"
    : ["running", "completed"].includes(status.state) ? "✓" : status.state === "paused" ? "−" : "Ⅱ";
  const main = document.createElement("span");
  main.className = "history-main";
  const title = document.createElement("span");
  title.className = "history-title";
  const state = document.createElement("span");
  state.className = "history-state";
  state.textContent = stateLabel(status);
  const percent = document.createElement("span");
  percent.textContent = processingActive(status) ? "Processing" : `${Number(status.percent).toFixed(1)}%`;
  title.append(state, percent);
  const time = document.createElement("span");
  time.className = "history-time";
  time.textContent = new Date(status.started_at).toLocaleString();
  const version = document.createElement("span");
  version.className = "history-version";
  version.textContent = runVersion(status);
  const summary = document.createElement("span");
  summary.className = "history-summary";
  const imapOnly = status.source_roots.length && status.source_roots.every(root => root.startsWith("imap://"));
  const messages = imapOnly && status.download_coverage ? status.download_coverage.messages : status.processed_messages;
  summary.textContent = `${processingMode(status) ? `${integer(status.processing_jobs_completed)} processing tasks finished`
    : `${integer(messages)} source messages`} · ${formatDuration(status.elapsed_seconds)}`;
  main.append(title, time, version, summary);
  row.append(dot, main);
  row.addEventListener("click", () => { selectedStatusId = status.status_id; render(); });
  return row;
}

function runVersion(status) {
  return status.application_version ? `Version ${status.application_version}` : "Version unavailable";
}

function renderDetail(status) {
  const detail = byId("ingest-detail");
  detail.replaceChildren();
  if (!status) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "No ingest history is available for this archive.";
    detail.append(empty);
    return;
  }
  const heading = document.createElement("div");
  heading.className = "detail-heading";
  const headingText = document.createElement("div");
  const title = document.createElement("h1");
  title.textContent = `Ingest ${status.run_pk}`;
  const subtitle = document.createElement("p");
  subtitle.textContent = `${new Date(status.started_at).toLocaleString()} · ${runVersion(status)} · process ${status.process_id}`;
  if (status.scan_policy === "not-scanned") {
    subtitle.textContent += " · WARNING: imported without antivirus scanning";
    subtitle.className = "failure";
  }
  headingText.append(title, subtitle);
  const badge = document.createElement("span");
  badge.className = "state-badge";
  badge.textContent = stateLabel(status);
  heading.append(headingText, badge);

  const track = document.createElement("div");
  track.className = "progress-track";
  const fill = document.createElement("div");
  fill.className = "progress-fill";
  const processingNow = processingActive(status);
  if (processingNow) fill.classList.add("indeterminate");
  fill.style.width = `${Math.max(0, Math.min(Number(status.percent), 100))}%`;
  track.append(fill);
  const caption = document.createElement("div");
  caption.className = "progress-caption";
  track.setAttribute("role", "progressbar");
  track.setAttribute("aria-label", processingNow ? "Processing jobs" : "Source import progress");
  const processing = `${integer(status.processing_jobs_completed)} tasks finished · ${processorLabel(status.processing_processor || "preparing")}`;
  track.setAttribute("aria-valuetext", processingNow ? processing : `${Number(status.percent).toFixed(1)}%`);
  if (!processingNow) track.setAttribute("aria-valuenow", Number(status.percent));
  caption.append(textSpan(phaseLabel(status)), textSpan(processingNow ? processing : status.state === "paused"
    ? `${Number(status.percent).toFixed(1)}% saved` : `${Number(status.percent).toFixed(1)}% · ETA ${!status.bytes_total && status.files_processed < status.files_total ? "unavailable" : status.eta}`));

  detail.append(heading, track, caption, statistics(status));
  if (status.concurrent_processing) {
    detail.append(textBlock("New mail downloads while saved messages and attachments are processed.", "processing-explanation"));
  }
  if (processingMode(status)) {
    const explanation = document.createElement("p");
    explanation.className = "processing-explanation";
    explanation.textContent = "Processing messages and MIME parts already saved in this archive. Each email stays in the count until all its tasks finish. The count can stay level while earlier processing stages finish.";
    if (phaseLabel(status) === "Processing saved mail" && status.state === "running" && status.source_roots.length) {
      explanation.textContent += " Source downloading/importing resumes after this saved work finishes.";
    }
    detail.append(explanation);
  }
  if (status.source_roots.some(root => root.startsWith("imap://"))) {
    detail.append(section("Downloaded message coverage", messageCoverage(status)));
  }
  if (status.failure_detail) detail.append(section("Failure", textBlock(status.failure_detail, "failure")));
  detail.append(section("Workers", workerTable(status.workers)));
  if (status.saved_workers?.length) detail.append(section("Saved-mail processing workers", workerTable(status.saved_workers)));
  if (status.source_roots.length) {
    const sources = document.createElement("ul");
    sources.className = "source-list";
    for (const source of status.source_labels) {
      const item = document.createElement("li");
      item.textContent = source;
      sources.append(item);
    }
    detail.append(section("Sources", sources));
  }
}

function statistics(status) {
  const processing = processingMode(status);
  const imap = status.source_roots.length > 0 && status.source_roots.every(root => root.startsWith("imap://"));
  const values = processing ? [
    ["Messages requiring processing", status.saved_messages_remaining == null ? "Unavailable" : integer(status.saved_messages_remaining)],
    ["Processing tasks finished", integer(status.processing_jobs_completed)],
    ["Average processing rate", `${(Number(status.processing_jobs_completed) / Math.max(Number(status.elapsed_seconds), 1)).toFixed(2)} tasks/s`],
    ["Elapsed", formatDuration(status.elapsed_seconds)],
  ] : [
    [imap ? "Source messages downloaded" : "Source messages imported", imap ? status.download_coverage ? integer(status.download_coverage.messages) : "Unavailable" : integer(status.processed_messages)],
    [imap ? "Download partitions finished" : "Source files finished", `${integer(status.files_processed)} / ${integer(status.files_total)}`],
    ["Elapsed", formatDuration(status.elapsed_seconds)],
    [imap ? "Average download rate" : "Average import rate", imap && !status.download_coverage ? "Unavailable" : `${Number(imap && status.download_coverage ? status.download_coverage.messages / Math.max(status.elapsed_seconds, 1) : status.message_rate).toFixed(2)} messages/s`],
    ["Archived", integer(status.counts.archived)],
    ["Previously seen", integer(status.counts.duplicates)],
    ["Autosaved", integer(status.counts.autosaves)],
    ["Metadata", integer(status.counts.metadata_excluded)],
    ["Infected", integer(status.counts.infected)],
    ["Unchanged", integer(status.counts.unchanged_sources)],
  ];
  const grid = document.createElement("div");
  if (!processing && status.processing_jobs_completed) values.push(["Processing tasks finished", integer(status.processing_jobs_completed)]);
  if (!processing && status.saved_messages_remaining != null) values.push(["Messages requiring processing", integer(status.saved_messages_remaining)]);
  grid.className = "statistics";
  for (const [label, value] of values) {
    const cell = document.createElement("div");
    cell.className = "stat";
    if (label === "Processing tasks finished") cell.title = "Completed tasks for messages or MIME parts, including metadata extraction and search indexing. Several tasks can belong to one email.";
    if (label === "Messages requiring processing") cell.title = "Distinct archived emails with pending, running or failed work, including body and attachment tasks and blocked prerequisites. An email leaves the count only when all its tasks finish. Excludes future downloads. Older runs may not report this count.";
    cell.append(textSpan(label), textBlock(value, "strong"));
    grid.append(cell);
  }
  return grid;
}

function messageCoverage(status) {
  const coverage = document.createElement("div");
  coverage.className = "message-coverage";
  const measured = status.download_coverage;
  if (!measured) { coverage.append(textBlock("Download coverage unavailable for this older run.")); return coverage; }
  const date = value => value ? new Date(value).toISOString().slice(0, 10) : "Unavailable";
  coverage.append(textBlock(`This run: ${integer(measured.messages)} messages · Earliest: ${date(measured.earliest_date)} · Latest: ${date(measured.latest_date)}`));
  coverage.append(textBlock("Dates reported by the mail server (UTC). Counts cover downloads in this run; attached emails and later processing are excluded."));
  const table = document.createElement("table");
  table.className = "coverage-table";
  const head = document.createElement("thead");
  const headings = document.createElement("tr");
  for (const label of ["Year", "Messages"]) {
    const cell = document.createElement("th");
    cell.scope = "col";
    cell.textContent = label;
    headings.append(cell);
  }
  head.append(headings);
  const body = document.createElement("tbody");
  for (const entry of [...measured.years].sort((a, b) => a.year - b.year)) {
    const row = document.createElement("tr");
    for (const value of [String(entry.year), integer(entry.messages)]) {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.append(cell);
    }
    body.append(row);
  }
  table.append(head, body);
  coverage.append(table);
  if (!measured.years.length) coverage.append(textBlock("No dated messages downloaded in this run yet."));
  return coverage;
}

function workerTable(workers) {
  const table = document.createElement("table");
  table.className = "worker-table";
  const headings = document.createElement("tr");
  for (const label of ["Thread", "Operation", "Current task / source", "Download / import progress", "Processing tasks finished"]) {
    const heading = document.createElement("th");
    heading.textContent = label;
    headings.append(heading);
  }
  const head = document.createElement("thead");
  head.append(headings);
  const body = document.createElement("tbody");
  for (const worker of workers) {
    const row = document.createElement("tr");
    const source = worker.path || worker.last_path || "—";
    const savedTask = worker.phase.startsWith("processing ") || worker.activity_unit === "jobs";
    const operation = worker.phase === "idle" ? "Idle" : savedTask ? "Processing saved mail"
      : ({downloading: "Downloading", processing: "Processing imported mail", importing: "Importing"})[worker.operation] || "Importing";
    const task = worker.phase.startsWith("processing ") ? processorLabel(worker.phase.slice("processing ".length)) : worker.phase;
    const progress = savedTask ? "—" : worker.activity_unit
      ? `${integer(worker.activity_done)}${worker.activity_total === null ? "" : ` / ${integer(worker.activity_total)}`} ${worker.activity_unit}`
      : worker.bytes_total ? `${formatBytes(worker.bytes_done)} / ${formatBytes(worker.bytes_total)}` : "—";
    for (const [value, className] of [
      [worker.worker, ""], [operation, ""], [`${task}\n${source}`, "worker-path"], [progress, ""],
      [worker.processing_jobs_completed == null ? "Unavailable" : integer(worker.processing_jobs_completed), ""],
    ]) {
      const cell = document.createElement("td");
      cell.className = className;
      cell.textContent = value;
      row.append(cell);
    }
    body.append(row);
  }
  table.append(head, body);
  const scroll = document.createElement("div");
  scroll.className = "worker-table-scroll";
  scroll.append(table);
  return scroll;
}

function section(title, content) {
  const block = document.createElement("section");
  block.className = "detail-section";
  const heading = document.createElement("h2");
  heading.textContent = title;
  block.append(heading, content);
  return block;
}

function textSpan(value) {
  const span = document.createElement("span");
  span.textContent = value;
  return span;
}

function textBlock(value, className) {
  const block = document.createElement(className === "strong" ? "strong" : "div");
  if (className !== "strong") block.className = className;
  block.textContent = value;
  return block;
}

function formatDuration(value) {
  const seconds = Math.max(0, Math.round(Number(value || 0)));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainder = seconds % 60;
  return [hours ? `${hours}h` : "", hours || minutes ? `${minutes}m` : "", `${remainder}s`].filter(Boolean).join(" ");
}

function formatBytes(value) {
  let amount = Number(value || 0);
  for (const unit of ["B", "KiB", "MiB", "GiB", "TiB"]) {
    if (amount < 1024 || unit === "TiB") return unit === "B" ? `${amount} B` : `${amount.toFixed(1)} ${unit}`;
    amount /= 1024;
  }
  return "0 B";
}

function showError(message) {
  const error = byId("error");
  error.textContent = message;
  error.hidden = false;
}

window.addEventListener("mailarchiver-status-changed", () => historyRefresh.changed());
window.addEventListener("focus", () => { if (initialized) historyRefresh.changed(); });
if (window.pywebview?.api?.history) initialize();
