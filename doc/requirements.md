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
  through futures; only their owner commits durable state. See [acquisition](requirements/ACQUISITION.md).
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
  Native Close targets the focused window, including standalone list editors.
  Navigation retains the displayed visibility and date filters; provider domains
  are excluded from derived institution membership and search aliases.
  Name rows display observed signature phones, with multiple numbers expandable.
  See [desktop navigation](requirements/DESKTOP.md#identity-picker-navigation).

- GUI imports use tracked futures; queued imports cancel safely on Quit,
  retaining restartable work. See [desktop](requirements/DESKTOP.md).
- Track Cocoa archive-open callbacks with command futures; see [desktop](requirements/DESKTOP.md).
- Track Cocoa About-window commands with futures; see [desktop](requirements/DESKTOP.md).
- Confirmed Quit offers **Suspend import and quit** and dims loaded windows with
  a Shutting down notice; see [desktop shutdown](requirements/DESKTOP.md#shutdown-feedback).
- Desktop recovery requires an informed Continue Import/defer/abort choice, and collapsed
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
