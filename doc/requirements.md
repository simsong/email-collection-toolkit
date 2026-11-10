<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Email Collection Toolkit requirements

These are the current behavior contracts. The linked subsystem files own the
details; this overview is an index, not a second feature changelog. A requirement
does not establish that every platform or release gate has passed.

| Subsystem | Scope |
| --- | --- |
| [Archive](requirements/ARCHIVE.md) | Canonical MBOX, hashes, deduplication, provenance and recovery |
| [Acquisition](requirements/ACQUISITION.md) | Read-only local/IMAP import, source support and importer boundaries |
| [Processing](requirements/PROCESSING.md) | Durable jobs, MIME extraction, identities and saved manual decisions |
| [Search](requirements/SEARCH.md) | Indexes, query semantics, dates and verified message display |
| [Desktop](requirements/DESKTOP.md) | Archive documents, windows, native UI and progress |
| [Scanning](requirements/SCANNING.md) | Embedded ClamAV, definitions, failure evidence and isolation |
| [Delivery](requirements/DELIVERY.md) | Installable commands, packaging, versions, signing and updates |
| [Validation](requirements/VALIDATION.md) | Fixtures, test gates, platform acceptance and corpus checks |

## Shared boundaries

- IMAP credential checks authenticate without mailbox access or archive writes;
  replacement passwords are tested before saving. Account acquisition uses
  a saved per-account limit (default 8, range 1–15) and cancellable capacity backoff while retaining the
  full processing worker pool. Interrupted sessions retry with fresh TLS and
  epoch checks, retaining delivered UID state; exhausted recovery pauses safely. Protocol failures and private diagnostics
  retain their distinct handling, including malformed greetings and full CLI login
  diagnostics. Expected configuration problems use actionable warnings without
  validator dumps or tracebacks. Retry classification precedes redaction; CLI backoff remains visible,
  console cancellation wakes discovery, and pending password validation blocks Escape. See [acquisition](requirements/ACQUISITION.md).

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
  Failure deadlines remain explicit; readiness must be observed.
  Unknown-size acquisition reports ETA unavailable. IMAP run details show
  message counts, IMAP INTERNALDATE UTC bounds and per-year coverage.
  Run history preserves the producing application's launch version, including failures;
  older snapshots without that evidence display the version as unavailable.
  Worker activity updates keep four table columns fixed; task/source expands
  into the remaining width. See [desktop](requirements/DESKTOP.md).
- Pending GUI searches show progress without claiming no matches before
  completion. See [search](requirements/SEARCH.md).
- User commands are `ect`, `ect-create`, `ect-import` and `ect-search`. They invoke
  application code directly. Make is the developer build/test convenience layer.
- Versions come from `pyproject.toml`; launch/build provenance discloses local
  changes. Release publication and installed-platform acceptance have separate gates.
- Dependency security fixes cover the application, converter, optional research
  and Rust locks; ZIP acquisition must reject contradictory member headers.

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
