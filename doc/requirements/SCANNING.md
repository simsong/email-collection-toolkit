<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Scanning requirements

Current subsystem contract and acceptance boundaries. This is not a claim of
platform/release acceptance. Deferred features live in [plans](../plans/README.md).

## Malware handling

**PR #124 migration:** direct libclamav scanning replaces daemon/CLI scanning.
[Embedded antivirus](../EMBEDDED_CLAMAV.md) defines concurrent shared-engine scans,
infected-only API headers, macOS/Windows storage,
and definition updates. About shows daily definitions' publication date/age and
a yellow recommendation after three calendar months. Releases must refresh
bundled definitions at least quarterly. GPL-2.0-only application licensing matches
ClamAV's license version; other dependency compatibility remains pending as
recorded in THIRD_PARTY_NOTICES.md.

* Each new message is streamed to ClamAV unless the user explicitly chooses
  an unscanned import. Missing or failed scanning must never silently mean clean.
  The CLI requires exactly one of `--clamav` or `--no-scan`; the latter records
  `not-scanned` in run status and an antivirus metadata defect on each new message.
  Earlier run status without this field is unknown, not presumed scanned.
  Repeat imports do not retroactively scan previously archived messages.
* Before each ordinary Python scanner session, compare the selected daily database's
  publication timestamp with the current UTC time. When it is strictly more than
  24 hours old, check Internet reachability and, if online, automatically run the
  one-shot definition updater before loading the scan engine. Definition age alone
  never blocks scanning. Offline checks, CDN failures, updater contention and invalid
  downloads retain the existing definitions; successful updates use the newly selected
  validated generation. Stop/Quit must remain effective during the update.
  Explicit `MAILARCHIVER_CLAMAV_DATABASE` overrides and caller-supplied definition
  sets remain pinned without network checks, including candidate validation engines.
  Definition checking, copying, updating and engine initialization must keep the
  import heartbeat current, including blocking steps that emit no callbacks.
  Definition age or normal preflight waits must not produce a stale/failure X.
  No resident updater or scheduled job is created.
* Python and Rust scanners disable ClamAV's URL-phishing module, including
  suspicious-link domain/protocol mismatch checks. These checks can flag legitimate
  marketing redirects. Signature-based malware/phishing and bytecode scanning,
  MIME/attachment parsing, and encrypted/over-limit reporting remain enabled.
  This policy also removes URL-module detections of genuine phishing links;
  a clean scan is not a guarantee that links are safe. Existing quarantine
  decisions are not retroactively changed.
* The scan-worker coordinator owns response IPC and observes every submitted
  future. Native scan threads return typed results without sending IPC or
  mutating shared publication state. Shutdown drains accepted requests. Request
  backpressure must not hold the state lock needed to deliver responses.
* Host ingestion shares one compiled engine across native scan threads.
  A temporary app-owned worker provides startup/scan deadlines; it exposes no
  persistent service or socket. Python records scan results through the existing
  processing database. Source workers scan before
  owner-thread publication; canonical MBOX publication stays serialized.
* API producers perform antivirus scanning themselves. Only infected messages
  carry `X-ClamAV-Detection`, `X-ClamAV-Engine-Version`, and
  `X-ClamAV-Definitions-Version` headers. Python reads the detection header to
  route the emitted message to INFECTED without rescanning. Clean messages have
  no antivirus provenance. Python alone hashes messages and deduplicates them;
  emitters need no per-message hashes, database access, or scan receipts.
  Failures are reported to the operator, who can re-import using normal deduplication;
  the API does not automatically restart an interrupted import.
* Development definitions live in ignored `etc/clamdb/`. `make freshclam` seeds
  that directory from an installed database when available, then refreshes it.
  Both installer builders instead run the staged application's explicit-directory
  downloader with its bundled engine, updater and certificates. Publish the new
  export only after native signature loading and clean/EICAR checks; refuse an
  existing destination and do not read or replace the user's active generations.
  Retain failed-download cooldown state beside the explicit build output.
  Before running the mounted app self-test, DMG validation records every mounted
  file and symlink in a retained inventory and checks that `libclamav.dylib` is
  present. Release CI also lists every inventory entry in the Actions log before
  testing the installed app. A present library rejected by the native loader
  must report the underlying loader error, not misidentify it as a missing
  file. A failed self-test never publishes the candidate DMG.
* `ingest --workers N` controls the number of source containers ingested
  simultaneously. Its default is the detected CPU count capped at eight, and
  `N` must be positive. Each worker reads and parses its mailfile and submits
  one ClamAV request at a time. Duplicate admission, SQLite commits, the
  publication journal, and canonical MBOX appends remain serialized through
  one publisher. A source plug-in may declare a lower concurrency limit for
  each source-native `concurrency_key`; the framework enforces it and fairly
  interleaves captured keys. A shared plug-in instance must be reentrant. A
  resume decision is accepted only from a source declaring resumable support.
* Mailfile workers send typed status updates to a main-thread status driver;
  worker threads never write progress output. The driver writes standard error
  at startup, every 250 milliseconds, and completion. An interactive terminal
  receives a redraw-in-place scoreboard with a numbered row for every
  configured worker and a white-on-blue top line reporting aggregate byte and file
  percentage and ETA; redirected output reports the same aggregate fields in
  line-oriented text without terminal controls. Both report the main-thread
  `waiting for ClamAV startup` preflight and worker phases including `checking`,
  `ingesting`, `scanning`, `waiting to publish`, `publishing`, `checkpointing`,
  and `idle`. They also report elapsed time, processed message and completed
  source-file counts, average message rate, resolved date range, current
  year/current-year count, active and peak worker counts, source file, and byte
  or provider-message progress. Unknown-byte sources use completed containers
  rather than reporting 100% prematurely. Lines must be fitted to the terminal width so redraws
  never accumulate wrapped headings. It separately reports archived, duplicate
  previously-seen duplicates, autosave-excluded, source-metadata-excluded,
  infected, unrecognized-file, and integrity-skipped-container counts. A
  worker never prints a skipped-container diagnostic itself; the main status
  driver prints each queued path and reason once.
  While the main thread waits for ClamAV to load virus definitions, every
  refresh explicitly identifies that wait and shows its increasing startup
  elapsed time instead of a stale source-file status. Readiness requires
  successful native initialization and definition compilation; library load
  failure is an antivirus error, not a missing mail source. Startup has a
  120-second deadline and production scans have a 60-second deadline. A timeout
  is a scanner failure, never a clean or infected result. Owned plaintext
  temporaries are removed after worker shutdown.
* Control-C immediately prints and flushes `**Interrupted. Shutting down…**`
  before waiting for workers or beginning cleanup. Print it once and preserve
  it across terminal dashboard redraws. It is a graceful stop: close scanner and MBOX resources, commit
  completed messages and observations, publish a complete BagIt/Mailbag
  checkpoint, report interruption,
  print the standard archive report for the completed partial run, and return
  exit status 130 without a traceback.  An `ENOSPC` MBOX append
  must be rolled back to the prior file size where possible, reported, and
  stopped without silently treating the message as archived.
* Every ingest run records its completion time, result, and failure detail.
  Failure detail retains traceback filenames and source-code line numbers,
  including underlying Pydantic validator exceptions. For an available message,
  include its source reference, neutrally labelled native cursor (byte offset for
  local MBOX), SHA-256,
  length, and an escaped prefix of up to 4,096 input bytes plus up to 512 bytes
  per selected/original/quoted envelope. Limit each source identity field and
  cursor to 1,024 characters plus a truncation marker; omit arbitrary source
  provenance and hierarchy from failure reports. Limit exception summaries to
  2,048 characters, each exception note to 32,768 characters, and the complete
  rendered failure to 65,536 characters, plus truncation markers. Pydantic summaries and chained tracebacks omit input-value dumps so
  they cannot bypass these preview limits. These local diagnostics may contain
  private mail; they are not public telemetry. A failure between messages identifies the container without
  attributing it to the previously yielded message. Persist the same detail in
  the run catalog and Ingests status, without changing source or canonical mail.
  An unexpected parser failure preserves earlier published messages, closes
  resources, refreshes the BagIt/Mailbag checkpoint for committed MBOX changes, and leaves a
  rerunnable error observation containing the source cursor (and numeric offset
  when available) plus raw SHA-256.
* The main status driver writes the same typed state used by terminal rendering
  to one run-specific `status/ingest-*.json` file. The JSON contract records a
  format/version identifier, archive and run identity, process and source
  roots, timestamps, state, phase, aggregate progress, dates, rates,
  disposition totals, and every configured worker's current and cumulative
  statistics. Updates use same-directory atomic replacement. A later ingest
  creates a new file and never replaces an earlier run's final status.
* Before each MBOX append, ingest durably records the target, prior length,
  message identity, and whether the target already existed. Catalog changes
  remain uncommitted until the append is complete. On an exception or the next
  startup after process death, an uncatalogued append is removed; a catalogued
  append is hash-validated, retained, and its journal is cleared.
* Completed and interrupted ingests print the archive report.  Reports always
  include year totals and default to the top 10 senders and recipients for the
  selected scope.  All report sections are aligned tables with right-aligned,
  comma-grouped numeric columns. Correspondent tables include the first and
  last message dates for each address. Addresses identified by Sent classification are suppressed
  from these correspondent lists only; they remain in the catalog and yearly
  people counts.  `--top 0` suppresses correspondent lists.
  Year ranges must be ascending and correspondent limits must be nonnegative.
* ClamAV outcomes are `clean`, `infected`, `unscannable`, or `scanner-error`,
  with scanner version, signature database version, and diagnostic retained.
* Python host ingestion shall preserve originals unchanged and continue for
  `unscannable` and `scanner-error`, including encrypted/over-limit input,
  scanner startup failures and timeouts. Retain the verdict, diagnostic and
  available engine/definition versions, with an antivirus metadata defect.
  Never reinterpret an incomplete/error scan as clean. Positive detections
  quarantine automatically. No manual review or scanner-availability approval
  may gate continuation; storage/integrity failures and cancellation remain errors.
* Only a positive detection routes a message to `INFECTED1.mbox`; an
  unscannable attachment or scanner failure does not destroy or silently
  exclude a message.
* Infected and malformed quarantine mail remains catalogued but is excluded
  from the disposable search index, ordinary search listings, reports, and
  correspondent statistics. Its canonical MBOX content remains available for
  optional inspection or export; it never gates later imports.
