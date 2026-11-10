<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Email Collection Toolkit implementation

The linked subsystem files map current behavior to source, recovery boundaries
and validation. They own implementation details; this overview is an index rather
than a second change log. Platform acceptance is stated alongside its evidence.

| Subsystem | Scope |
| --- | --- |
| [Archive](implementation/ARCHIVE.md) | Canonical MBOX, hashes, deduplication, provenance and recovery |
| [Acquisition](implementation/ACQUISITION.md) | Read-only local/IMAP import, source support and importer boundaries |
| [Processing](implementation/PROCESSING.md) | Durable jobs, MIME extraction, identities and saved manual decisions |
| [Search](implementation/SEARCH.md) | Indexes, query semantics, dates and verified message display |
| [Desktop](implementation/DESKTOP.md) | Archive documents, windows, native UI and progress |
| [Scanning](implementation/SCANNING.md) | Embedded ClamAV, definitions, failure evidence and isolation |
| [Delivery](implementation/DELIVERY.md) | Installable commands, packaging, versions, signing and updates |
| [Validation](implementation/VALIDATION.md) | Fixtures, test gates, platform acceptance and corpus checks |

## Shared boundaries

- IMAP credential checks authenticate without mailbox access or archive writes;
  replacement passwords are tested before saving. Account acquisition uses
  a saved per-account limit (default 8, range 1–15) and cancellable capacity backoff while retaining the
  full processing worker pool. Interrupted sessions retry with fresh TLS and
  epoch checks, retaining delivered UID state; exhausted recovery pauses safely. Protocol failures and private diagnostics
  retain their distinct handling, including malformed greetings and full CLI login
  diagnostics. Expected configuration problems use actionable warnings without
  validator dumps or tracebacks. Retry classification precedes redaction; CLI backoff remains visible,
  console cancellation wakes discovery, and pending password validation blocks Escape. See [acquisition](implementation/ACQUISITION.md).

- Source files and accounts are read-only. Preserve message bytes except the
  documented IMAP CRLF-to-LF acquisition policy, with original wire provenance.
- Standard MBOX and SHA-256 manifests are the canonical preservation record.
  Search indexes are rebuildable; manual decisions, configuration and history
  require backups of the whole archive.
- Canonical writes use the writer lease and recoverable publication protocol.
  Interrupted work retains completed checkpoints; errors do not silently drop mail.
- ClamAV runs in owned workers. Detections and scanner failures retain messages
  and evidence; no resident service or manual-review gate is introduced.
- Jobs, windows and import progress follow actual work and change notifications.
  Source workers and cooperative saved-processing dispatch share an event-driven
  owner loop; invocations run in a separate bounded pool and commits stay serialized.
  Immutable working objects are reused and cleanup checks their digest before
  unlinking. Combined status coalesces saved-queue reads and refreshes the count
  when the last source finishes.
  Failure deadlines remain explicit; readiness must be observed.
  The pinned SDK's API replies are posted to the native view without a second
  synchronous acknowledgement; Ingests bounds its own reply waits.
  The estimator tracks unfinished unknown-size containers separately from known
  source bytes. Promoted attachments increment archival counts without affecting
  source totals or dates. IMAP run details show
  message counts, IMAP INTERNALDATE UTC bounds and per-year coverage.
  Ingest snapshots record the shared immutable launch version. Ingests displays that
  saved value in history and run details, with an unavailable fallback for older files.
  The worker table uses fixed column widths, with task/source taking the remaining
  space. See [desktop](implementation/DESKTOP.md).
- The GUI omits the empty-table placeholder while its status line reports search
  progress and final counts. See [search](implementation/SEARCH.md).
- User commands are `ect`, `ect-create`, `ect-import` and `ect-search`. They invoke
  application code directly. Make is the developer build/test convenience layer.
- Versions come from `pyproject.toml`; launch/build provenance discloses local
  changes. Release publication and installed-platform acceptance have separate gates.
- `make security-lock` refreshes alerted dependencies across the application,
  converter, research and Rust locks; downloader fixtures check ZIP header rejection.

## Documentation and evidence

[The user manual](USER_MANUAL.md) explains workflows; [the command-line guide](CLI_REFERENCE.md)
owns installed command syntax. [Developer commands](DEVELOPER_COMMANDS.md) covers
builds, tests and release recipes. Unimplemented work belongs to [plans](plans/README.md);
dated investigations belong to [history](history/README.md).

Change the owning subsystem requirement and implementation together. Validate
through the real authorized execution path and report the tested revision,
platform and remaining limits. [The toolkit audit](TOOLKIT_AUDIT.md),
[threading review](THREADING.md) and [timing audit](TIMING_AUDIT.md) retain their
review scopes and evidence; they are not blanket acceptance claims.
