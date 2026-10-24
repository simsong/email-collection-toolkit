<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Acquisition requirements

Current subsystem contract and acceptance boundaries. This is not a claim of
platform/release acceptance. Deferred features live in [plans](../plans/README.md).

## Printed-email extraction boundary

The standalone extractor preserves its source PDF, creates a new derived MBOX
with source/page evidence and marks generated records unreviewed. It does not
publish PDF messages into an archive. Remaining integration requirements are
in [plans/ARCHIVE_EXTENSIONS.md](../plans/ARCHIVE_EXTENSIONS.md).

## Remote account authorization

Google account authorization and its CLI are removed. Direct Google API ingestion is
not planned for the current release and will be reimplemented when needed.
Google Takeout MBOX and complete local Apple Mail cache messages remain supported.
Do not ship Google authentication libraries or Requests in the application's
runtime dependency closure. Any future HTTP clients should use the standard
library unless another dependency is explicitly approved.

## Standalone IMAP download CLI

`mailarchiver-imap` is a Python CLI independent of the desktop UI, targeting macOS
and Windows. `--new ARCHIVE` initializes only a new/empty destination; `--archive
ARCHIVE` validates and opens an existing archive. Initial setup prompts for server,
port, verified TLS mode, username and hidden password/app password, saves and runs.
Configuration uses `config.yaml` version 3's typed `imap_sources`, with unique source
IDs; passwords remain exclusively in the native OS keyring. Existing local-only
version-1/2 configurations remain readable. Missing credentials require interactive
`--configure`; unattended runs fail clearly without a plaintext or environment fallback.

Each invocation synchronizes once and exits. Eight download workers are the default;
`--workers` sets a positive count. Generic IMAP includes every selectable mailbox
except names containing `spam`, case-insensitively. Gmail discovers special-use
All Mail, fetches bodies only there, and obtains message identities and label
membership through `X-GM-MSGID` and `X-GM-LABELS`; other folders are not fetched.
All Mail coverage is explicit: Spam/Trash-only messages are not included.

Acquisition uses EXAMINE and BODY.PEEK, never mutating source messages or flags.
After validating the complete BODY.PEEK literal, replace CRLF pairs with LF
before ingest; retain lone CR, existing LF, and missing final newlines. Do not
parse/reserialize MIME. This applies throughout the message, including unencoded
MIME payloads; base64 attachment payload bytes remain unchanged when decoded.
Record original wire SHA-256, byte length and converted CRLF count in provider
metadata and retain this evidence across metadata-only label/UID refreshes.
Canonical hashes/deduplication use the normalized bytes. Existing checkpoints and
archived messages are not rewritten; old CRLF and new LF variants have distinct
canonical hashes if a later UID reset requires refetching. Local-file imports
keep their original bytes. MIME messages enter the existing ingest pipeline. INTERNALDATE
supplies a source date for malformed or missing message dates. Committed account/
mailbox/UIDVALIDITY/UID observations avoid repeating body transfer. Gmail IDs are
account-scoped unsigned decimal strings and survive UID epoch changes. Generic
epoch changes can require refetching, followed by canonical content deduplication.
Metadata-only refreshes update Gmail labels without increasing download counts.

Newly archived counts increment in the current UTC hourly bucket as each message
commits. Duplicate downloads, exclusions and message-ingest errors are recorded
separately. Provider checkpoints and counters share canonical publication's database
transaction; failed attempts cannot become successful checkpoints. Downloaded
duplicates retain observations but add no canonical copy. Nested attached-message
promotions are not counted as separate network downloads.

Status prints at startup, every 300 seconds and on exit. `--show-messages` adds
per-download dispositions and subjects. Ctrl-C cooperatively stops and checkpoints,
returning 130; network operations retain a 30-second socket timeout. Reruns resume
committed progress. Workers shall close source iterators on their owning threads;
terminal process-group interrupts shall not abort scanner children mid-message.
Default scanning remains required; only explicit `--no-scan`
permits unscanned ingest. Attachment extraction retains current local-import limits.
No scheduler, background agent, graphical panel or graph is delivered in this phase.
Integration tests use a real disposable TLS IMAP server and synthetic mail; tests
may substitute only the OS keyring boundary to avoid editing personal credentials.

## Ingest sources

* Recursive local-directory ingest recognizes MBOX streams, Apple Mail MBOX
  packages, Maildir, individual RFC 5322 files, and Apple `.emlx` files.
  MBOX may have a terminal-capture or MMDF control preamble when its first
  classic envelope appears within the first 16 physical lines, has a valid
  ctime-style timestamp, and is followed by an RFC header block. MMDF control
  delimiters frame source records and are excluded from the RFC 5322 bytes.
  The probe byte and line limits come from `local_source_rules.yaml`, rather
  than Python constants.
  A Maildir is recognized structurally: a message must be directly below
  `cur` or `new`, and their parent must contain `cur`, `new`, and `tmp`
  directories. The Maildir root is the logical mailbox; `cur`, `new`, and the
  physical message filename are provenance, not mailbox-name components. A
  Maildir at a mounted volume root uses the mount directory name, or `Maildir`
  for an unnamed filesystem root, so its logical mailbox remains selectable.
  Local source-file fingerprints and append checkpoints use the physical file
  bytes, including `.emlx` trailing metadata even though it is not message data.
  Files whose exact basename is `Info.plist` or `table_of_contents`, or whose
  suffix is case-insensitively `.toc`, are known mailbox-container metadata and
  are silently omitted from discovery; they do not produce skipped-input
  reports or counts.
  Directory traversal must surface missing paths and permission failures rather
  than silently treating an unreadable mailbox as empty. Direct access to
  `~/Library/Mail` may require Full Disk Access for the invoking application.
* `.emlx` input uses its leading decimal byte count to select exactly the
  RFC 5322 message; Apple's trailing plist metadata is not part of the
  message hash or output. Apple `.partial.emlx` input is rejected because its
  attachment payloads are detached and reconstructing a message would not
  preserve the original RFC 5322 bytes. Apple Mail databases, plist files,
  attachment directories, and `.emlxpart` fragments are not separate messages.
  For an Apple Mail cache, the logical mailbox path ends at the deepest
  `.mbox` package, with each `.mbox` suffix removed. Account and parent mailbox
  components remain in the path; internal UUID, `Data`, numeric bucket,
  `Messages`, and `.emlx` filename components do not.
* Generic read-only IMAP is the first live importer. The standalone CLI supports
  passwords/app passwords on services permitting them, including eligible Gmail
  accounts. Microsoft 365/Outlook.com OAuth remains planned, not delivered by the
  password CLI. A later Gmail API adapter may provide richer history metadata and
  a rolling `--days N`
  mode using Gmail's `newer_than:Nd` query. Google Takeout MBOX is supported as an offline,
  one-time baseline input and remains an offline end-user path; personal Takeout is
  not assumed to be programmatically triggerable. Direct multi-part Takeout ZIP
  ingestion remains future work, so current users extract every part and ingest
  their common parent directory. `doc/GMAIL.md` separately identifies end-user
  instructions and developer-only OAuth, verification, assessment, and IMAP
  decisions.
* IMAP ingest supports TLS and authenticated account configuration, records
  account/folder/UID provenance, and retrieves RFC 5322 bytes without marking
  messages read or modifying the remote mailbox.
* Outlook `.pst` and `.ost` ingest does not require Outlook to modify or export
  the source. The adapter records the parser/converter and version, enumerates
  every encountered store item, preserves folder and item identifiers as
  provenance, and reports corrupt, deleted, partial, or unsupported records
  rather than silently omitting them. The current backend decision, fixture
  matrix, and format limitations are maintained in
  [ON_DISK_MAIL_FORMATS.md](../ON_DISK_MAIL_FORMATS.md).
  The implemented Rust PST helper is a standalone ingest executable accepting a
  filename and emitting mboxrd to stdout, with diagnostics on stderr, as
  specified in [PST_IMPORTER.md](../PST_IMPORTER.md#executable-stream-contract).
  Use Microsoft's Rust PST library for PST; the independent external libpff
  converter remains the OST reader. Each emitted record carries `X-Imported-URI`,
  `X-Importer-Name` and `X-Importer-Version`. These fields are included in h2.
  Existing h3 includes selected headers AND the encoded MIME body; it is a
  comparison control, not permission to discard conflicting variants. The
  current Message-ID-plus-h2 dedup does not collapse different importer headers.
  Preserve failures and partial-run provenance; a successful empty stream is
  not proof that an arbitrary PST was fully recovered. Qualify OST separately.
  Microsoft 365 has no platform-neutral Takeout equivalent. Outlook PST export
  on Windows is an acquisition path; OLM export from legacy Outlook for Mac
  requires a future reader. The current checkout imports PST and OST with the qualified backends
  described below. OLM, Graph and Exchange Online IMAP remain unavailable. `doc/M365.md` must keep that boundary explicit.
* Complete Apple Mail EMLX records are supported as local input. Partial
  records and detached content do not establish provider completeness; general
  offline cache-layout adapters remain planned. See
  [APPLE_MAIL_CACHE.md](../APPLE_MAIL_CACHE.md) for the source-safety boundary.
* The Apple Mail/archive comparator is strictly read-only. It indexes complete
  `.emlx` records in a disposable database, opens the archive catalog
  read-only, and classifies exact raw, semantic-only, cache-only, archive-only,
  and ambiguous matches using semantic-message version 1 (`h3`). It compares
  header names and DKIM-relaxed values only for semantic-only pairs, outputs no
  header values or message content, reports excluded partial and unreadable
  records, and warns when the active Envelope Index WAL changes during the
  scan. Provider metadata must be read from a private database/WAL snapshot:
  SQLite must not open the source Envelope Index or create/change its sidecars.
  Changes detected while copying the snapshot must fail with a retry diagnostic.
  Its privacy-preserving service breakdown classifies Gmail from the
  special mailbox hierarchy, Microsoft Exchange from Apple's EWS scheme, and
  retains other IMAP, POP, local, and unknown stores separately without
  reporting account addresses or opaque identifiers. It must never treat `h3`
  alone as authorization to merge or delete a canonical source variant.
* The ambiguous-h3 review exporter selects a requested number of distinct
  equivalence classes deterministically by descending archive-variant count,
  descending cache-occurrence count, and h3. For each selected class it copies
  every matching complete cache occurrence and every matching hash-verified
  canonical representation without changing either source. It refuses to
  overwrite an output directory and writes private owner-only EML files,
  per-case manifests, a root manifest, and a CSV/Markdown index. Reports assign
  identical raw `h2` values to explicit equivalence groups, identify groups
  shared across cache and archive, compare the canonicalized `Date` and
  `Subject` components used by `h3`, and report `X-Apple-Auto-Saved` per file.
  Existing reports can be refreshed only after every exported file passes both
  its recorded `h2` and case `h3`; source messages are not needed for refresh.
  Private messages belong only in gitignored checkout-root output and must never be
  committed as fixtures or documentation.
* Every source adapter emits original RFC 5322 bytes where the source contains
  them. When a proprietary store requires reconstruction or conversion, the
  observation records that fact and the responsible tool/version; reconstructed
  output is never represented as byte-identical to a source RFC 5322 record.

## MCT Importer API and Rust programs

[MCT Importer API Version 1.0](../MCT_IMPORTER_API.md) defines filename input and
mboxrd stdout with `X-Imported-URI`, `X-Importer-Name`, `X-Importer-Version`.
No h4 is required: h2 covers every header and body byte; existing h3 excludes
top-level importer fields while covering selected headers and the encoded body.

Rust/Cargo are required for building the importer tools and standalone PST import
helper. Packaged end users need the helper executable, not a Rust compiler.
`make rust-programs` builds `mdti-validator`, `mcti-generator` and `pst-importer`; each has its
own same-named Makefile build target. Keep a committed Cargo lockfile and run
Rust formatting, Clippy and tests through Makefile targets, including `make check`.
`make pst-import PST='/path/archive.pst'` emits only mboxrd on stdout and
diagnostics on stderr. Both `pst-import` and `pst-smoke` must reject an omitted,
empty, missing, unreadable, or non-file PST path before building or extracting,
with an actionable diagnostic on stderr and no stdout output.
The validator warns before consuming stdin that all input is discarded, reports
validation errors on stderr, and reports valid complete message counts after
EOF. It never imports mail or creates archive files. The generator accepts an
unsigned count and emits exactly that many deterministic RFC 2822/MIME text
messages with counters and valid API provenance. Validate malformed records,
partial EOF, size limits, MIME structure/encoding, quote levels, process exit
statuses and recovery to following records, using actual Rust processes.
The standalone [PST importer](../PST_IMPORTER.md) uses Microsoft's pinned
`outlook-pst` crate through its explicit read-only reader API. Emit validated
mboxrd records with stable node-ID URIs, exact by-value attachment data,
reconstruction evidence and source SHA-256 checks. Continue after recoverable
item failures but return nonzero for any incomplete extraction.
Use one resolved timestamp for the reconstructed RFC `Date:` header and the
synthetic mboxrd `From pst-importer` delimiter: a valid transport `Date:` first,
then MAPI submit (`0x0039`) or delivery (`0x0E06`) time, then the Unix epoch.
Format the delimiter in UTC using the English ctime form.
Both PST exporters accept `--offset N` and `--limit N` for a stable range of
normal-content items before class filtering. They traverse folders and item
node IDs in ascending numeric order, stop after the requested range, and report
both encountered and selected counts. Their reconstructed `Date:`, `From:`,
Subject, and Message-ID fields use the same normalization policy; invalid
Message-IDs are omitted. Unknown non-ASCII MAPI body code pages retain their
bytes and use the Windows-1252 fallback charset.
Both retain non-content transport headers as readable, safely folded UTF-8
fields without RFC 2047 re-encoding. Reconstructed MIME text charsets are
unquoted, and attachments use matching Content-ID, disposition, and RFC 2231
filename forms.
Both identify a PST node in `X-Imported-URI` with the shared
`#item=<numeric-node-id>` fragment.
Before emitting mail, scan normal contents throughout the entire PST folder
hierarchy for Contacts, including nested folders and folders outside the IPM
mail subtree. Retain all populated contact email slots in memory, not bodies
or photos. Resolve all three email slots through the store's named-property map
using PSETID_Address and their LIDs; never treat LIDs as fixed property IDs.
Resolve Exchange legacy distinguished-name addresses using unambiguous DN/SMTP
pairs from contacts, sender, represented-sender, or recipient properties in the
same PST. Contact address-book EntryIDs can supply explicit DN aliases.
Match DNs case-insensitively; do not guess addresses or resolve conflicting pairs.
Retain the original DN and the mapping's source item/property in generated headers.
Apply the directory to From and reconstructed To/Cc/Bcc, and to entire native
addresses or angle-bracket addresses in existing sender/recipient, reply,
resent and return-path headers. Never substitute text inside display names or
comments. Preserve existing SMTP addresses and original transport-header bytes.
Report address-book scan counts and failures; unreadable lookup objects prevent
success, with failures also encountered during mail extraction counted once.
Otherwise retain native `EX` identities, bare or in an angle address with their
source display name, without rejecting readable messages. Mark these with
`X-PST-Sender-Address-Type: EX`; the archive stream validator
must accept that explicit representation while rejecting malformed identities.
Decode subject encoded-words and emit readable UTF-8 Subject values. Remove
MAPI's leading marker and prefix-length character, retaining textual prefixes
such as `Re:` and `Fw:`. Preserve safe folding and reject header injection.
For MAPI Internet code page 1256, retain the original String8/binary body bytes
and declare `charset=windows-1256` in the reconstructed MIME part. Unknown non-ASCII code pages retain their bytes and use `charset=windows-1252`.
Meeting requests and responses (`IPM.Schedule.Meeting` and subclasses) are
silently excluded, counted as non-mail, and never cause a warning or error.
Delivery reports (`REPORT.IPM.Note` and subclasses) are ordinary imported mail.
Identify underlying PST reader failures explicitly as library errors.
`--only-invalid` shall emit diagnostic mboxrd records only for failed items whose
message properties can be read. Preserve available transport headers and body
property bytes, label synthetic diagnostic headers, and retain failure status.
When reconstruction reached the header/body separator, also retain the exact
reconstructed header block, including invalid values and folding, separately
from original transport headers and diagnostic envelope headers.
Unreadable items remain library errors without invented message content.
Exercise deterministic recovered-email construction, decoded body/attachment
handling, partial-run accounting, read-only-source behavior, changed-source
detection, and producer/consumer failures with public, licensed PST fixtures.
PST recovery is complete for the project's email-collection scope; calendar,
contacts, virtual search folders, configuration, and file carving are not
collection requirements.
The CLI archive host is implemented; cross-importer h3 duplicate suppression
remains planned.
PST and OST share a storage-format family, but OST extraction is explicitly
best effort. Do not infer a format from a changed extension or relaxed signature
check. Use the external libpff converter for OST; report what was recovered from
the cache separately from server-mailbox completeness. See
[PST/OST scope and current limits](../PST_IMPORTER.md#relationship-between-pst-and-ost).

## PST test corpus downloader

The Rust `pst/pst-downloader.rs` tool reads the supplied `pst/*.json` inventories
and acquires direct PSTs and PST members of ZIP/7z archives under ignored
`var/pst/`. Use [the downloader contract](../../pst/README.md) for schema and limits.
Validate supplied SHA-256/lengths and basic PST signatures on completed files
before publishing them to the cache; retain original
artifacts and URL/member provenance, deduplicate only byte-identical content,
and verify cache reuse without replacing corrupt evidence. Download/extraction
is bounded and streaming; failures remain visible and prevent success while
later sources can proceed. Dry-run and ordinary tests must not download corpora.
Ctrl+C must cancel pending HTTP waits, release the cache's OS lock, and exit 130.
After process death, a rerun acquires the released lock and reuses verified
downloads. Preserve any older artifact missing its receipt before downloading
it again. These development-download rules do not add automatic API ingest restart.

## External OST import

Read OST through the standalone converter using pinned `libpff-python`, with a
read-only handle and before/after SHA-256 checks. Route genuine `SO` client magic
to OST regardless of extension; `SM` files remain PST even when named `.ost`.
Retain folder/node provenance, receipts, and partial-item diagnostics. OST is a
cache: extraction does not establish server-mailbox completeness. Omit an item
when an embedded MAPI message cannot be reconstructed; fail the run as
incomplete while retaining its source reference and diagnostic.
Never fetch external attachment references. Exclude search folders and non-mail
objects explicitly; unknown MAPI classes must report incomplete extraction rather
than silently count as non-mail. Stream attachment reads, verify their declared
lengths, bound reconstructed message sizes,
and enforce a host-side process deadline and output/diagnostic limits. Kill and
reap an overdue converter. The host must never import pypff. The converter owns
no archive/database state. It emits standard mboxrd; source hashes are permitted,
but canonical message hashing and deduplication stay in the host. For scanned
imports, pass the converted stream through the external Rust mcti-scan executable
before API admission. Only infected messages gain the three ClamAV headers.

There is no reader-selection UI or PST reader setting. The Rust helper is the
only PST importer. `plugins.pst` configures only its executable and resource
limits. `plugins.ost` independently configures the libpff reader for OST.
`make test-pff` exercises a genuine OST fixture, the Rust PST reader, partial
results, source fixity, limits, repeat deduplication, and CLI search.

### Retired desktop experiments

The retired desktop has no current delivery requirements. Its historical scope
and acceptance evidence are in [RUST_GUI_EXPERIMENTS.md](../RUST_GUI_EXPERIMENTS.md).
The independent Rust tools retain their requirements above: preserve hash-selected
bytes, borrow unchanged MIME text, bound transfer-decoder storage and batch
quoted-printable output. Embedded messages may require retained decoded content.
Tests must establish exact bytes, malformed-input behavior and bounded callbacks.

## Owner-thread source publication

Source pool threads retain each source iterator and thread-bound input handles.
They submit typed publication operations as tracked futures; the owning driver
alone mutates shared catalog, processing and search state and publishes
MBOX checkpoints. Driver failure releases every queued waiter before joining
workers. Progress retains the originating worker identity, and processor waits
continue publishing status heartbeats, including one-worker message dispatch.
No publication mutex is needed.
