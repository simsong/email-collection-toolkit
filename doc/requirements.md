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

- Freeze all manifest-referenced acquisition adapters; validate local ingestion
  with `make test-frozen-source-plugins` before packaging handoff.
- Natural search preserves quoted symbols and works with existing indexes;
  see [search behavior](requirements/SEARCH.md).
- Desktop recovery requires an informed continue/defer/abort choice, and collapsed
  warnings remain readable; see [desktop recovery](requirements/DESKTOP.md#saved-import-recovery-and-notices).

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
