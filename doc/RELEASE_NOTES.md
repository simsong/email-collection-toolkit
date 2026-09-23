<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Release notes

## Unreleased

* Run an unsigned mounted-DMG smoke test on the macOS CI runner before release
  tagging. Both the mounted updater check and definition updates supply a
  temporary FreshClam configuration instead of requiring the host's config.
  Include and verify the matching ClamAV and OpenSSL license texts in the DMG.
  Report Apple's notarization issue log on a rejected submission without
  exposing API-key credentials. Gatekeeper now assesses the stapled DMG with
  the disk-image primary-signature context. The temporary API-key filename no
  longer embeds its identifier.

* Package ClamAV with its matching OpenSSL libraries so the mounted macOS app
  can load the antivirus engine. Validate that the bundled `freshclam` updater
  launches. Record mounted DMG contents and report native loader errors when
  release validation fails.

* Clarify GPLv2-only versus GPLv2-or-later linking compatibility with LGPLv3.
  The separate converter uses GPLv3 for project-owned code; upstream pypff/libpff
  remains LGPLv3-or-later and does not require relicensing.

* Reject or fold overlong generated libpff headers, retain per-item libpff
  retrieval failures in diagnostics, and preserve unresolved Exchange DNs in
  display-name angle addresses with their explicit `EX` marker.

* Make the Microsoft Rust adapter the sole PST importer. Remove the redundant
  PST-reader setting and document libpff as the separately configured OST reader.

* Use the shared `#item=<node-id>` PST provenance URI in Rust and libpff
  output. The libpff converter now omits incomplete items with diagnostics,
  matching the Rust importer rather than emitting a partial parent record.

* Align Rust and libpff PST reconstruction for primary headers and mboxrd
  separators. Both now support stable `--offset`/`--limit` item ranges,
  validate Message-IDs, preserve unknown-code-page bytes with a Windows-1252
  fallback, and avoid invalid base64-encoded multipart attachment containers.

* Use the PST message timestamp for the synthetic mboxrd `From pst-importer`
  delimiter. It now shares the reconstructed `Date:` timestamp, preferring a
  valid transport date and then MAPI submit/delivery times before the epoch.

* Preload the PST Contacts email directory across the full folder hierarchy,
  including all three named email slots and Exchange address-book EntryID aliases.
  Use explicit, unambiguous mappings for sender and recipient address repair;
  preserve original addresses and mapping evidence. Report incomplete scans.

* Expand Exchange sender identities using unambiguous SMTP mappings stored
  in the same PST. Preserve the original DN and mapping provenance, and retain
  native identities when mappings are missing or conflict.

* Preserve native Exchange sender identities with an explicit `EX` address-type
  marker instead of rejecting them as SMTP addresses. Decode PST subjects and
  emit readable UTF-8 headers; retain header syntax and injection checks.

* Include completed reconstructed header blocks in PST `--only-invalid`
  diagnostic output, retaining invalid header values for inspection.

* Support Windows-1256 PST bodies, retaining their bytes and declaring the
  correct MIME charset instead of rejecting code page 1256.

* Silently exclude PST meeting requests/responses as non-mail. Label reader
  failures as library errors. Add `pst-importer --only-invalid` (Makefile
  `ARGS=--only-invalid`) to inspect failed items' available headers/body evidence
  in diagnostic mboxrd records while retaining nonzero extraction status.

* Validate `PST` before building for `make pst-import` and `make pst-smoke`.
  Missing input now produces usage guidance instead of an empty-filename
  extraction error; invalid paths produce a readable-file diagnostic on stderr.

* Remove the Google authorization prototype and its dependencies. Remove Requests
  from the application runtime and replace tldextract with offline PSL matching.
* Always import PST/OST through external executables. Package the libpff converter
  independently under GPLv3, with source-preserving extraction, hard host timeouts,
  bounded diagnostics, and a separate Rust/libclamav scan stage before API admission.
  The main application remains GPLv2 and does not load libpff.

* Recover interrupted development PST corpus downloads using OS cache locks,
  prompt Ctrl+C cancellation, and verified reuse of completed downloads.
  Preserve older artifacts lacking receipts before retrying their download.

* Adopt GPL-2.0-only terms for all project-owned code, Rust tools, and the website
  theme, with additional licenses available. Preserve third-party license terms.
* Implement a direct-library antivirus prototype with concurrent scans,
  infected-only antivirus headers from Rust PST, explicit definition
  refresh, and About date/age reporting. Display an amber recommendation after
  three calendar months and adopt quarterly application releases. Python owns
  message hashes and deduplication; API producers need no scan receipts or database.
  `make freshclam` updates ignored `etc/clamdb/`, seeded from installed definitions.
  Release CI refreshes this copy and the DMG bundles it with the engine/updater.
  The license version now matches ClamAV. Public distribution still requires
  resolving the remaining Apache-2.0 ftfy compatibility issue recorded in
  THIRD_PARTY_NOTICES.md and completing native dependency notices/source coverage.
  See [status and remaining validation](EMBEDDED_CLAMAV.md).
* Preserve existing Cargo dependency versions when adding workspace dependencies
  through make rust-lock.

* Keep release CI headless and all workflow jobs on macOS; retain explicit local
  native-GUI release validation and verified Darwin website tooling.

* Expose stable processing job IDs and align API documentation with concrete
  provenance fields, cancellation methods and durable states.

* Report typed timeout/error counts and n/a for unobserved plugin timings; exclude
  unfinished invocations from duration statistics.

* Show all registered acquisition plugins and processor versions in About.

* Separate header-message counts from signature occurrences in identity queries
  and both live pickers, including filtered, deduplicated group totals.

* Retain typed scanner errors/unscannable outcomes and engine/signature versions
  with invocation history; block filing while preserving retryable input.

* Prevent malformed attached-message transfer encodings from becoming canonical
  child messages; retain the original parent with failed extraction diagnostics.

* Keep depth/text-limit failures visible as incomplete work; allow configured
  depth limits and resume after raising them without losing original bytes.

* Bound cumulative MIME split/decoded bytes, parts and child messages before
  publishing a nested tree. Make limits configurable under `plugins.mime` and
  preserve failed work for retry; stream quoted-printable decoding in chunks.

* Reconcile stale search, owner-rule, PST/OST, Apple Mail, processor GUI and
  packaging descriptions; distinguish manual identity data from disposable
  indexes, and mark historical planning inventories explicitly.

Earlier bullets record the implementation sequence; a historical “planned”
statement does not supersede a newer implementation entry above it.

* Add configurable MBOX rollover (`mbox_max_bytes` in archive `config.yaml`,
  default 3.75 GiB), counting physical framing bytes and preserving oversized
  messages whole. Validate with 20 KiB parts, restart and publication recovery.

* Implement a shared selector registry for parsing, SQL, and GUI completions.
  Match header and authoritative names before content processing, derive matching
  roles from one Any lookup, and support generic role/date tiles after three value
  characters. Use 50-hour worldwide date windows in CLI and GUI with ISO, slash,
  and month-name dates; document boundaries and overlapping daily results.

* Distinguish active ingest from background content work on quit. Stop content
  without an ingest warning, keep the event loop alive through checkpointing,
  and handle Ctrl-C through the orderly GUI shutdown path.

* Tighten matcher rows and darken text, remove the archive-identities badge,
  and label the resume action Continue Processing. Keep explicit search selectors
  such as `from:simsong` scoped to their requested field in autocomplete.

* Connect the desktop GUI to resumable processor work, saved ingest policy,
  archive-backed name/institution pickers and immediate manual edits. Show
  registered processors in About and attached-message tags and parent paths.
  Exercise the shipped pages against real archives in headless browser tests.

* Immediately acknowledge Ctrl-C during CLI ingestion before waiting for workers
  and archive cleanup, preserving the notice across dashboard redraws.

* Accept unsigned annotated release tags matching the project version. Remove
  the release-signing public-key variable and GitHub tag-signature checks while
  retaining commit consistency checks and optional Apple app/DMG signing.

* Add libpff OST extraction (now an external converter) with genuine cache-fixture coverage,
  source fixity, streamed attachments, and explicit partial-import diagnostics.
  This release also added a now-removed developer configuration option,
  **Redundant PST Import**, to run Rust and libpff through existing deduplication.
  Document CLI directory ingestion and search.

* Restrict pull-request CI to macOS, with on-demand Homebrew ClamAV and pinned
  macOS Zola binaries. Keep native GUI tests opt-in.

* Bound derived HTML/RTF/text processing; invalidate stale search rows atomically
  with processor generations; keep public providers out of automatic affiliations.
  Preserve PST timeout exit evidence and bound retained stdout/stderr prefixes.

* Retain positive antivirus messages in INFECTED even if metadata/date parsing
  fails; label unknown-date placeholders and block downstream processing.

* Run production CLI ingest, message and content processing through registered
  Python plugins in process. Add deferred/resumable content commands, MIME and
  HTML/RTF processing, signature evidence, identity queries/manual edits, and
  first-class attached messages with deduplication and durable parent/tag data.
  Integrate the external Rust PST importer, retaining partial-run evidence.
  No Python plugin worker subprocesses are used; plugin deadlines are cooperative
  with bounded I/O and late-result rejection.

* Preflight and journal configuration writes across a complete processor rank;
  recover interrupted publication before plugin reads. Record missing/unreadable
  inputs as failed jobs and reject invalid MIME tokens before plugin execution.
  Enforce inventory member size and SHA-256 for 7z as for ZIP archives.

* Add processor get_my_config/write_my_config APIs with recursive archive-over-
  installation settings, isolated plugin namespaces and atomic scoped writes.
  Discard staged writes on plugin failures, timeouts and aborted ranks.
* Synchronize verifier interruption tests with actual input consumption and
  release blocked I/O after SIGINT so Python can deliver the pending signal.

* Isolate the test-plugin framework schema from production catalog resources.
  Include catalog regression tests in the framework target and exercise real
  CLI cancellation/recovery without native windows.

* Add the headless API v2 processor framework, executable test plugins, resumable
  CLI jobs, rank/abort handling, plugin deadlines and invocation reports.
  A fresh framework schema includes person/organization, affiliation, evidence
  and tag relations. GUI integration remains subsequent
  stacked PRs; no generated-format compatibility migration is required.

* Document proposed ranked processor DAGs with a shared website graphic and
  track the future tag editor in issue #119. These are design documents, not a
  completed processing-pipeline refactor.

* Add experimental name and institution matcher windows with synthetic data,
  large disclosure triangles, mailbox/domain and date filters, sortable columns,
  drag-to-merge, and undo. `make matcher-prototype` opens both subclasses.
  Matcher buttons demonstrate predefined matches; real matching and database
  integration are not connected.

* The standalone verifier announces each file before processing, displays
  per-pass hashing and message progress bars, and supports `--quiet`/`-q`.
  Clarify the archive-root argument and script-directory default. Ctrl-C now
  reports incomplete verification and exits 130 without a traceback.
* Document proposed ranked processor DAGs with a shared website graphic and
  track the future tag editor in issue #119. These are design documents, not a
  completed processing-pipeline refactor.

* Add experimental name and institution matcher windows with synthetic data,
  large disclosure triangles, mailbox/domain and date filters, sortable columns,
  drag-to-merge, and undo. `make matcher-prototype` opens both subclasses.
  Matcher buttons demonstrate predefined matches; real matching and database
  integration are not connected.

* Keep local `make dmg`, `make dmg-signed`, and `make test-dmg` headless. Add
  `make check-release` for the visible GUI test of a new or existing (`DMG=path`)
  image, and require it in GitHub release assembly. Require the GUI self-test's
  background workers to exit before reporting success; fail with thread
  diagnostics after a bounded shutdown wait and announce the test windows.

* Add `make dmg-signed` to build with the first valid local Developer ID
  Application identity, with a `SIGNING_IDENTITY` override. Add
  `make list-signatures` to list available code-signing identities. Signed
  builds fail if no identity is selected; notarization remains separate.

* Add Rust `pst/pst-downloader.rs` and Makefile targets for inventory-driven PST,
  ZIP and 7z acquisition into ignored `var/pst/`, with source/member reports,
  SHA-256 verification, content deduplication, cache reuse and bounded extraction.

* Require `.mboxrd` for derived PDF output and use it for audit/source fixtures,
  ensuring byte-preserving re-import. Make Rust run targets select `.exe` on Windows.

* Add standalone Rust `pst-importer` using Microsoft's `outlook-pst` 1.2.0,
  with read-only source handles, source SHA-256 checks, deterministic MIME,
  body/attachment evidence, validated mboxrd stdout and nonzero partial-run status.
  Add actual PST/process tests and build/run Makefile targets. Public fixture
  qualification recovers 12 messages and 30 attachments while reporting two
  upstream attachment failures; CLI archive integration is now implemented, while
  native packaging remains planned.

* Implement Rust `mdti-validator` and `mcti-generator` for MCT Importer API 1.0,
  with `make rust-programs`, individual build targets, locked dependencies,
  Clippy/format checks and real-process stream tests. The validator discards
  stdin, reports errors and counts valid complete messages at EOF. The generator
  emits counted RFC/MIME text messages with mboxrd quoting and provenance.
  H2/h3 suffice; no h4 is introduced. Rust is required to build these tools and
  the PST helper.

* Correct canonical and derived MBOX writers to use reversible mboxrd quoting;
  Python's default writer uses mboxo. Decode declared `.mboxrd` sources once,
  preserve unknown-source quoting and retain hash-verified legacy recovery.
  Document the Library of Congress reference, the code audit, all hash purposes
  and added message headers. Existing archives are not rewritten.
* Specify filename-to-stdout-mboxrd ingest executables with separate URI, importer
  name and version headers, starting with a Rust PST adapter candidate and
  optional additional passes. All headers remain in h2; h3 already includes the
  body. PST import and full Windows ingest are beta requirements. The runner,
  cross-importer dedup policy, Windows installer and Snap remain
  planned. Document Microsoft sources for possible MIME reconstruction.

* Report missing project copyright notices as warnings so they do not fail CI
  or release builds; retain the copyright notice in the Makefile.

* Reconcile the copyright/license draft with current validation and release
  workflows. Preserve external artwork and generated workflow bytes, cover
  current project source files, and retain the ordered lint/type/test gates.
* Disable New Folder in macOS setup browsers to prevent source-tree writes before
  validation. Reserve a job-free Cancel/quit atomically against new imports;
  a competing import receives the normal stop confirmation and checkpoint wait.

* Keep native Close disabled globally throughout setup operations, including
  Cancel and modal dialogs that fall back to an existing search window; reject
  queued Close actions while setup is locked.

* Disable native Close while setup dialogs or import startup are pending, restore
  it on completion or error, and clear stale warnings from reused folder pickers.
  Add native Option sampling/reopen regression coverage.

* Reconcile startup setup with the current owner email editor and preserve
  Finder file dragging, launch-argument handling, and shared import safeguards.

* Add a three-step first-run setup window with repeatable source/archive folder
  selection, Cancel to quit, and Start import opening progress. Reopen setup with `--new` or
  Option held during macOS launch (also Option-click on the running Dock icon).
  Preserve remembered archives, reject overlapping folders, and keep About
  available from the application menu without showing it at startup.
* Normalize an immediate quoted MBOX delimiter into a literal `X-From:` header.
  Promote a meaningful inner envelope when the outer sender is `XXX` or
  `???@???`; retain the displaced outer value as `X-From:`. Preserve body
  quoting and record original source-payload hashes/framing as provenance. Fix
  `From XXX` wrapper detection misreading indented forwarded body text as a
  delimiter. The normalized archive is readable by ordinary RFC/MIME readers.
  Import failures now retain source-code tracebacks, validator origins, source
  references/cursors, message hashes, and bounded input previews in local run
  history, including both original framing previews. Bound source identity and
  cursor previews, exception messages/notes and total failure reports; reject
  malformed outer lines before header normalization. Double-framed Eudora
  metadata stubs remain excluded. Existing archives are not rewritten.
* Restrict automatic PKCS#12 import to
  GitHub-hosted runners and document process-argument visibility. Correct the
  certificate setup repository and distinguish deliberate unsigned builds from
  missing credentials. Existing local keychain identities remain supported.

* Build macOS DMGs in the GitHub release workflow. Sign the app and DMG when
  both optional PKCS#12 secrets are configured; otherwise warn in Actions and
  publish an explicitly named `_UNSIGNED.dmg`. Preserve failure on invalid
  configured credentials. Document secret setup and temporary keychain cleanup;
  signing does not yet include automatic notarization. Correct the native
  dependency audit to distinguish a library's own install name from an import.

* Use one file-drag path for message-list rows and the message-file icon,
  explicitly writing only `public.file-url` to copy `.eml` files (or a ZIP for
  multiple selected messages) to Finder/Desktop. Modifier-click selects several
  rows; dragging exports them instead of extending a selection range.
  Isolate prepared files from attachment exports, revoke tokens safely on close,
  wait for the result table to finish building before startup clears it, and
  discard queued clicks from a replaced result set.

* Fix source GUI startup treating the `mailsearch-gui` launcher and option
  values as archive paths. Preserve explicit archive selection and Finder opens;
  validate the actual launcher with the GUI self-test.

* Correct the macOS dependency audit to distinguish library install names from
  actual dependencies, avoiding a false failure for bundled pydantic-core.

* Fix false Sent classification from implicit owner-name substring matching.
  Every import now reviews owner include/exclude fields, with exclusions taking
  precedence and bare names matching only the exact mailbox name. Save defaults
  in archive `config.yaml` and regenerate `owner-names-detected.txt` separately.
  Existing archives need a fresh rebuild to correct historical classifications.
* Plan comparable Dioxus Desktop and Tauri trials before choosing the compiled UI;
  retain ingest, search, and archive preservation in Python. Prioritize Windows
  full ingest and defer Linux/snap delivery. This records the migration decision;
  the Dioxus frontend, worker protocol, and Windows installer are not implemented.
  The framework decision remains pending trial results.

* Add a clean-Windows development setup guide covering ARM64/x64 uv installation,
  x64 Python on ARM VMs, and the remaining full-ingest and packaging work.
  The procedure has not yet been executed in a clean Windows VM; this is not
  a Windows support announcement.

* Preserve original MBOX `From ` delimiters through import. When absent,
  synthesize a delimiter from the latest valid header timestamp instead of
  import time, with deterministic documented fallbacks. Existing archives are
  not automatically repaired.


* Complete the Email Collection Toolkit display-name and repository-link rename,
  preserving access to existing preferences and OAuth configuration. Add the
  Searching guide, refreshed website navigation, real interface screenshots,
  and credited Wikimedia keyboard clipart.

* Introduce the Email Collection Toolkit website identity and stacked-envelope
  application icons; preserve Gmail setup and Advanced navigation, responsive
  access, and equal personal and archival use cases. Validate Zola TOML before
  builds and report read/decode failures without a traceback.
- Add an experimental read-only ePADD address-book exporter in
  `dev/addressbook-exporter.py`, with explicit owner addresses, separate
  address-level contacts, private output, and exclusion/hash reporting.
  It avoids address-shaped display-name aliases; live ePADD repair remains
  unvalidated.

- Audit all query selectors with production-SQL EXPLAIN and execution-budget tests.
  Fix Any-address searches, sender counts, date filters under alternate sorts,
  attachment-inclusive counts, mailbox filtering, and subject candidate scans
  to use their filtering indexes before result ordering.

- Show invalid search syntax inline instead of raising a pywebview exception.
- Use the existing recipient address index for To/Cc/Bcc substring searches,
  avoiding per-message recipient probes and forced full-catalog sort scans.

* Inspect Apple Mail provider metadata using private database/WAL copies, avoiding
  source shared-memory writes. Abort on scanner helper execution errors before
  removing a daemon socket or launching a replacement.

* Reject empty-domain contacts and report zero-byte partial EMLX files instead
  of silently skipping them; direct selection of partial records still fails.

* Reject duplicate deferred AI request IDs and reserve h3 review destinations
  exclusively so a late-created empty directory is not overwritten.

* Explicitly attach h3 review catalogs read-only, stage name-evidence summaries
  before publication, and roll back database publication if the summary link
  fails. Restore the CSS-injection regression's intended validation path.

* Treat missing, non-executable, and invalid-format ClamAV health-check helpers
  as unavailable instead of allowing OS execution errors to escape the probe.

* Validate all contact-filter regexes at policy load time and report YAML or
  regex typos as path-qualified CLI errors, without tracebacks or archive writes.

* Use platform-correct read-only catalog URIs for Contacts. The standalone
  message scrolling regression now guarantees overflow and verifies actual
  scrolling to source locations, independent of platform font metrics.

* Distinguish bogus-domain contact-filter diagnostics from bogus local parts
  without changing which addresses are excluded.

* Use installed package metadata for BagIt writer versions, verify release tags
  before project installation/artifact execution, and keep all mobile navigation
  links visible regardless of their order.

* Use `gh` with the authorized review-request-only identity for Copilot requests
  instead of controlling the browser.

* Resolve committed cleanup-conflict markers and restore generated skill
  wrappers. Require pr-to-ready to integrate stranded task work before handoff
  and perform verified post-merge checkout cleanup. Retain dirty, unmerged,
  and private evidence-bearing worktrees until their disposition is settled.

* Require pr-to-ready to disclose potential conflicts with active work and obtain
  user approval for the coordination plan before proceeding. Add explicit
  handoff cleanup with verified publication, artifact preservation, retained
  unmerged branch refs, and removal of retired skill-distribution entries.

* Limit Ruff discovery to tracked and non-ignored new Python files, avoiding
  generated directories while supporting project-local linked worktrees.

* Clarify installed verifier usage and make GUI asset HEAD/redirect response
  lengths explicit without reading asset bodies for HEAD.

- Standardize the agent PR workflow as `pr-to-ready`, retain
  `codex-to-complete` and `codex-to-ready` aliases, and add shared
  Codex, Claude, and Copilot implementer/reviewer instructions.


* Add the desktop document controller, archive writer leases, document options,
  coordinated import/quit handling, and macOS application packaging.
* Add the Gmail authorization developer preview and read-only Apple Mail cache
  comparison. Direct Gmail and Microsoft 365 ingestion remain unavailable.

* Tighten plug-in method contracts, native-window failure handling, and integrity
  version validation while adding complete ty and Pyright coverage.

* Require Ruff and Pylint, followed by ty and Pyright, before tests in
  `make check`, with locked development dependencies and ordered stages.

* Document the browser-driven pr-to-ready workflow, ten-minute review
  checks, signed Codex identity, and explicit human handoff for review loops.

* Make `refresh-index` observable and safe to interrupt: it now reports
  message-weighted verification/indexing progress bars with ETA, announces its
  Ctrl-C safety before work begins, and discards an incomplete replacement
  on **Ctrl-C**, retaining the existing search index. Its bounded all-core
  default (or two workers if core detection is unavailable) now parallelizes
  verified MBOX reads, SHA-256 checks, and MIME parsing
  while one ordered SQLite writer builds the replacement safely.
* Stop opening message links immediately. The message viewer now shows an
  allowed link destination in the bottom status bar on hover and requires an
  explicit **Open Link**, **Copy Link**, or **Ignore** choice on click.
* Make graphical searches complete across the full archival time span instead
  of favoring the newest 10,000 catalog rows. Probe at most 2,001 matches,
  display all sets up to 2,000, and automatically load larger remainders with
  a red background-search status. Remove manual result paging and show the
  complete search language with examples at startup and after an empty query.
* Add the rainbow-envelope identity to the Python GUI and create the Zola
  `envelope-rainbow` GitHub Pages site. The site links to project documents,
  curation resources, discussions, and tag-derived stable/beta release data.
  Add a pinned draft-release workflow modeled on bulk_extractor's signed-tag,
  source-artifact, checksum, and draft-publication flow.
* Add an OCR-engine-independent standalone printed-email PDF extractor. It
  streams page-addressable native text through Poppler, accounts for message
  and non-message pages, records PDF/page/policy and handwriting provenance,
  and atomically writes a standard derived MBOX without modifying the source.
  A focused real-PDF pytest compares the six-page SIPBADMIN fixture with its
  human-reviewed MBOX ground truth.
* Persist each ingest's typed progress and final statistics in its own
  atomically updated `status/ingest-*.json` operational BagIt tag file. Add a
  bottom GUI status line plus a singleton **Windows → Ingest** history window
  that displays every retained run and all configured worker threads without a
  catalog schema change.
* Preserve the read-only data-quality investigation as maintained scripts and
  documentation. Generated message exports and metadata evidence remain private
  ignored artifacts and are not retained in the repository.
* Treat complete `cur`/`new`/`tmp` Maildir structures and Apple Mail `.mbox`
  package chains as logical mailboxes while retaining every physical message
  file as provenance. Within Maildir, a structural single-message match yields
  to exactly one content parser, fixing envelope-prefixed messages that also
  match MBOX without using priority to hide genuine parser ambiguity. Mark
  Apple `[Gmail].mbox` observations as local Gmail caches and prefer a retained
  direct-provider observation in provenance displays when both exist.
* Rank address completions by deduplicated message count and then by the most
  recent matching message. Retain per-message suggestion dates so replacement
  and index maintenance recalculate recency correctly, while preserving the
  three-character threshold, 120 ms debounce, 20-result limit, and stale-query
  suppression in the graphical interface.
* Add read-only ingest for extensionless Emacs RMAIL Babyl files. Detection is
  based on the case-insensitive `BABYL OPTIONS:` header, and the streaming
  reader supports both LF and CRLF containers, falls back to visible headers
  when a record has no original-header block, and excludes Babyl labels and
  redundant visible-header blocks from reconstructed RFC 5322 messages.
* Compare `Date:` with a UTC-normalized, outlier-trimmed median of all valid
  `Received:` timestamps. Differences greater than two days use the computed
  date, store `received-median` in the catalog, and show a warning banner plus
  a subtle red message background in the graphical viewer. Add
  `--earliest-year` so an archive can reject earlier epoch-like header dates
  and use the same source/stream/path fallbacks; the default remains 1900.
* Add API-v1, manifest-discovered source and physical-file plug-ins. The loader
  validates every packaged and explicitly trusted `--plugin-dir` manifest
  before importing external code, rejects duplicate or ambiguous ownership,
  orders deterministically, and freezes both registries before inventory. The
  local source yields typed containers, delegates to MBOX, Babyl, EMLX, EML,
  Maildir, or external file generators, and returns typed mail objects without
  owning threads or status output. A timezone-aware source timestamp can retain
  undated provider mail with a documented `source-fallback` catalog tag. Gmail,
  IMAP, O365, Microsoft Exchange, and NUL-delimited stdin are explicit
  unavailable source stubs.
* Move local complete-file and MBOX-prefix SHA-256 behind source integrity
  controls. The framework persists append-only typed decisions and evidence;
  only completed checks can drive a later skip or resume. Provider version
  tokens and cursors are represented without being mislabeled as hashes. A
  separate archive-integrity adapter owns BagIt/Mailbag and `h1`/`h2`/`h3`
  initialization, checkpointing, and verification.
* Print every unrecognized input filename and reason once, and separately
  report unchanged containers skipped by source integrity. Add source-neutral
  cursors, provider containers, per-source concurrency limits, and a temporary
  deduplicated discovery snapshot. Stable inventories are verified before
  ClamAV; live providers are captured once; concurrency keys are fairly
  interleaved. Preserve per-container hierarchy/provenance, nullable numeric
  cursor projections, provider phases, and unknown-byte progress. Acceptance
  coverage runs a directory-loaded multi-account provider through common
  workers, status, ClamAV, catalog, hierarchy display, and canonical publication.
* Replace the unreleased development V1 catalog layout with source plug-in,
  work-ID, opaque-cursor, and typed source-integrity tables. Existing
  development archives using the earlier V1 layout are intentionally rejected
  and must be re-imported into a new archive directory.
* Suppress exact empty Eudora MBCP metadata stubs with a retained
  `source-metadata-excluded` observation, and unwrap narrowly recognized
  `From XXX` status containers so the nested RFC 5322 message supplies its
  actual sender and metadata.

### Checkout reconciliation — 2026-09-08

Recovered work from historical development checkouts:

- Read-only human-contact reports and archive-local filtering policies.
- Provider-stratified Apple Mail comparison and private, hash-verified h3 review exports.
- Experimental name/signature evidence extraction, kept separate from production matching.
- Bounded ClamAV subprocesses and confirmation for active, unknown, or mismatched attachments.
- Directory imports report partial EMLX records and continue with complete records.
- Wheel/sdist installation checks, website-build CI, and retained browser failure traces.

Contacts/geography GUI, live IMAP sources, and Refresh/Rebuild are documented
plans, not newly implemented features. No source mailbox or real archive is
changed by reconciliation or its fixture tests.
