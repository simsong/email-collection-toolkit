<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Acquisition implementation

Current implementation and validation boundaries. Deferred feature designs
live in [plans](../plans/README.md); historical evidence does not validate this revision.

## Provider implementation boundaries

The Google authorization prototype, mailarchiver-auth entry point, client
registration workflow, and its Google/Requests/keyring/DNS dependencies were
removed. The standalone IMAP CLI reintroduces keyring solely for native password
storage and adds Gmail app-password acquisition alongside Takeout and complete
local Apple Mail cache files. Direct Gmail API/OAuth remains future work. Historical registration
illustrations are retained as assets but are no longer an executable workflow.

`public_suffix.py` replaces tldextract with an offline ICANN rule reader using
its previous unchanged PSL snapshot (2025-04-07, MPL-2.0). It applies wildcard,
exception, longest-suffix and IDNA rules without Requests or network access.

`doc/M365.md` likewise separates the unsupported end-user boundary from the
developer design. It records Outlook PST and legacy-Mac OLM as the nearest
offline export paths, generic OAuth IMAP as the first planned live source,
Graph delegated `Mail.Read` as a later provider-specific path, and Entra
public-client and publisher-verification constraints.
`doc/APPLE_MAIL_CACHE.md` records the best-effort cache boundary and the
read-only preflight required before completeness claims.

Gmail API, O365, Microsoft Exchange, and NUL-delimited standard input have
manifest-loaded reserved source plug-ins. They recognize only their explicit
source forms and raise a clear unavailable error. The generic provider pipeline
already accepts virtual containers, source-native cursors, provider integrity
evidence, per-account concurrency keys, and raw RFC 5322 messages. The reserved
adapters remain unavailable until each implements actual account/stream access
with substantive provider-local acceptance coverage.

## Local adapter contract

PST/OST adapters are implemented with the limits documented in PST_IMPORTER.md.
Eudora and general working-cache adapters remain planned.
PST/OST, Eudora, and working IMAP caches are local read-only adapters, not
remote-source modes. Each adapter produces a typed source record containing
the available RFC 5322 bytes, source-native identity and folder context,
completeness state, and extraction provenance. A proprietary-store parser or
converter is isolated behind that interface and its name and version are
stored with every run. Acceptance fixtures include corrupt and partial stores
so item-accounting and error reporting are tested, not merely successful
conversion. Candidate third-party components must be evaluated for byte
fidelity, maintained format coverage, licensing, streaming behavior, and
repeatable output before selection.

### Standalone printed-email PDF extractor

`pdf_mail.py` provides the first OCR-independent implementation slice for
standalone scans of printed email. The Makefile `extract-pdf-mail` target
validates PDF magic, streams a complete SHA-256, and invokes Poppler
`pdftotext -layout` without rewriting the source. Form-feed-delimited page text
is consumed incrementally. A conservative page classifier requires a leading
header block containing Date, Subject, and at least one of From or To. Every
page is retained in the typed result as `printed-email` or `non-message`.
The public `segment_pdf_mail()` boundary accepts typed page text plus its
extraction-policy identifier, so another OCR engine can supply the same
segmentation and MBOX path without changing message interpretation logic.

The current segmentation policy emits at most one provisional message per
qualifying page. This deliberately supports the reviewed `sipbadmin.pdf`
acceptance fixture before implementing messages that span pages or share a
page. Each typed record retains its unmodified extracted page text, observed
headers, body interpretation, source page, subject, and an explicitly supplied
handwriting flag. The flag is metadata only; handwriting is not transcribed or
indexed.

`write_pdf_mbox()` refuses to replace an existing output, writes through a
same-directory temporary file, and atomically installs standard MBOX. Each
record has a deterministic synthetic Message-ID, PDF SHA-256 and page range,
extraction and segmentation policy, `machine-unreviewed` status, handwriting
status, selected observed headers, and an unmodified observed Message-ID in a
separate provenance header. The source PDF remains unchanged. Archive copying
to `data/pdf/`, routing to `data/pdf-mbox/`, search indexing, duplicate
relations, and viewer page navigation remain the next integration layer.

## Current acceptance implementation

The first implementation supports recursive local MBOX, Emacs RMAIL Babyl,
`.eml`, Maildir, and `.emlx` ingest, owner-rule Sent classification, exact
`(Message-ID, SHA-256)` deduplication, autosave and source-metadata exclusion,
`Date:`/`Received:`/source/previous-message/path-year date resolution,
embedded libclamav scanning in a run-owned worker process,
`data/mbox/INFECTED1.mbox`, SQLite catalog/FTS files, native BagIt/Mailbag
metadata, versioned `integrity/*.mbox.integrity` tags,
per-run observation review, and year/correspondent reports.  The top-level
`MAIL_ARCHIVE_DIR` selects the archive for every command by default; the
`--archive` option overrides it. `ingest` takes one
or more source roots as positional arguments and `--owner-names-file` selects
a legacy include-rule list; otherwise archive `config.yaml` supplies owner rules. Ingest requires `--clamav` or explicit `--no-scan`.
With `--clamav`, it starts
a temporary scanner worker that loads libclamav and the selected definitions,
and stops it when the run ends; it never enables persistent or on-access scanning. Workers enqueue typed phase/path/offset updates; the main
thread drains them and redraws the stderr scoreboard every 250 milliseconds.
The same main-thread snapshot is atomically published to a unique
`status/ingest-*.json` operational tag file. It is updated in place for that
run and finalized with completion state, failure detail, aggregate statistics,
and per-worker file/message totals. Later ingests create new files, preserving
the history without changing the SQLite schema.
Before starting workers, source plug-ins make a lightweight read-only discovery
pass which counts every schedulable container and its available byte estimate
without hashing or retaining message contents. The framework spools typed
container metadata to a temporary SQLite work snapshot, deduplicates scoped
container identities, and verifies a second discovery only for plug-ins that
declare stable inventory. Live providers are discovered once. The local plug-in also emits every
unrecognized regular filename and reason; the framework prints each once. It
silently discards zero-length files and paths matching the case-insensitive
globs in packaged `local_source_rules.yaml` before file-parser recognition.
PyYAML loads that versioned file once into immutable Pydantic models; unknown
keys and nonpositive probe limits fail validation. The
main thread then starts or validates ClamAV and waits for its
health probe before creating the mailfile worker pool. This gives the scoreboard
a stable overall byte and file percentage and ETA; the terminal highlights that
aggregate line in white on blue, while
redirected output reports the same fields without terminal controls. Each
configured worker has a stable numbered row, following the bulk_extractor
status model, and worker threads never print directly. Lines are truncated from
the left of long paths to the current terminal width before a dynamic cursor
rewind, preventing wrapped paths from accumulating old headings. The title
reports the main-thread `waiting for ClamAV startup` preflight and derives
`ingesting` from worker messages. It
shows active and peak concurrency, sanitized plug-in phases, per-worker
checking/ingesting/scanning/publishing/checkpointing/idle state, streaming
source byte or provider-message progress, and completion percentage, and reports processed/total source-file plus
archived/previously-seen/autosave/source-metadata/infected, unrecognized-file,
and unchanged-container counts. Control-C prints and flushes
`**Interrupted. Shutting down…**` before worker-pool shutdown waits; the outer
ingest handler also announces interruptions during preflight. The reporter emits
the notice once and starts subsequent dashboard frames below it. Real-SIGINT
tests hold a worker blocked until the acknowledgement is observed.
Control-C commits completed work, closes the
temporary scanner, publishes a BagIt/Mailbag checkpoint, reports a controlled interruption, and
prints the partial-run archive report before returning 130.  An `ENOSPC` append is truncated back to the prior MBOX size where
possible and reports a controlled nonzero stop.  Acceptance coverage includes
the checked-in MBOX/Babyl/EMLX corpus, source checkpoints, append resumption,
malformed metadata, publication recovery, and disposable-index failure.
Hash-verified MBOX retrieval considers both the stored payload and alternatives
without one writer-added terminal newline, preserving a non-empty source
message that lacked a terminal newline. Candidate interpretations are
deduplicated before hashing. Mailbag CSV metadata unfolds folded `Message-ID`
values before RFC 4180 writing so a source header cannot introduce bare LF into
an otherwise CRLF tag file.
After metadata discovery, `MailContainer` objects stream from the temporary
snapshot into an ingest pool bounded by `--workers`; discovery does not pre-hash
or retain file contents. Snapshot ordering interleaves concurrency keys, and
the framework enforces each plug-in's per-key limit. Each pool task owns one container through source-integrity planning,
streaming parse and scan, and checkpoint. A never-seen local file is
ingested before its complete fingerprint is calculated; that fingerprint is
still required before its checkpoint is committed. ClamAV readiness is a scanned-ingest
precondition; explicit `--no-scan` bypasses it and records that choice.
Modern Apple Mail package traversal recognizes complete
`Data/.../Messages/*.emlx` payloads and ignores MailData, plist, and detached
attachment files. It reports missing paths and macOS Full Disk Access failures
instead of treating them as empty input. Directory discovery reports and skips
`.partial.emlx`, including zero-byte records excluded from the generic empty-file
shortcut; direct selection is rejected because
the cached RFC 5322 representation omits detached attachment bytes; use Apple
Mail's mailbox export to obtain a complete MBOX source. Physical `.emlx` paths
remain source identities and integrity boundaries. Their catalog hierarchy
ends at the deepest containing `.mbox` package, strips `.mbox` suffixes, and
therefore omits Apple's internal UUID, `Data`, bucket, `Messages`, and message
filename components while retaining the account path. A `[Gmail].mbox` chain
also stores a typed cache relationship to the Gmail source kind and retains
the Apple account UUID as a non-authoritative account hint.
This same EMLX path is the interim local-file bridge for Gmail, Microsoft 365,
and ordinary IMAP accounts synchronized by Apple Mail; it makes no provider
completeness claim. Reingest uses the ordinary source controls and message
identity, so newly completed EMLX records can be added without duplicating an
unchanged canonical message.

`apple_mail_compare.py` performs read-only cache/archive reconciliation. It
walks only complete nonsymlink `.emlx` files, extracts their declared RFC 5322
payloads, and stores relative paths plus raw and semantic SHA-256 values in a
temporary SQLite database. It attaches `archive.sqlite3` by a read-only URI and
uses indexed `messages.sha256` and `observations.semantic_sha256` lookups to
classify exact, semantic-only, cache-only, archive-only, and ambiguous matches.
Apple mailbox URL schemes and Gmail special-folder structure produce
privacy-preserving Gmail, Exchange/EWS, other IMAP, POP, local, and unknown
aggregates without exposing account identifiers.
For semantic-only pairs it retrieves hash-verified canonical MBOX bytes and
compares DKIM-relaxed header multisets. Its report contains only header names
and aggregate counts, never values or content. It snapshots the active Apple
Envelope Index WAL metadata before and after the scan to flag a live cache
change. Provider metadata is queried only from a private byte copy of the
Envelope Index plus existing WAL/rollback journal, never by opening the source
with SQLite. Source shared-memory files are not copied; SQLite reconstructs them
privately. File identity, size, modification and change times must remain stable
across copying, otherwise the operation asks the user to quit Mail and retry.
This is a checked quiet-copy interval, not a live SQLite transaction snapshot.
`make compare-apple-mail` supplies the standard paths and
`make test-apple-mail-compare` exercises exact, semantic, formatting-only,
header-added, cache-only, archive-only, and partial-record behavior.
`h3_review.py` uses the same disposable index to select high-multiplicity h3
classes deterministically and exports all matching cache occurrences and
hash-verified canonical variants. It refuses to overwrite its destination,
writes owner-only private EML and manifest files, and is invoked by
`make h3-ambiguous-review`. Its synthetic acceptance test verifies complete
variant export, exact bytes, service classification, manifests, and overwrite
refusal.
Emacs RMAIL files are detected by their case-insensitive `BABYL OPTIONS:`
header because they commonly have no extension. The reader accepts LF and CRLF
container line endings, streams records without modifying the source, combines
the original-header block with the body, and excludes Babyl labels and the
duplicated visible-header block. A record with an empty original-header block
uses its visible headers instead. Babyl files are fully reprocessed if they
change; MBOX-only append-boundary resume is not applied to them. A `0x1f` end
marker before the first record returns an empty stream, while EOF without a
record or end marker raises a truncation error.

Source discovery has two independent, manifest-loaded registries.
`SourcePlugin.discover()` yields source-neutral containers, progress, and skip
events; `SourcePlugin.messages()` yields source-neutral `MailObject` values.
An optional `MailObject.source_date_utc` is a message-specific fallback for
providers whose RFC 5322 bytes have no usable `Date:` or `Received:` timestamp.
The strict model rejects naive datetimes and normalizes aware values to UTC.
The production `file-folder` source performs sorted read-only traversal and
delegates each container to the selected file generator. Packaged file plug-ins
implement EMLX, Babyl, MBOX, and single-message EML/Maildir. The IMAP adapter is
described under Standalone IMAP CLI below. Gmail API, O365,
Microsoft Exchange, and NUL-delimited stdin are reserved source stubs and fail
without accessing those systems. A direct `cur` or `new` child is a Maildir
message only when their parent also contains the standard `cur`, `new`, and
`tmp` directories. Its physical path remains the container identity, while the
Maildir root is stored as the logical hierarchy. If multiple packaged file
generators recognize one file, their manifest priority selects EMLX, then
Babyl, then MBOX, then a single message. Thus an envelope-prefixed Maildir
message uses the MBOX parser without becoming a separate mailbox.
A Maildir coincident with its mounted volume uses the mount directory name, or
`Maildir` at an unnamed filesystem root, instead of an empty hierarchy. Any
competing match involving an external file plug-in remains a fatal preflight
ambiguity. Each ignored path glob and its rationale are maintained beside the
file-probe byte limit and MBOX preamble-line limit in the YAML file, rather than
being duplicated in Python.

The loader scans only packaged and repeatable `--plugin-dir` roots, validates
every `plugin.toml` before importing external code, sorts by priority and kind,
loads file plug-ins before sources, and freezes both registries before
inventory. External category, plug-in, and entrypoint paths must resolve within
the explicitly trusted root. Boundary models are strict, so a text value cannot
be silently encoded as `MailObject.raw`.
The source-neutral provider path is acceptance-tested with a directory-loaded
virtual source, opaque cursor, version-token integrity control, common workers,
ClamAV, publication, and unchanged rerun.

Every source supplies `SourceIntegrityControls`. The framework records each
read/skip/resume decision and its typed evidence before consuming messages and
marks the check complete only after the control validates completion. The
completion generator runs outside the publisher lock and its progress events
are forwarded as yielded; after the final evidence is validated, only its
catalog persistence and completion mark are committed under that lock. The
local control owns complete-file/prefix SHA-256 and MBOX-boundary logic; a
failed check cannot supersede the last completed evidence. The framework's
separate message-transfer SHA-256 bridges each source observation to canonical
mail. `MailbagArchiveIntegrityControls` owns archive initialization,
checkpointing, and independent verification; source plug-ins cannot replace
the canonical BagIt, Mailbag, or `h1`/`h2`/`h3` controls. See
[PLUGINS.md](../PLUGINS.md) and [INTEGRITY_CONTROLS.md](../INTEGRITY_CONTROLS.md).

Successful ingests also print the archive report after finalization.  The
report shows per-year totals plus the top 10 senders and recipients by default;
all sections use aligned tables with right-aligned, comma-grouped numbers, and
the correspondent tables show each address's first and last message dates.
Addresses identified by a `Sent` message are filtered from correspondent lists
only, not from stored metadata or yearly people totals.  `report --top 0`
suppresses those lists.

`ingest --workers N` defaults to `min(os.cpu_count(), 8)` and means at most `N`
source containers in flight. Each framework worker invokes source controls,
streams plug-in records, parses, and sends
one ClamAV request at a time, allowing independent mailfiles to use concurrent
scanner clients. A single publisher lock serializes duplicate admission,
observations, SQLite transactions, publication-journal updates, MBOX appends,
and FTS writes; it does not surround source-integrity generator execution.
One plug-in instance is shared across workers and must be reentrant. A discovery failure or stable-inventory mismatch occurs before
scanner initialization and message publication; unstable providers use their single captured
worklist. The parser rejects nonpositive worker counts before starting an ingest. Native definition compilation must succeed before a host scan is submitted.

Date sorting/repacking, integrated verification, richer
text extraction, Eudora, working IMAP cache directories, direct Gmail API/OAuth,
redaction, and research-oriented metadata remain planned work. The standalone
password-based IMAP CLI is described below. The delivered
`mailsearch` command reads both databases without writing:
it applies `to:`/`from:`/`subject:` catalog filters, worldwide calendar-date
`date:`/`before:`/`after:` filters, and ANDed FTS5 terms; it prints stable
`message_pk` header lines and reads a numbered message directly from its
catalogued MBOX byte location, validating its SHA-256 before output.

## Ingest pipeline

An ingest run executes these steps:

1. Load and freeze the manifest registries, select exactly one source plug-in
   for each source specification, and capture the `MailContainer` generators in
   a temporary SQLite snapshot. Print each `SkippedInput` path and reason once,
   deduplicate identical scoped work IDs, and fail on conflicting definitions.
   Re-run and compare discovery only for sources declaring stable inventory;
   finish this preflight before ClamAV or publication. Fairly order captured
   concurrency keys, then stream the snapshot into at most `--workers` tasks.
2. Ask the source's integrity controls to emit typed evidence and one
   read/skip/resume decision. Persist the attempt before consuming messages.
   The local control ingests a never-seen file before calculating its complete
   SHA-256, fingerprints known files to skip a complete match, and resumes a
   grown MBOX only after a matching prefix and validated message boundary.
   Provider controls may instead use immutable IDs, version tokens, and opaque
   cursors. Reject resume from a non-resumable plug-in. Only
   completion-validated container evidence becomes a checkpoint. The generic
   API v1 container contract has no per-message provider commit; the IMAP
   integration adds transaction-coupled per-message state described below.
3. Stream the RFC 5322 representation from `SourcePlugin.messages()`. An `.emlx`
   adapter reads the decimal length prefix, then exactly that many bytes. A
   Babyl adapter reconstructs the message from the record's original headers
   (or its visible headers when the original block is empty) and body while
   preserving their line endings; the container does not retain
   the original header/body separator, so the adapter restores one matching the
   header line ending.
4. Hash the raw RFC 5322 bytes and parse only headers needed for identity,
   classification, and exclusion. Resolve dates by comparing `Date:` with the
   trimmed UTC median of valid `Received:` timestamps. When no usable
   `Received:` timestamp exists and the `Date:` header is missing or has an
   epoch-like year through 1980, scan decoded text bodies for embedded `Date:`
   headers and the localized patterns in the packaged
   `message_patterns.yaml`, choosing the most recent plausible candidate and
   recording `body-embedded`. When no header or body date supplies a result,
   use the message-specific `source_date_utc`, then the
   prior resolved message date in the same input stream. A filesystem message
   still lacking a date derives its year from a four-digit year in the source
   path; record every fallback source in the catalog.
   An unexpected parser exception records the source display name, opaque
   cursor and numeric byte offset when available, raw
   hash, and exception before stopping; earlier messages published by any file
   worker remain committed.
5. If the source adapter marks an exact MBCP metadata stub, commit a
   `source-metadata-excluded` observation and continue. If
   `X-Apple-Auto-Saved` exists, commit an `autosave-excluded` observation and
   continue. Do not write an MBOX record for either exclusion.
6. Look up `(normalized Message-ID, SHA-256)`.  If it exists, commit a
   `duplicate` observation and continue without antivirus or text extraction.
7. Stream the raw message to ClamAV.  A positive result routes it to
   `INFECTED`; all other nonfatal outcomes retain it in its normal category
   while recording the result.
8. Durably journal the target MBOX, its prior size/existence, and the message
   identity, then append and flush the mboxrd-encoded raw bytes.
9. Keep message, recipient, defect, observation, and location rows in one
   catalog transaction until the append succeeds. Commit the authoritative
   catalog and clear the journal. An exception or the next ingest startup
   truncates an uncatalogued append and refreshes the BagIt/Mailbag checkpoint; a catalogued append
   is validated and retained. Index disposable search content afterward only
   for normal Sent and Archive mail.

Deduplication is deliberately before ClamAV. A known archived message is not
scanned again, including one previously imported without scanning. A future
rescan operation must explicitly revisit those stored messages; no rescan
command is currently implemented.

## Standalone IMAP CLI

`imap_cli` exposes `mailarchiver-imap` and `make imap IMAP_ARGS=...`. It reuses
`create_empty_archive`, archive validation, the writer lease and `run_ingest`,
with eight workers by default and terminal scoreboard output disabled. A separate
catalog reader prints committed totals every 300 seconds and optional message lines.
The SIGINT handler requests cooperative cancellation and retains the lease until
the ingest engine has stopped and checkpointed. No desktop window or OS scheduler
is started. Setup is interactive; subsequent invocations can be unattended.

Strict `ImapAccount` records live in `config.yaml.imap_sources`; nonempty lists
select configuration version 3. Existing version-1/2 files remain readable. The
opaque source ID and endpoint fingerprint derive
`keyring://mail-archiver/imap/<id>/<endpoint-fingerprint>`; the native macOS or
Windows backend stores its password keyed by this reference and username. Changing
connection identity assigns a new ID to prevent cursor reuse against another account.
The endpoint fingerprint also prevents hand-edited server settings from reusing
an existing password against a different host, port or TLS mode.

`imap_source` replaces the reserved IMAP manifest. Discovery uses authenticated
LIST and read-only EXAMINE, freezes UIDVALIDITY/UIDNEXT, and snapshots bounded worker
ranges through the existing discovery database. Each worker owns a TLS session,
enumerates at most 65,536 UID numbers per SEARCH, fetches metadata in batches of
256 messages, and streams complete BODY.PEEK literals through ordinary ingest.
BODY size and UID identity must match before publication. Gmail selects only the
special-use `\\All` mailbox; X-GM-MSGID and X-GM-LABELS replace header-based matching.
INTERNALDATE is an explicit source-date fallback, not a rewrite of RFC 5322 headers.

Additive operational tables in `archive.sqlite3` retain `imap_seen` UID/Gmail-ID
checkpoints, `imap_hourly` UTC counts, and the observed `imap_mailboxes` hierarchy.
Gmail IDs use TEXT to preserve unsigned 64-bit values. Publication records remote
metadata and counters in the same catalog transaction as the corresponding
observation. Errors are counted but not checkpointed as successes. Known messages
yield the additive API-v1 `SourceObservation` metadata event, updating labels without
body transfer or download counts. `MailObject.provider_metadata_json` survives
durable ingest retries; attached-message promotions explicitly clear it so they
cannot acquire the parent download's identity or count. Old label hierarchy nodes
remain historical observations; the latest membership is retained per remote UID.

`make test-imap` uses development-only pymap, verified temporary TLS certificates
and synthetic messages. It exercises actual CLI/ingest behavior and source
immutability, rather than a mocked IMAP client. Native secrets access is substituted
only in tests. Live Gmail, native Windows consoles and native keyring interaction
are separate acceptance checks. Scheduling, background operation and graphs remain
unimplemented. All attachment bytes are preserved; indexing uses the existing
extractors and does not gain PDF/Office extraction in this change.

## Current acquisition boundaries

The Google authorization prototype, its developer client override and its
dependencies have been removed; the application contains no OAuth client.

Apply the [Apple Mail partial-record boundary](../APPLE_MAIL_CACHE.md#current-importer-behavior): report incomplete cache input without treating it as a full message.

## PST corpus acquisition implementation

The Cargo workspace includes the `pst` package with `pst/pst-downloader.rs`.
`make pst-download-plan` validates inventories without network/filesystem output;
`make pst-download` streams HTTP through reqwest, ZIP through zip and 7z through
sevenz-rust. Transfers only copy bytes; validation hashes the completed temporary
files before publication. SHA-256-addressed PST objects, URL-keyed original artifacts/receipts,
and atomic per-run/latest reports live under ignored `var/pst/`. A cache lock
serializes writers. Supplied expected hashes differ explicitly from observations.
[The contract](../../pst/README.md) documents reruns, limits and unavailable links.
`make test-pst-downloader` uses real temporary HTTP servers and archives to test
exact bytes, deduplication, cache tampering, extraction and partial failure.
The fixture server explicitly puts accepted sockets into blocking mode with a
read timeout. A real split-header regression checks that packet gaps cannot
produce a premature response, as observed in a failing local macOS run.
The downloader does not invoke the PST importer or canonical archive engine.
The cache uses OS file locks with a persistent guard inode to prevent unlink
races. Ctrl+C cancels async HTTP waits and removes the diagnostic lock; forced
death releases OS ownership for the next invocation. Publish receipts before
artifacts, preserve legacy unreceipted artifacts, and verify completed downloads
before reuse. Real subprocess tests cover Ctrl+C, forced termination, competing
writers, stale locks, and reuse without redownloading completed files.

## External libpff converter

`pff_source.py` invokes the independent `converters/pff` executable. Only that
GPLv3 program loads `pypff` from pinned `libpff-python==20231205`; the GPLv2 host
never imports it. The converter uses read-only file objects, labels the source as an Outlook cache,
walks normal folders with cycle/depth controls, and streams base64 attachment
chunks into a bounded per-message MIME buffer. Headers and Unicode text are used
to construct recovered, interoperable email; original transport-header text is
retained as a separate evidence part. libpff supplies decompressed RTF. By-value
and OLE attachment streams are retained. The Python binding does not expose
embedded MAPI message reconstruction: that item is reported as incomplete rather
than invented. Non-mail Outlook administrative data and virtual search folders
are outside the email collection scope. No deleted-record carving occurs.
Missing transport headers use available MAPI display recipients; these may contain
names rather than resolved SMTP addresses. Sender reconstruction retains the name
and an available SMTP property; it does not treat an Exchange DN as an SMTP address.
Unrecognized MAPI classes report incomplete extraction instead of being silently
excluded. Native attachment lengths are checked against bytes read.
This is best-effort recovery from the local OST cache, not a claim about the
complete server mailbox.

`processing-libpff/<run>/receipt.json` records source SHA-256, libpff version,
process ID, content type, counts, completion, and fatal failure details;
`diagnostics.jsonl` streams bounded per-item descriptions. Sources are rehashed
before success. `plugins.ost` settings are `timeout_seconds` (60),
`max_message_bytes` (64 MiB), `max_folder_depth` (64), `executable`,
`max_output_bytes` (1 GiB), and `max_diagnostics_bytes` (64 MiB). The host kills
and reaps a child that exceeds the deadline or output bounds. Native body
allocations occur in that separate process before reconstructed-output checks.
Exit 3 reports incomplete source items with fully written emitted records; other
nonzero exits retain evidence without admitting potentially truncated output.

`make pff-converter` installs the separately locked converter environment;
`make pff-converter-bundle` builds its standalone executable for the DMG.
The bundle excludes pypff from the main application and includes the independent
converter, its source and notices. For scanned imports, the host first invokes
the external converter and then `mcti-scan`, a separate GPLv2 Rust executable
using libclamav. That producer-side stage emits infected-only headers. The host
consumes its output without rescanning or adding clean-message provenance.
No process loads both libpff and libclamav. Neither executable writes the archive
or computes canonical message hashes.

PST dispatch has no reader-selection UI or setting: `PstFileParser` always
invokes the Rust helper. The separate `PffFileParser` handles OST. Both use
`item:<node>` cursors.

Local container metadata carries an optional parser/settings fingerprint. Local
integrity controls persist it alongside the source hash under
`local-parser-settings-v1`; missing or changed fingerprints require a new read.
Existing non-Outlook parsers retain their source-only checkpoint behavior.

`make test-pff` uses the real Aspose Unicode/version-23 OST and public PST
fixtures, with CLI archive creation, content resume, search, and canonical
fixity verification. OST support is best effort rather than a version-specific
completeness claim. The native
extension is a dependency only of the standalone converter. Its complete
LGPL/GPL license texts are retained with that converter because its wheel omits them.

### Retired desktop experiments

See [RUST_GUI_EXPERIMENTS.md](../RUST_GUI_EXPERIMENTS.md) for the consolidated
architecture, performance measurements and historical acceptance record.
Independent Rust importer, verifier and MIME tools remain supported.

## Recovered tool safety and validation

Native initialization has a 120-second deadline; production scans retain a
60-second deadline. A stuck/crashed native worker fails pending scans and is
reaped. Owned extraction/message temporaries are removed. Scan threads persist
typed evidence directly, and processor invocation history retains it too.

The h3 review prototype (see [CLI limitations](../CLI_REFERENCE.md#research-and-diagnostic-commands))
writes hash-verified ambiguous classes to a new private directory outside both
source stores. Refresh validates manifest paths and hashes; it does not import
or deduplicate. Its writable SQLite comparison index enables URI handling and
attaches the canonical catalog with `mode=ro`. Annotation reads one message
variant at a time.
Publication uses exclusive directory creation instead of replacing the destination.
Staged children move into that reserved directory with the top-level manifest
last; caught move failures roll completed moves back to staging. A process crash
can leave an incomplete reserved directory without a manifest; publication is
not a crash-atomic directory swap. Other publishers never reuse that directory.

`make name-matcher-observations ARCHIVE=... OUTPUT=... ARGS='--limit 100'`
builds a private SQLite derivative outside the canonical archive, using verified
MBOX locations and a read-only catalog. Extraction errors are recorded. The
prototype makes no provider requests and selects no production matcher.
Deferred result correlation rejects duplicate request IDs before reading results,
including independently constructed identical requests with deterministic IDs.
Evidence publication requires a filesystem supporting hard links and owner-only
permissions. The completed database is linked exclusively from same-filesystem
temporary storage; plain rename is not a safe no-overwrite fallback on Unix.
The summary is fully written and closed in staging before either publication
link is created. If the second link fails, the first is removed so ordinary
publication errors do not strand an output. This is rollback on caught errors,
not a crash-atomic transaction across two directory entries.

`make test-reconciliation` exercises these recovered boundaries.
`make distribution-check` builds and installs wheel and sdist, checks packaged
resources, and invokes console entry points without a GUI. This smoke does not
validate installed GUI resources; separate DMG tests exercise the desktop bundle.

## PST reconstruction and importer tools

The [on-disk format inventory](../ON_DISK_MAIL_FORMATS.md) records PST/OST research;
the [executable importer specification](../PST_IMPORTER.md#executable-stream-contract) defines the
filename-to-stdout-mboxrd interface. The standalone [PST adapter](../PST_IMPORTER.md)
uses Microsoft's `outlook-pst` 1.2.0 through read-only `read_from` handles,
traverses the IPM subtree and validates each bounded temporary record before
streaming it. It reconstructs MIME and retains attachment/transport evidence;
source fixity checks and partial-run errors prevent false success.
Each record resolves its timestamp once: a valid transport `Date:` header, then
MAPI submit time, then delivery time, then the Unix epoch. That value supplies
both the reconstructed RFC `Date:` header and the UTC ctime-form mboxrd
delimiter, so their instants cannot diverge.
The Rust and libpff commands apply the same reconstruction rules to primary
headers: normalized source-or-MAPI dates and senders, decoded subjects, and
validated Message-IDs. Both accept `--offset` and `--limit`, traverse folder
and message node IDs in ascending order, and stop once the requested
normal-content range is selected. Unknown non-ASCII String8 body code pages
retain their bytes with a Windows-1252 fallback declaration. Attachments labeled
`multipart/*` are opaque base64 attachments, so the enclosing MIME part uses
`application/octet-stream` rather than an invalid encoded multipart container.
Both serializers copy safe transport fields directly instead of allowing a MIME
library to RFC 2047-encode ASCII punctuation. MIME parameter and attachment
serialization uses the shared unquoted charset, Content-ID, disposition, and
RFC 2231 filename forms.
Their item provenance URI uses the common `#item=<numeric-node-id>` fragment;
the producer identity remains in `X-Importer-Name` and `X-Importer-Version`.
An initial read-only traversal of normal contents from the PST root collects
Contacts throughout the folder hierarchy before emitting mail. It caches all
populated Email1/Email2/Email3 slots using PSETID_Address's store-specific named
property IDs. Slot address/original-display-name properties supply explicit
DN/SMTP pairs; validated Exchange Address Book EntryIDs supply DN aliases for
SMTP contacts. Search folders, associated configuration objects and orphan carving are excluded.
The traversal also collects explicit DN/SMTP pairs from sender
(`0x0C1F`/`0x5D01`), represented-sender (`0x0065`/`0x5D02`), and recipient-table
(`0x3003`/`0x39FE`) properties. It retains contact addresses and the identity map,
not message bodies or photos. Stderr reports contact/slot/mapping/conflict and
unreadable-object counts. Lookup failures outside mail extraction also prevent
success; failures encountered in both passes are counted once.
Case-insensitive DN matches resolve only when SMTP evidence is unambiguous.
Resolved `From` uses the SMTP address and available sender display name;
`X-PST-Original-Sender` retains the DN and `X-PST-Sender-Resolution` identifies
the evidence item/property. Missing or conflicting mappings leave Exchange
sender values unchanged in `From`, including a source display-name angle address,
with `X-PST-Sender-Address-Type: EX`.
The same map supplies missing SMTP addresses for reconstructed To/Cc/Bcc rows,
with `X-PST-Original-Recipient` and `X-PST-Recipient-Resolution` evidence.
Existing From/To/Cc/Bcc/Sender/Reply-To/Resent-*/Return-Path fields resolve whole
native values or angle-bracket addresses outside quoted names/comments;
`X-PST-Original-Address` and `X-PST-Address-Resolution` retain substitution evidence.
Existing SMTP addresses and the attached original transport headers stay intact.
The archive validator recognizes this marked
native identity instead of requiring SMTP syntax. Ordinary mailbox validation
remains in place. Subjects lose only the MAPI marker and prefix-length character;
their textual prefixes remain. They are decoded and emitted as readable UTF-8 with
whitespace folding; only unbroken words over 900 bytes use encoded-word folding.
Header values permit valid UTF-8, while field names and provenance remain ASCII;
control bytes, malformed UTF-8 and oversized lines still fail validation.
MAPI Internet code page 1256 maps to MIME `windows-1256`; plain and HTML body
bytes are base64-encoded unchanged. Unicode properties still emit UTF-8.
Meeting classes are counted as non-mail without per-item diagnostics. Typed
PST, messaging, LTP, and NDB failures are labeled `LIBRARY ERROR (outlook-pst 1.2.0)`.
The `--only-invalid` option runs the same reconstruction/validation but emits
only diagnostic envelopes for readable failed items. It spools each envelope
within the existing record limit; selected MAPI headers/body properties are
base64 attachments, with Unicode buffers stored as UTF-16LE and String8/binary
buffers unchanged. Synthetic envelope headers and an `X-PST-Diagnostic` marker
distinguish these from recovered mail. Valid mail is suppressed; unreadable
items have stderr evidence only. A `reconstructed-headers.txt` attachment retains
the completed header block from the failed reconstruction spool verbatim,
excluding its mbox envelope. It is not an original transport-header property.
`invalid-exported` counts diagnostic records,
separately from normal `emitted` messages; extraction errors still return nonzero.
Another implementation can run as a second pass. The CLI adapter invokes the executable,
decodes its mboxrd output and sends recovered messages through the ingest
pipeline for hashing, scanning and publication. H3 already includes
the encoded body; its top-level header selection excludes importer annotations.
Cross-importer duplicate suppression requires a separate explicit policy and
must not silently discard differences in bodies or attachments.

PST and OST share the same storage-format family. The adapter and pinned crate
enforce PST client magic. The separate `ost` file plugin invokes the external libpff converter
and recognizes OST client magic before considering extensions. Genuine Unicode
OST fixture tests exercise recovered mail and incomplete embedded MAPI items.
The detailed distinction and reader limits are in
[PST_IMPORTER.md](../PST_IMPORTER.md#relationship-between-pst-and-ost).

Packages use selected OS/architecture-specific executables and native
dependencies; the Rust adapter does not require a JVM.
The macOS builder bundles `pst-importer`, `mcti-scan` and the `pff-converter`.
The Windows MSIX does not yet include them, and no Linux Snap builder exists.

The Cargo workspace now contains `rust/mct-importer`: the Rust library,
`pst-importer`, `mdti-validator` and `mcti-generator` implement/test
[MCT Importer API 1.0](../MCT_IMPORTER_API.md). `make rust-programs` or each named
binary target produces release executables under `target/release`; Windows adds
`.exe`. `pst-import` and `pst-smoke` share a `pst-input-check` prerequisite
that checks the exported `PST` value for a readable regular file. Builds run
only after this check, including under parallel make, and their output goes to
stderr. `pst-import` leaves stdout exclusively for extracted mboxrd.
`make rust-check` runs rustfmt, Clippy with warnings fatal, and Rust
tests including real process pipelines. `make check` includes this stage after
Python type checks and before pytest. Cargo.lock pins dependencies. The validator
uses bounded byte reads, one mboxrd decode, mailparse header/address parsing,
Chrono RFC-date validation, uriparse absolute URI validation, strict transfer
decoding and explicit multipart closure/depth checks. It discards input and
counts valid complete records at EOF, reporting the first error per rejected
record. It is not a full RFC grammar oracle or an archive ingest command.
The generator produces a deterministic 7bit text MIME part with a counter and
From-like lines. No h4 is introduced; comparison uses h3 and exact fixity h2.
Rust is required to build the PST importer; released packages will
bundle its native executable without requiring users to install Rust.
