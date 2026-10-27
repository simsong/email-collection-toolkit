<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Mail archive normalizer requirements

Requirements are organized by subsystem. These files describe current contracts and
source behavior, including explicit limits; they do not assert that every native
platform or release gate has passed. [Future work](plans/README.md) is separate.

| Subsystem | Scope |
| --- | --- |
| [Archive](requirements/ARCHIVE.md) | Canonical storage, hashes, deduplication and recovery |
| [Acquisition](requirements/ACQUISITION.md) | Local sources, IMAP, PST/OST and diagnostic acquisition |
| [Processing](requirements/PROCESSING.md) | Processors, identities, Contacts CLI and synthetic matcher |
| [Search](requirements/SEARCH.md) | Search indexing, headers, dates and verified text display |
| [Desktop](requirements/DESKTOP.md) | Documents, native UI, browser boundary and message viewing |
| [Scanning](requirements/SCANNING.md) | Embedded scanner, evidence, definitions and timeouts |
| [Delivery](requirements/DELIVERY.md) | Platform packaging, release identity, signing and publication |
| [Validation](requirements/VALIDATION.md) | Fixtures, independent checks, CI and acceptance limits |

## Shared boundaries

- Source and relocated native acceptance retain current phase and timeout diagnostics; bounded
  deadlines must allow the component checks to run. See [validation](requirements/VALIDATION.md).

- Preferences persist half-CPU, all-CPU or custom worker limits, defaulting to
  half the logical CPUs and never exceeding twice that count. Explicit CLI
  workers override and disable the desktop choice for that launch.
  Preferences expose only their intended JavaScript methods, excluding native window state.
  Both Preferences windows save valid edits automatically and on Close, with
  standard platform window controls and no Done or Save button. See [desktop](requirements/DESKTOP.md).

- Reprocessing is curator-invoked, with a long-duration confirmation; code and
  settings changes never invalidate completed work. See [processing](requirements/PROCESSING.md)
  and [desktop](requirements/DESKTOP.md).

- Closing an import owner transfers its operation to another same-archive search;
  the last-window veto must never block the native event loop. See [desktop](requirements/DESKTOP.md).

- Processing progress labels distinct archived messages **Messages requiring processing**;
  each leaves the count after its final task finishes. See [desktop](requirements/DESKTOP.md).

- Progress separates downloads/imports from saved-mail processing, with independent
  worker task totals in the table and no worker-count summary cards.
  See [desktop](requirements/DESKTOP.md).

- Scanner preflight keeps publishing import heartbeats even during blocking
  definition checks and updates. See [scanning](requirements/SCANNING.md).

- The macOS development launcher seals its bundle identity and preserves the
  original interpreter's native credential access. See [delivery](requirements/DELIVERY.md).

- About opens an offline processor DAG from the active registry, including
  open-ended MIME extraction outputs. See [processing](requirements/PROCESSING.md#registered-processor-graph).

- Fixes handed back for testing advance the version; About retains launch/build
  Git provenance and discloses local changes. See [delivery](requirements/DELIVERY.md).

- IMAP workers prioritize missing per-message checkpoints before refreshing saved
  metadata; interrupted UID gaps remain eligible. See [acquisition](requirements/ACQUISITION.md).

- Imports can suspend and resume without quitting; identity pickers display
  loading and retry feedback. See [desktop](requirements/DESKTOP.md).

- About status remains available during import writer locks; plugin inventory
  retries separately. See [desktop](requirements/DESKTOP.md).

- Shared test acceptance retains reader preservation, scanner API/platform
  boundaries and all legacy MBOX recovery cases; see [validation](requirements/VALIDATION.md#shared-acceptance-coverage).

- Ordinary pytest includes disposable TLS IMAP acquisition through CLI and GUI
  background services, with three-message search/read-only/rerun checks;
  see [validation](requirements/VALIDATION.md#offline-imap-fixtures).

- Manual review must never gate application processing. Python ingestion records
  scan errors/incomplete scans and preserves originals while continuing;
  see [scanning](requirements/SCANNING.md#malware-handling).

- Recovery shows active processor/job work independently of source-import counts,
  and queue lookup avoids rescanning completed history; see [processing](requirements/PROCESSING.md).

- Deferred message/content recovery uses the configured worker pool with ordered
  per-message checkpoints; see [processing](requirements/PROCESSING.md).
  Continue Processing stays disabled during startup and active archive work.

- End-of-import processor statistics use an aligned table labeled as cumulative
  archive history; see [processing](requirements/PROCESSING.md).

- Freeze all manifest-referenced acquisition adapters; validate local ingestion
  with `make test-frozen-source-plugins` before packaging handoff.
- Source workers retain thread-bound input handles and return publication requests
  through futures; only their owner commits durable state. Single-worker processor
  waits retain live status heartbeats. See [acquisition](requirements/ACQUISITION.md).
- The experimental Rust UI, helper and obsolete build/test paths are removed from
  the active checkout. The signed `experimental-rust-ui` tag preserves the
  October 10, 2026 retirement snapshot; see the [archive note](RUST_GUI_EXPERIMENTS.md).
- Preview workers return tracked results; current-generation GUI polling alone
  updates preview caches. See [desktop](requirements/DESKTOP.md).
- Original IMAP folder labels decode percent escapes once while retaining stored
  selection paths; see [desktop](requirements/DESKTOP.md).
- Natural search preserves quoted symbols and works with existing indexes;
  see [search behavior](requirements/SEARCH.md).

- [Customizable Default Lists](CUSTOMIZABLE_DEFAULT_LISTS.md) provide typed bundled
  templates, personal overrides, revisioned archive snapshots and a shared editor.
  Provider-scoped reply-address rules retain attributed routing evidence without
  accepting identity merges. Owner defaults start empty and prompt during setup.

- Person views hide recognized non-person identities reversibly, retaining uncertain
  names and original evidence. Research comparisons can weight exact full names
  by frequency; see [processing research](requirements/PROCESSING.md#local-name-linkage-research).

- Optional local name-linkage comparisons retain private evidence and distinguish
  synthetic accuracy from unlabeled archive scores. Owner review distinguishes
  non-person flags independently of identity-link judgments; see [processing research](requirements/PROCESSING.md#local-name-linkage-research).

- Identity picker labels open name searches; count buttons open filtered evidence messages.
  Navigation shows pending/error feedback and targets the clicked row independently of selection.
  Count/name navigation and focus changes do not repeat the initial recovery prompt.
  Native Close targets the focused window, including standalone list editors.
  Navigation retains the displayed visibility and date filters; provider domains
  are excluded from derived institution membership and search aliases.
  Name rows display observed signature phones, with multiple numbers expandable.
  See [desktop navigation](requirements/DESKTOP.md#identity-picker-navigation).

- GUI imports use tracked futures; queued imports cancel safely on Quit,
  retaining restartable work. See [desktop](requirements/DESKTOP.md).
- Cocoa Open/About/Setup commands and startup export cleanup use tracked futures;
  see [desktop](requirements/DESKTOP.md) and the [threading audit](THREADING.md).
- Confirmed Quit offers **Suspend import and quit** and dims loaded windows with
  a Shutting down notice; see [desktop shutdown](requirements/DESKTOP.md#shutdown-feedback).
- Desktop recovery offers Continue Import for both phases, Leave import suspended,
  or Stop import while retaining archived messages; collapsed
  warnings remain readable; see [desktop recovery](requirements/DESKTOP.md#saved-import-recovery-and-notices).
  Ingest history displays IMAP accounts and servers instead of internal locators.
  Startup is visible before the first run snapshot; enlarged status circles use
  symbols alongside color and text labels.

- Scanner threads return tracked results to their process coordinator, which alone
  writes responses; see [scanning](requirements/SCANNING.md).
- Embedded scanners disable URL-phishing checks while retaining malware scanning;
  see the [scanning policy](requirements/SCANNING.md#malware-handling).
- Before Python scan sessions, refresh definitions over 24 hours old when online;
  offline or failed refreshes retain existing definitions. See [scanning](requirements/SCANNING.md).
- Retry saved scanner failures with the current engine and restore archived content
  before pending ingest in normal unbounded runs; see [processing recovery](requirements/PROCESSING.md#working-object-lifetime).
- Release cached raw mail after each MBOX publication and SQL commit, preserving
  live derived inputs and retry checkpoints;
  see [working-object lifetime](requirements/PROCESSING.md#working-object-lifetime).
- Preserve source stores and original message bytes, with the documented IMAP
  CRLF-to-LF acquisition exception; canonical MBOX/manifests
  remain the portable preservation record.
- Back up manual decisions, configuration and operational history; rebuilding
  search does not recover that durable state.
- Keep executable syntax in [CLI_REFERENCE.md](CLI_REFERENCE.md), with workflows
  in [USER_MANUAL.md](USER_MANUAL.md).
- Keep future features in [plans/](plans/README.md) and dated findings in
  [history/](history/README.md). The retired GUI has one
  [retrospective](RUST_GUI_EXPERIMENTS.md).

When changing behavior, update the relevant subsystem contract and implementation
together and record its substantive validation. This overview is the stable
entry point; incoming section links have been updated to their owning files.
