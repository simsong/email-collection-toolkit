<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Archive requirements

Current subsystem contract and acceptance boundaries. This is not a claim of
platform/release acceptance. Deferred features live in [plans](../plans/README.md).

## Manual state and documentation status

The archive has three operational databases: `archive.sqlite3` (catalog and
source observations), `search.sqlite3` (disposable search), and
`processing.sqlite3` (queues, evidence, identities, affiliations and tags).
Manual identity/tag decisions are durable user data, not reproducible from mail.
Back up the entire archive, including databases and `config.yaml`.
Current source code includes CLI and GUI processor integration and local PST/OST
adapters. Research plans and historical release inventories are not promises
of shipped features. Authoritative matching, the tag editor, provider OAuth
and geography remain deferred; the Rust GUI migration is retired.

## Purpose

Create and maintain a cleartext, personal, long-lived archive of all user
email. Canonical MBOX and manifests preserve message content. Search indexes are
rebuildable; manual identity/tag decisions and operational history require backup.
Unified search uses local derived indexes. Redacted exports and richer research
derivatives are specified separately in the [archive extension plan](../plans/ARCHIVE_EXTENSIONS.md#redaction-and-research-derivatives).

This document specifies current preservation invariants. Deferred extensions
are indexed in [plans](../plans/README.md); the
[implementation overview](../implementation.md) maps current behavior to source.

Supported acquisition paths and source-idempotence requirements are defined in
[Acquisition](ACQUISITION.md). Unimplemented source families belong to the
[acquisition plan](../plans/ACQUISITION.md).

## Canonical archive layout

All deliverables reside in one archive directory. That directory is a native
BagIt 1.0 bag conforming to Mailbag 1.0. It contains `bagit.txt`,
`bag-info.txt`, `mailbag.csv` or its required numbered parts,
`manifest-sha256.txt`, `tagmanifest-sha256.txt`, a top-level `integrity/` tag
directory, and a `data/mbox/` payload directory.

* Mail is stored only under `data/mbox/` in standard MBOX files, using
  byte-preserving mboxrd quoting. Do not retain a per-message EML corpus.
  Quote every payload line matching `^>*From ` by adding one `>`; decode exactly
  one level. Python's default mboxo escaping is insufficient. Existing mboxo
  archive records remain unchanged and must be recovered only through their
  expected h2. Explicit `.mboxrd` sources declare reversible decoding; unknown
  source dialects retain stored quoting. See [MBOX_READING.md](../MBOX_READING.md).
  All stored headers, including added provenance fields, participate in h2;
  there is no X-header exclusion from raw-message integrity. Document every
  digest's input, purpose and limits, and all added headers, in
  [INTEGRITY_CONTROLS.md](../INTEGRITY_CONTROLS.md).
* Preservation acceptance uses four committed byte fixtures under
  `tests/data/writer-preservation/2024`: CRLF without a final newline, distinct LF
  content sharing the same Message-ID, invalid declared UTF-8, and a missing
  multipart boundary. The portable writer and production GUI import service
  must retain all four, keep source files unchanged, export original bytes,
  and pass independent archive verification. Missing dates use source-year
  fallback; malformed MIME remains accessible as Raw Source.
* Accept LF and CRLF input independently of the host operating system. Do not
  recode line endings in message headers, bodies, attachments, or existing
  delimiters during import, storage, or retrieval. Preserve lone CR characters:
  they may be content, such as terminal progress output emailed from stdout,
  rather than line terminators. Platform newline translation must not change
  source bytes. This policy does not change the explicitly documented MBOX
  framing rules. The explicit IMAP acquisition exception converts CRLF to LF
  before canonical hashing and records original wire hash/size; local imports
  and existing archives retain their original line endings.
* Preserve every available original `From ` record delimiter, including sender,
  timestamp, whitespace, and line ending. Carry MBOX framing separately from
  RFC message bytes so message hashes and deduplication stay unchanged except
  for the explicit double-framing normalization below. For
  duplicate RFC messages, the first published observation supplies the envelope.
  For immediate double framing, preserve the selected delimiter and convert
  the other envelope to a literal `X-From:` header under the rule below.
  Apply the canonical byte/hash and source-reconstruction contract in
  [INTEGRITY_CONTROLS.md](../INTEGRITY_CONTROLS.md#verifying-and-reconstructing-normalized-records).
  The separate supported status-header `From XXX` wrapper uses its nested delimiter.
  Only synthesize a delimiter when none exists: use the latest valid timestamp
  across Date, Received timestamp suffixes, Resent-Date, and Delivery-Date,
  normalized to UTC, independently of the routing-date median. Invalid or
  implausible timestamps do not participate. Without a valid header timestamp,
  use the existing resolved source/prior/path date; a low-level writer without
  that context uses the fixed Unix epoch, never the current time. Synthesis uses
  the parsed sender when representable as one ASCII token, otherwise
  MAILER-DAEMON. Never rewrite a present envelope to conform to these rules.
* Normal mail is partitioned by resolved message year and category:
  `{YEAR}-Sent1.mbox` and `{YEAR}-Archive1.mbox`.
* A nonempty file rolls over before its next record reaches the configured
  `mbox_max_bytes` limit in archive `config.yaml` (default 3.75 GiB, 4026531840
  bytes). The limit includes envelopes, mboxrd quoting and record separators.
  A single record at or above the limit is preserved whole in its own part;
  the next record starts a new part. No message is split or discarded.
  Tests use four synthetic 6 KiB messages and a 20 KiB limit: three records
  in part 1 and the fourth in part 2. Later parts are named
  `{YEAR}-Sent2.mbox`, `{YEAR}-Sent3.mbox`, `{YEAR}-Archive2.mbox`, etc.
* Messages detected as infected are instead placed in `INFECTED1.mbox`, with
  the same numeric rollover rule if needed.  They are never discarded or
  altered.
* A noninfected message is **Sent** when its parsed `From:` address matches
  an owner include rule and no exclude rule; otherwise it is **Archive**.
  Rules are case-insensitive whole-mailbox globs (`*`, `?`, `[abc]`). A bare
  `slg` is equivalent to `slg@*`: it matches `slg@example.org` and the legacy
  sender `slg`, but not `3slg` or `slg+tag`. Only a rule containing `@` matches
  a domain. `*simson*` with exclude `*david*` excludes `david_simson@example.org`
  and never matches an unrelated mailbox just because its domain is `simson.net`.
  Neither display names nor implicit substring expansion identify owners.
* Resolve routing dates using [DATE_HANDLING.md](../DATE_HANDLING.md): plausible
  Date/trimmed Received median, documented embedded-body and source/stream/path
  fallbacks. Preserve original headers/bytes and record the selected date source.
* `X-Apple-Auto-Saved` messages are excluded entirely.  Their source and
  exclusion reason are retained in the primary database, but no MBOX copy is
  created.
* An MBOX record whose envelope sender is exactly
  `mbcp@s.eecs.harvard.edu`, whose only headers are `X-UID`, `Status`, and
  `X-MBCP-Flags` (with both X-headers present), and whose body is empty is
  source metadata, not an email. Record a `source-metadata-excluded`
  observation and do not publish it. For double framing, apply this check to the
  selected envelope and original headers/body after the quoted delimiter; ignore
  only the generated `X-From:` field, never an original header or body text.
  An envelope sender of `XXX` is unwrapped
  only when the outer record has a nonempty, well-formed status-only header block and
  its body starts with a quoted nested delimiter with complete ctime syntax. Indented or unquoted body
  lines are never nested delimiters. Retain the outer source offset as provenance.
* A framing line copied into an RFC header must start with literal `From ` and
  contain exactly one LF- or CRLF-terminated line. Malformed outer framing is
  left unnormalized, including in the legacy status-wrapper path, and remains
  subject to normal import validation.
* Double processing is recognized when the first payload line, immediately
  after a physical MBOX delimiter, is itself a `>From ` delimiter with a sender
  and ctime-style timestamp. Keep the outer delimiter and convert the quoted
  line to a literal `X-From: sender timestamp` header. If the outer sender is
  exactly `XXX` or `???@???` and the inner sender is neither placeholder,
  instead promote the inner delimiter and convert the displaced outer line to
  `X-From:`. Real local senders, including `nobody` and `MAILER-DAEMON`, are not
  automatically bogus. Retain envelope values/line endings and all following
  message headers and body bytes, including body quoting. This explicitly
  authorized framing normalization changes canonical message bytes: canonical
  hashes describe the normalized message, while observation detail records the
  original source-payload SHA-256, exact framing bytes and rule in a versioned record.
  Do not recursively remove quotation levels or infer a producer from branding.
  Do not search later header/body lines, accept indentation, or use timestamp
  differences to infer wrapping. See [MBOX_READING.md](../MBOX_READING.md) for the
  Procmail/formail, MIMEDefang and Eudora compatibility boundary.
* Each finished `data/mbox/NAME.mbox` has one
  `integrity/NAME.mbox.integrity` BagIt tag in the versioned hybrid format
  specified by [INTEGRITY_CONTROLS.md](../INTEGRITY_CONTROLS.md).
  Its JSON control records declare `h1` as SHA-256 over the complete MBOX,
  `h2` as SHA-256 over each recovered original RFC 5322 message, and `h3` as
  SHA-256 over semantic-message standard version 1. Its TSV region records
  ordered message identifiers and tagged `h2:` and `h3:` digests.
* `manifest-sha256.txt` lists every payload file exactly once. Its MBOX digest
  equals that file's `h1` digest. `tagmanifest-sha256.txt` hashes the BagIt and
  Mailbag metadata, every integrity tag, the payload manifest, and the
  installed validator; it is published last.
* `mailbag.csv` has one row per canonical message and uses a stable,
  case-insensitively unique Mailbag Message ID derived from the normalized
  Message-ID and raw SHA-256. It records the containing MBOX and MIME attachment
  count. Archives over 100,000 messages use the Mailbag-required numbered CSV
  parts.
* A source message retains the SHA-256 identity of its original bytes even when
  it lacks a final line break. Because the standard MBOX writer adds a final
  line break when one is absent, direct retrieval and independent verification
  consider both representations and accept only the one matching that original
  SHA-256. This includes the zero-byte-message case.
* Because the standard-library MBOX writer does not distinguish an escaped
  source `From ` line from an original literal `>From ` line, direct retrieval
  considers the bounded possible interpretations and accepts only the one whose
  SHA-256 matches the catalogued original bytes.
* A source message may itself begin with an MBOX-style `From ` line, notably in
  an Emacs RMAIL Babyl original-header block. The standard-library writer uses
  that line as the canonical MBOX record separator. Direct retrieval therefore
  considers both payload-only and separator-plus-payload interpretations, with
  the catalogued original SHA-256 selecting the source representation.

`archive.sqlite3` and the disposable `search.sqlite3` are operational BagIt
tag files but are deliberately not listed in the tag manifest; their live
SQLite state is outside the portable preservation checkpoint. An optional
archive-local copy of geographic reference data is also operational metadata;
it is explicitly copied by the user and is never refreshed implicitly.
The top-level `status/` directory likewise contains operational, unmanifested
JSON tag files. Each ingest creates a distinct file and atomically replaces
only that file with its current typed status; the final replacement retains
the run's complete statistics as append-by-run history.

The archive lives on the encrypted laptop filesystem.  BorgBackup and
Backblaze provide independent backup; archive-internal encryption is not a
requirement.

Each new BagIt checkpoint records the installed application version as
`Mailbag-Agent-Version`. Existing historical bags are not rewritten merely
because a new software version is installed.

Read-only data-quality audit tools may create derived MBOX, CSV, and JSON
evidence from a source tree and canonical archive. Those outputs contain
private message content and metadata, must default to an ignored temporary
directory, and must never be committed. The tools must refuse to overwrite
existing evidence and must not modify source mail or the canonical archive.

## Deduplication and provenance

On macOS, Command-Q during an active import must offer Cancel or Stop Import
and Quit. Explain that quitting stops imports, that **Continue Processing**
resumes saved work (or File → Import retries the source), and that already
archived messages are not imported twice. Cancel leaves imports running. Confirmed
Quit disallows new imports and signals every local/native and API import to stop
after its current message, before fetching the next. With no active jobs or import
worker tails, exit immediately after bounded private-export cleanup without waiting for UI callbacks. Otherwise allow
one shared five-second budget for message completion, checkpointing and lease
release, then force process exit even if workers remain. Exit sooner when work
finishes. A deadline can interrupt a message or checkpoint: SQLite transaction
rollback and the durable MBOX append journal support recovery on the next ingest;
an interrupted archive is not promised to have current BagIt manifests. Continue
Processing or reimport repairs checkpoints and deduplicates committed messages.
Do not bypass Sparkle's deferred/installing handoff. Window-close restrictions
during ingest remain intact.

Archive creation, explicit message/attachment saves (including standalone windows),
filter-set mutations, and owner-rule/identity saves already in progress share the Quit deadline; reject
new writes after Quit is reserved. Track creation before the first archive file
is initialized, and explicit saves through atomic destination replacement.
Delete private attachment/drag copies without window
callbacks, and reclaim interrupted cleanup on a later launch only when the
owning process is gone. Native scanner helpers must exit when their owner dies,
including while a scan is blocked. Replayed ingest work follows the same
current-message cancellation boundary as newly acquired mail. GUI Open must
recover hot SQLite rollback journals under the archive writer lease before
read-only schema validation, retaining database and sidecar path-safety checks.
Include the optional existing processing database so Continue Processing can
read its queue and saved policy after a crash. Import startup remains tracked
from lease acquisition and settings writes through job registration; failed
startup releases its lease before reporting completion. Definition refreshes
also share the Quit deadline, and their updater process cannot outlive the GUI.
Arm the exit watchdog before taking any application locks after confirmation;
a callback waiting on Cocoa must not prevent deadline enforcement.
Pre-confirmation probes of application, controller and document state must not
block. Unavailable job state requires confirmation rather than an idle assumption;
Cancel must leave work running without reserving Quit or arming its watchdog.
Setup Cancel must dispatch Quit before either menu refresh can wait on a callback lock.
Processing database initialization must publish a complete schema atomically;
a version marker alone is insufficient for validation. Message-boundary replay
must not repeat archive-wide recovery or aggregate-report scans for every message.

The required Rust import gate in `make check` must import the entire local
`tests/data/` directory through the CLI into a disposable archive, with a
ten-minute subprocess deadline. A reviewed expectation file lists source fingerprints and every
retained email's subject and raw SHA-256, plus observation/exclusion counts.
Failures report both unexpected and missing messages. Verification must read
canonical bytes, run the installed validator, confirm source immutability, and
repeat ingest to establish idempotence. Updating expectations requires the
explicit `make update-corpus-expectations` Rust maintenance target. Tracked
fixture expectations are public; ignored local mailbox expectations remain in
an ignored local overlay. Unknown local files fail comparison until explicitly reviewed.
This test uses the real configured ClamAV engine, as the GUI does. Its subject/hash
expectations include quarantined mail and do not depend on signature-dependent
mailbox destinations. Dedicated EICAR tests also verify infected routing.

* A duplicate is only a message with both the same normalized `Message-ID`
  and the same SHA-256 of its RFC 5322 message bytes.  Never collapse all
  messages sharing only a Message-ID.
* Messages missing Message-ID are retained and identified by their SHA-256;
  they are reported as metadata exceptions.
* A **source** is where mail was found; an **archive mailbox** is where this
  program saved a deduplicated canonical copy. Every source observation links
  to one source file and source volume, records its source/forensic path,
  offset where applicable, ingest run, raw RFC 5322 SHA-256, semantic (`h3`)
  SHA-256, and disposition (`archived`, `duplicate`, `autosave-excluded`,
  `source-metadata-excluded`, or error). One archived message can retain many
  source observations.
* A source volume has a stable identity plus normalized JSON metadata. Local
  ingest records the complete OS volume report available at ingest time,
  including label, format information when available, and current mount path.
  Cloud adapters use a provider/account origin identity plus per-container
  native ID, hierarchy, display name, and non-secret provider JSON.
  A local provider cache records a typed `cache` relationship, the upstream
  provider kind, and any non-secret local account hint while retaining its
  local volume and physical path. If the same canonical message also has a
  direct cloud-provider observation, provenance displays that direct source
  first and identifies the local observation as a retained cache copy.
  Source evidence is retained in the private archive catalog and excluded from
  redacted or public derivative packages by default.
* Idempotence is at message level.  After a raw message hash and Message-ID
  have been obtained, a matching stored identity is skipped before ClamAV,
  text extraction, and MBOX writing.  A deliberate rescan/reindex mode is
  separate from normal ingest. Repeated ingest may add newly available source
  messages. A byte-identical message found in a backup, provider export, and
  local cache has one canonical record with multiple observations. A record
  that differs in raw RFC 5322 bytes is preserved even when its semantic `h3`
  digest matches; semantic identity is reconciliation evidence, not an
  admission-time discard rule.
* Every source plug-in declares source integrity controls appropriate to its
  source. The framework executes those controls, displays their progress, and
  persists typed evidence and resume decisions. Cryptographic hashes,
  provider version tokens, immutable identifiers, and cursors are distinct
  evidence kinds and must not be mislabeled. Source integrity controls are
  separate from the canonical archive controls in `INTEGRITY_CONTROLS.md`.
  Integrity-control generators run outside the publisher lock so source
  hashing and provider I/O do not serialize unrelated workers, and their typed
  progress is forwarded as yielded. Only the catalog transaction that records
  validated evidence and marks the checkpoint complete is publisher-serialized.
* Every recognized local source file records its absolute path, nanosecond
  modification time, byte length, complete-file SHA-256, last check time, and
  completing ingest run. Those file columns are a local display cache; skip
  and resume decisions use only typed evidence from the last completed
  `source_integrity_checks` record. Reingest always verifies content: a matching full
  SHA-256 skips the file without parsing its messages.  If a file grew, ingest
  first compares the SHA-256 of the old length.  A matching MBOX prefix followed
  by a valid `From ` boundary resumes at that byte offset; all other changes
  reprocess the whole file.  The checkpoint is updated only after the selected
  region finishes and the source metadata remains stable.
  A lightweight preliminary pass counts recognized source containers and their
  available byte estimates without hashing or retaining message contents. It
  stores typed container metadata in a temporary SQLite work snapshot, prints
  every unrecognized regular file once with its path and reason, and finishes
  before any message is published. Zero-length files and paths matching the
  commented, case-insensitive globs in packaged `local_source_rules.yaml` are
  silently omitted from discovery and the unrecognized-file count. The same
  versioned YAML stores local file-probe and MBOX preamble limits and is
  strictly validated before discovery.
  Duplicate scoped container identities are
  scheduled once; conflicting definitions fail. Sources declaring stable
  inventory are verified by a second preflight discovery; live sources are
  discovered once. Worker execution is bounded by the configured count: each
  worker plans, reads, parses, scans, and checkpoints one container at a time. A new
  file's complete fingerprint is calculated before its checkpoint is committed. A later source failure or
  interruption retains committed messages; any in-flight file without a
  checkpoint remains safely rerunnable.
* Emacs RMAIL Babyl files are recognized from their case-insensitive
  `BABYL OPTIONS:` header rather than a filename extension. Both LF and CRLF
  containers are streamed read-only. Each record's original header block and
  body become one RFC 5322 message. If the original-header block is empty, the
  visible headers are the record's only headers and are used instead. Babyl
  labels and redundant visible-header copies are source-container metadata and
  are not added to the canonical message. A zero-record container ending with
  the Babyl `0x1f` end marker is a valid empty mailbox and completes normally;
  a container reaching EOF without either a record or end marker is truncated
  and fails.
* Source and physical file parsing use separate versioned, immutable plug-in
  registries. The production file registry contains MBOX, Babyl, EMLX, and
  single-message EML/Maildir generators. The production local source generator
  delegates each recognized filename to that registry. When more than one
  packaged parser recognizes a file, manifest priority selects the format;
  EMLX, Babyl, MBOX, then single-message precedence ensures that MBOX framing
  takes precedence over an enclosing Maildir `cur` or `new` path. Any
  recognition overlap involving an external parser remains fatal. Packaged
  reserved source stubs name Gmail API, O365, Microsoft Exchange, and standard input;
  the standard-input contract is RFC 5322 messages separated by a NUL byte.
  Reserved stubs must fail clearly and must not be exposed as working ingest.
* Built-in and explicitly configured trusted plug-in directories use API-v1
  manifests. All manifests are validated before any external Python is
  imported; duplicate kinds, incompatible APIs, unsafe entrypoints, ambiguous
  source selection, and external file-recognition ambiguity fail before
  workers start.
  Plug-in registries are frozen before inventory. Mail source trees and the
  archive are never searched for executable plug-ins. Typed boundaries are
  strict: in particular, text cannot be coerced into RFC 5322 bytes.

## Primary metadata database

`archive.sqlite3` is authoritative metadata, not the authoritative message
content.  It tracks at least:

* logical message identity: raw and normalized Message-ID, message SHA-256,
  date and date source, category, byte length, ClamAV result;
* parsed sender and recipient identity foreign keys, and decoded/unfolded
  Subject headers; sender identity uses a valid `From:` address, then a valid
  `From:` inside a quoted nested-MBOX record, then a valid RFC `Sender:`
  address. A narrowly recognized Google Chat event with none of these
  uses its embedded full name suffixed by `(Google Chat)`; all other missing
  senders remain empty in metadata and display as `(missing sender)` in reports.
  Every parser-provided header value is normalized to text,
  malformed fields fall back independently without affecting preservation,
  and their exception types and diagnostics are recorded;
* each final MBOX filename, message byte offset, byte length, and archive
  generation; and
* sources, ingest runs, exclusions, duplicates, validation results, and
  integrity metadata. Source volumes are distinct from source files: many
  paths may be found on one volume, and observations connect those paths to
  logical archive messages.

Logical messages and physical locations are separate relations.  MBOX offsets
are generation-specific and must be replaced atomically when a file is
sorted or repacked.

Email address text is normalized into `email_addresses(address_pk, address)`.
`messages.sender_address_pk` and `recipients.address_pk` reference that table;
`recipients.role` retains To, Cc, or Bcc while header order is not retained. The address
table also stores explicitly labeled non-email Google Chat identities.

## Sorting, validation, and recovery

* The MBOX byte hash, integrity tags, Mailbag CSV, payload manifest, tag
  manifest, locations, and metadata database updates are published in the
  documented checkpoint order. Interrupted runs leave either the preceding
  verified checkpoint or a detectably invalid, recoverable partial update;
  they never report a partial archive as valid.
* Ingest installs `verify_mail_archive.py` in the archive. This single-file,
  standard-library-only tool is strictly read-only: it verifies BagIt payload
  and tag manifests, required Mailbag structure, and every declared
  complete-MBOX, raw-message, and semantic-message digest without consulting
  SQLite. It exits nonzero after reporting any mismatch.
  Its source header and `--help` explain prerequisites, macOS/Linux and Windows
  commands, the default archive directory, checks performed, exit status, and
  read-only limitations. Instructions travel with every installed copy.
  The optional `archive` argument names the BagIt/Mailbag root and defaults to
  the script's directory, independent of the working directory. Before processing
  each file, print its path to stdout. Show throttled per-pass byte/message
  progress bars on stderr, including during the initial payload hashing pass.
  `--quiet`/`-q` suppresses informational output but retains errors. Ctrl-C
  reports incomplete verification without a traceback and returns status 130.
* The MBOX container's required separator newline is not part of a source
  message that lacked a terminal newline. Catalog retrieval and standalone
  verification select the candidate matching the recorded source SHA-256.
* Mailbag CSV uses CRLF record endings. Folded source headers used as CSV
  metadata are unfolded to single-line values; canonical message bytes are
  unchanged.
* `refresh-index` rebuilds the disposable FTS database in a temporary file,
  verifies every normal MBOX message against the catalog and the total
  searchable-message count, and replaces the prior index only after those
  checks succeed. It visibly reports a progress bar, completion count, and
  ETA weighted by catalogued message counts for mailbox verification and
  message indexing. It announces before starting that `Ctrl-C` discards the
  incomplete replacement and retains the existing search index. Its
  `--workers` option defaults to the available CPU count (or two if it cannot
  be determined), and parallelizes bounded verified-MBOX read/hash/MIME work while
  retaining one ordered SQLite writer. While it reads each verified message, it also refreshes the
  derived catalog subject from canonical header bytes, repairing newly supported
  legacy charset recovery without changing canonical mail. `review` queries the
  committed source-observation log by run, source, and disposition. Derived
  catalog fields and canonical locations are created correctly during ingest.

## Scope boundaries

The first release is a local command-line normalizer and verifier. Packaged
configuration holds archive and scanner policy; each archive's `config.yaml`
holds navigation state and explicit include/exclude owner rules. Legacy
`owner-names.txt` can seed those rules before the first confirmed import. A local
special-purpose search and message-viewing interface is a consumer of
the two SQLite databases, not a reason to depend on Thunderbird or FoxTrot.
No source mailbox is modified by this program.
The complete current catalog DDL is the packaged `sql/V1__archive.sql` resource
and is created only for a fresh archive. An unversioned catalog or any version
other than V1 is rejected rather than migrated. The separate disposable search
database likewise has exactly one packaged `sql/V1__search.sql`; before ingest
workers start, an obsolete search database is rebuilt from catalogued canonical
MBOX into a temporary file and atomically replaced, not migrated in place. A
failed rebuild preserves the prior database. A fresh catalog is also
refused beside existing canonical MBOX or `.mbox.integrity` output because that
would defeat deduplication. Those outputs are detected in `data/mbox/` and
`integrity/`; unsupported root-level legacy output is never imported.

The live appendable archive itself is the Mailbag interchange and preservation
package. A redacted or otherwise restricted release is a separate BagIt bag
with its own payload, manifests, Mailbag identifiers, and audit mapping. PDF
and WARC representations remain opt-in, sandboxed publication derivatives and
must not make remote requests without explicit authorization.
