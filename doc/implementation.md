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

- Natural search preserves quoted symbols and works with existing indexes;
  see [search behavior](implementation/SEARCH.md).
- Desktop recovery summarizes saved attempts and records lease-protected aborts;
  warning history collapses to a reopenable pill. See
  [desktop recovery](implementation/DESKTOP.md#saved-import-recovery-and-notices).

- Python and Rust omit the URL-phishing database option;
  see [scanner configuration](implementation/SCANNING.md#embedded-scanner-configuration).
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
