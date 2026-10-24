<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Mail archive normalizer implementation

Implementation is organized by subsystem. These files describe current contracts and
source behavior, including explicit limits; they do not assert that every native
platform or release gate has passed. [Future work](plans/README.md) is separate.

| Subsystem | Scope |
| --- | --- |
| [Archive](implementation/ARCHIVE.md) | Canonical storage, hashes, deduplication and recovery |
| [Acquisition](implementation/ACQUISITION.md) | Local sources, IMAP, PST/OST and diagnostic acquisition |
| [Processing](implementation/PROCESSING.md) | Processors, identities, Contacts CLI and synthetic matcher |
| [Search](implementation/SEARCH.md) | Search indexing, headers, dates and verified text display |
| [Desktop](implementation/DESKTOP.md) | Documents, native UI, browser boundary and message viewing |
| [Scanning](implementation/SCANNING.md) | Embedded scanner, evidence, definitions and timeouts |
| [Delivery](implementation/DELIVERY.md) | Platform packaging, release identity, signing and publication |
| [Validation](implementation/VALIDATION.md) | Fixtures, independent checks, CI and acceptance limits |

## Shared boundaries

- Reader/scanner acceptance reuses the stronger existing checks and Make targets;
  legacy MBOX cases share setup. See [validation](implementation/VALIDATION.md#shared-acceptance-coverage).

- Pytest-owned IMAP servers seed three INBOX messages and verify CLI/background-GUI
  ingestion through searchable output; see [validation](implementation/VALIDATION.md#offline-imap-fixtures).

- Python scan exceptions route automatically to retained originals with recorded
  evidence, without a manual-review gate; see [scanning](implementation/SCANNING.md#automatic-scan-error-handling).

- Indexed queue selection and dispatcher activity callbacks expose recovery work;
  see [processing](implementation/PROCESSING.md).

- Deferred message/content recovery uses the configured worker pool with ordered
  per-message checkpoints; see [processing](implementation/PROCESSING.md).
  The resume button follows pending requests and the shared activity poll.

- End-of-import stderr renders typed processor statistics in aligned columns;
  machine-readable status remains JSON. See [processing](implementation/PROCESSING.md).

- Freeze all manifest-referenced acquisition adapters; validate local ingestion
  with `make test-frozen-source-plugins` before packaging handoff.
- Source ingestion uses owner-thread completion futures instead of a shared
  publication mutex; see [acquisition](implementation/ACQUISITION.md).
- The private archive adapter collects typed import and definition-update outcomes before serializing
  job state; see [desktop](implementation/DESKTOP.md).
- GUI preview polls collect typed future results and discard obsolete generations;
  see [desktop](implementation/DESKTOP.md).
- Original-mailbox trees unquote IMAP display components once, preserving filter
  tokens and local filenames; see [desktop](implementation/DESKTOP.md).
- Natural search preserves quoted symbols and works with existing indexes;
  see [search behavior](implementation/SEARCH.md).

- [Customizable Default Lists](CUSTOMIZABLE_DEFAULT_LISTS.md) provide typed bundled
  templates, personal overrides, revisioned archive snapshots and a shared editor.
  Provider-scoped reply-address rules retain attributed routing evidence without
  accepting identity merges. Owner defaults start empty and prompt during setup.

- Pinned SBook5 lexical data drives reversible person-view filtering, independently
  of linkage scores and manual decisions; see [processing](implementation/PROCESSING.md#sbook-entity-filter).

- Optional linkage engines share frozen candidates and private reports; see
  [comparison implementation](implementation/PROCESSING.md#optional-name-linkage-comparison).
  Stable review labels retain independent non-person flags and unlabeled notes.

- Identity picker navigation uses typed selections and read-only evidence scopes.
  Native Close marshals Cocoa focus lookup and closure to the main thread.
  Email-labelled group fallbacks retain their filters; institution projections
  exclude provider domains without deleting durable organization records.
  Typed signature-phone observations feed expandable name-row phone lists.
  See [desktop implementation](implementation/DESKTOP.md#identity-picker-search-navigation).

- GUI commands share a bounded tracked executor, with cancellation-aware import
  leases and future-based shutdown waits; see [desktop](implementation/DESKTOP.md).
- Track Cocoa archive-open callbacks with command futures; see [desktop](implementation/DESKTOP.md).
- Track Cocoa About-window commands with futures; see [desktop](implementation/DESKTOP.md).
- Track Cocoa Setup-window commands with futures; see [desktop](implementation/DESKTOP.md).
- Track startup export cleanup with an application future; see [desktop](implementation/DESKTOP.md).
- Confirmed Quit injects a shared modal shutdown overlay independently of its
  five-second watchdog; see [desktop shutdown](implementation/DESKTOP.md#cooperative-shutdown-and-writer-fencing).
- Desktop recovery summarizes saved attempts with a Continue Import action and records lease-protected aborts;
  warning history collapses to a reopenable pill. See
  [desktop recovery](implementation/DESKTOP.md#saved-import-recovery-and-notices).
  History source labels resolve account/server names without changing saved locators.
  History also exposes accepted startup and uses enlarged check/X/pause circles.

- Python scan futures return typed responses for coordinator-owned IPC;
  see [scanning](implementation/SCANNING.md).
- Python and Rust omit the URL-phishing database option;
  see [scanner configuration](implementation/SCANNING.md#embedded-scanner-configuration).
- Python scanner startup checks old definitions and reuses the validated one-shot
  updater when online; see [automatic refresh](implementation/SCANNING.md#automatic-definition-refresh).
- Recovery rescans unsuccessful saved verdicts and drains archived content before
  pending ingest in unbounded runs; see [processing recovery](implementation/PROCESSING.md#working-object-lifetime).
- Release cached raw mail after each MBOX publication and SQL commit, preserving
  live derived inputs and retry checkpoints;
  see [working-object lifetime](implementation/PROCESSING.md#working-object-lifetime).
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
