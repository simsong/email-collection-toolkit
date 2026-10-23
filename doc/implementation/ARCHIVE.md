<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Archive implementation

Current implementation and validation boundaries. Deferred feature designs
live in [plans](../plans/README.md); historical evidence does not validate this revision.

## Portable verification and checkpoint publication

`standalone_verify.py` is itself limited to the Python standard library. At
ingest startup it copies its own source atomically to
`verify_mail_archive.py` in the bag root. The installed script accepts only the
native format in [INTEGRITY_CONTROLS.md](../INTEGRITY_CONTROLS.md). It validates
safe BagIt payload and tag manifests, required Mailbag metadata and CSV rows,
then streams the JSON declarations, complete-MBOX `h1` hashes,
recovered-message `h2` hashes, and semantic-message `h3` hashes. It returns
nonzero on missing, orphaned, malformed, unsupported, unsafe, unlisted, or
mismatched files. It neither imports the package nor reads SQLite.
Its module docstring doubles as formatted `--help`, carrying standalone run
instructions and verification limits into the installed archive copy.
The optional `archive` argument selects the bag root; omission uses the script's
directory even from another working directory. The stdlib reporter flushes file
paths to stdout before processing, and renders per-pass byte/message bars on
stderr. Updates are throttled to 0.2 seconds on terminals (in place), or five
seconds when redirected, with initial/final updates. `--quiet`/`-q` suppresses
paths, bars, and success messages while retaining errors. The CLI catches
`KeyboardInterrupt`, reports incomplete verification, and returns 130 without a
traceback. `make test-bagit` exercises installed-script output, quiet failures,
default-directory selection, and real POSIX SIGINT. Existing archive-local
copies retain their old behavior until refreshed by normal archive publication.

`write_bag_checkpoint()` streams catalog locations in MBOX byte order through
`write_integrity_files()` and
uses each catalogued raw SHA-256 to verify mboxrd decoding and resolve legacy
mboxo `>From ` ambiguity. It then
atomically writes deterministic JSON control records followed by the TSV table.
The initial declarations are `h1` (complete MBOX, SHA-256), `h2` (recovered
RFC 5322 bytes, SHA-256), and `h3` (semantic-message version 1, SHA-256).
Semantic version 1 applies DKIM relaxed header and simple body canonicalization
to the selected stable/delivery headers documented in `INTEGRITY_CONTROLS.md`;
it includes `Delivered-To` and excludes mutable `Status` and `X-Status` fields.
The same pass emits RFC 4180 Mailbag rows through a Pydantic `MailbagRow`, so
generation does not reread the corpus to count MIME attachments. The payload
manifest reuses the computed complete-MBOX hashes. `bag-info.txt` retains its
external identifier while refreshing its timestamp and payload oxum, and the
tag manifest is written last.

## Technology decision

The normalizer uses Python 3.12+, streaming I/O, RFC 5322/MIME parsing,
SQLite/FTS5, verified-TLS IMAP and embedded ClamAV workers. Gmail API/OAuth
remains a separate acquisition plan.  The observed local corpus
(about 52 GB and 1.47 million input messages) is a streaming workload.

Use the standard-library `mailbox.mbox` reader and writer.  Pass raw `bytes`,
not parsed `Message` objects, to `mbox.add()` so that MIME serialization is
never rewritten. `mboxrd.quote()` supplies reversible quoting before
`mailbox.mbox` handles locking and container writes; Python alone writes mboxo.
Capture pre-append and post-flush file offsets for verified retrieval.  Use the standard `email` package only for header/MIME parsing while
retaining original RFC 5322 bytes for identity hashing and output.

Line-ending policy is tolerant reading with byte-preserving storage: accept LF
and CRLF without converting either representation, and retain lone CR content.
CR characters in emailed program output can be terminal controls, so their
presence alone does not identify a newline convention. The raw integrity hash
continues to describe the stored message bytes; semantic-hash normalization is
a separate derived calculation, not permission to rewrite mail. `PreservingMbox`
extends the standard-library container with platform-independent separator
indexing and framed-byte publication. It is used by source ingestion, archive
publication, index rebuilding and standalone verification on both platforms.
This bypasses the standard-library Windows newline translation; passing bytes
alone to an unmodified `mailbox.mbox` is insufficient.

Use SHA-256 as the canonical hash.  A local OpenSSL 3.6.3 benchmark on this
Apple Silicon host measured 2.74 GB/s for SHA-256 on 16 KiB blocks, versus
1.61 GB/s for SHA-512 and 0.98 GB/s for SHA3-256.  Do not require BLAKE3: it
would add a dependency and is less portable for long-term verification.

## MBOX envelope preservation

`SourceMessage.mbox_envelope` and `MailObject.mbox_envelope` carry one complete
source delimiter as bytes, separately from `raw`. The MBOX adapter reads the
physical line at the source offset to retain CRLF as well as LF. The plugin
boundary, normalization guard and legacy status-wrapper reader share one complete-envelope check, rejecting
embedded line breaks, repeated CR, incomplete lines and nonliteral From prefixes
before framing can enter an X-From header. `mbox_framing.normalize_mbox_framing`
converts one immediate quoted delimiter into a literal `X-From:` header. If the
outer sender is exactly `XXX` or `???@???` and the inner sender is neither,
it instead promotes the inner envelope and writes the outer value as `X-From:`.
All following headers/body bytes are unchanged. MBCP metadata exclusion checks
use the selected envelope and the original payload after the recognized quoted
line, ignoring only the generated X-From field; original X-From fields or body
content still prevent metadata exclusion. Ordinary RFC/MIME readers then
work without a virtual-header workaround. Canonical SHA-256 and semantic hashes
describe the normalized message; their algorithms and standards are unchanged.
`MboxNormalization` crosses the source/plugin boundary and is appended as JSON
to each observation's detail, retaining original source-payload SHA-256 and both
framing lines (base64). This evidence plus normalized content reconstructs the
source adapter's original payload; whole-file source hashes still cover source
files themselves. Source mailboxes and existing archives are never rewritten.
The separate legacy XXX status-header wrapper still unwraps its nested envelope,
but requires nonempty, well-formed status-only headers and a complete quoted
ctime delimiter; malformed, indented, unquoted or prose candidates are retained. This
prevents scanning through a real message's headers into its quoted body.
Publication prepends the physical envelope to the raw bytes passed to
`mailbox.mbox.add`; outside the explicit normalization above, raw hashes and
duplicate identity remain unchanged. Leading
Babyl/EML envelopes already in `raw` remain adopted when no separate envelope
exists. No message is reserialized.

When framing is absent, `synthetic_envelope` chooses the maximum valid UTC
Date/Received/Resent-Date/Delivery-Date timestamp, using the existing plausibility
policy, including `--earliest-year` (default 1900). This is separate from the trimmed Received
median used for year routing. Missing header dates use the resolved message date
from source/prior/path metadata; direct low-level calls without that context use
1970-01-01 UTC. English weekday/month names are locale-independent. These
fallbacks are estimates, not recovered delivery evidence. Original envelopes
are preserved even when their date is malformed or disagrees with the headers.

`make test-envelopes` checks actual plugin/ingest/reimport publication, physical
LF/CRLF delimiter preservation, original SHA-256 and independent bag verification,
header date ordering/time zones, body exclusion, deterministic missing-date
fallbacks, and recovery of quoting, missing final newlines and leading envelopes.
It also exercises immediate double framing with Unix-style and Eudora-style
placeholder senders, LF/CRLF, normalized hashes, original source hashes, intact
body quoting, literal X-From display,
legacy status wrappers, and duplicate-free reimport.
This fixes future publication only. Existing archives require a separately
approved source-backed rebuild or repair that regenerates location offsets,
integrity tags and manifests. Ordinary duplicate-skipping reimport is not repair.

`ingest_diagnostics` adds exception notes at local `MailObject` validation and
worker processing boundaries, then formats the complete traceback into both
`ingest_runs.detail` and status JSON `failure_detail`, displayed by Ingests.
Pydantic field errors retain their underlying validator traceback when available.
Validation summaries and chained tracebacks omit Pydantic input values, relying
on the bounded previews for input evidence.
Notes use a neutral source-cursor label for native plug-in/remote cursors.
Source identity fields and cursors are each limited to 1,024 characters plus a
truncation marker. Arbitrary source provenance/hierarchy is not serialized into
either message or container failure notes.
Exception summaries are capped at 2,048 characters, individual notes at 32,768,
and the rendered report at 65,536, plus explicit truncation markers. Parse-error
wrappers include the message hash; source identities/cursors appear only in
bounded context notes rather than again in an unbounded exception message.
They contain the source reference, cursor, SHA-256, byte length, and escaped
prefixes bounded to 4,096 message bytes and 512 bytes per selected/original/quoted
envelope; no frame locals
or full-message copies are collected. Source lookup uses the recorded local
path and byte offset; remote adapters retain their native reference/cursor.
Before advancing a generator, the worker clears its current-message context so
iterator failures cannot blame the previous email. `make test-envelopes` includes
real plug-in validation after a successful message, generator failures, and
date-resolution failures, checking durable status/catalog evidence and source
immutability. The diagnostics remain local and can include private message text.

The normative MBOX transformation and normalized/source hash reconstruction
contract is in [INTEGRITY_CONTROLS.md](../INTEGRITY_CONTROLS.md#verifying-and-reconstructing-normalized-records).

## Native Mailbag storage

[Mailbag 1.0](https://archives.albany.edu/mailbag/spec/) is the native archive
layout, implemented directly without a `mailbagit` runtime dependency. The bag
root contains operational SQLite tag files, while canonical mboxrd files are
the only payload under `data/mbox/`. Rich per-message declarations live in the
top-level BagIt tag directory `integrity/`. The exact interoperability contract
is specified in [INTEGRITY_CONTROLS.md](../INTEGRITY_CONTROLS.md).

BagIt manifests describe complete files; mailarchiver declarations add exact
RFC 5322 recovery and semantic hashes for each ordered message. The MBOX
SHA-256 is deliberately present in both `manifest-sha256.txt` and the
corresponding integrity tag. `mailbag.csv` connects each message to its MBOX,
records its attachment count, and assigns a stable package identifier derived
from normalized Message-ID plus raw SHA-256. It splits at Mailbag's 100,000-row
boundary.

The archive is appendable, so a write can temporarily invalidate the preceding
manifest. `write_bag_checkpoint()` closes that interval by writing integrity
tags and Mailbag CSV first, the payload manifest from already computed MBOX
hashes, `bag-info.txt`, and the tag manifest last. Successful completion,
controlled interruption, and publication recovery all use this path. A
validator never treats an in-progress mismatch as valid.

`bag-info.txt` gets `Mailbag-Agent-Version` from installed package metadata,
the same version source used by the application. PyInstaller includes that
metadata with `--copy-metadata mailarchiver`; historical fixture bags retain
their original writer-version declarations.

`archive.sqlite3` and `search.sqlite3` remain outside the tag manifest. This is
intentional: SQLite journal state is operational rather than portable BagIt
fixity, and the search database is disposable. The tag manifest instead covers
all BagIt/Mailbag metadata, every integrity tag, and the installed verifier.

`mailbagit` remains useful as an independent interoperability check and for
future derivative packaging when it can operate without moving or
reserializing canonical files. It is not the ingestion engine. Restricted or
redacted releases are separate bags; PDF and WARC derivatives remain opt-in
and sandboxed because rendering message HTML can contact remote resources.

## Deduplication and semantic reconciliation

Email Collection Toolkit separates exact storage identity from semantic reconciliation.
The admission-time deduplication key is the tuple of normalized `Message-ID`
and `h2`, where `h2` is SHA-256 over the source adapter's recovered RFC 5322
bytes. When `Message-ID` is absent, the raw digest supplies the stable fallback
identifier. This conservative rule makes repeated ingest idempotent and stores
a byte-identical message found in multiple backups or caches only once, while
retaining every source observation. It does not collapse messages merely
because they reuse a `Message-ID`, nor does it discard a source variant whose
raw bytes differ.

The `h3` semantic-message version 1 digest provides the second identity layer.
Its byte stream begins with a versioned domain separator, followed by selected
headers under RFC 6376 DKIM-relaxed canonicalization, one CRLF, and the complete
MIME body under DKIM-simple body canonicalization. Repeated selected headers
are processed from the physical bottom upward in this fixed order:

```text
From, Sender, Reply-To, To, Cc, Bcc, Delivered-To, Date, Message-ID,
Subject, MIME-Version, Content-Type, Content-Transfer-Encoding,
Content-Disposition
```

`Delivered-To` distinguishes deliveries. Mutable client and transport fields,
including `Status`, `X-Status`, `Received`, `Return-Path`,
`Authentication-Results`, `DKIM-Signature`, and other unselected headers, do
not affect `h3`. The entire encoded MIME body—including attachment encodings
and nested MIME headers—does affect it. Thus “normalized header hash” is useful
shorthand but technically incomplete: `h3` is a domain-separated,
canonicalized whole-message digest. `h2` remains the authority for exact byte
identity; `h3` identifies a relationship for investigation and never by itself
authorizes a merge or deletion.

## Database design

`archive.sqlite3` uses foreign keys, explicit transactions, and a schema
version table. Its complete V1 DDL lives in the
packaged `sql/V1__archive.sql` resource rather than an inline Python string.
Principal relations are:

```text
email_addresses(address_pk, address UNIQUE)
messages(message_pk, message_id_normalized, sha256, sender_address_pk, subject,
         date_utc, date_source, category,
         UNIQUE(message_id_normalized, sha256))
recipients(message_pk, address_pk, role)
mbox_generations(generation_pk, filename, sha256, message_count, byte_count)
locations(message_pk, generation_pk, byte_offset, byte_length)
source_volumes(source_volume_pk, identity_json, metadata_json,
               first_observed_at, last_observed_at)
source_files(source_file_pk, source_volume_pk, source_plugin, work_id,
             source_path, hierarchy_path, metadata_json, path_kind, source_kind,
             modified_at_ns, byte_length, sha256, checked_at, completed_run)
source_integrity_checks(integrity_check_pk, source_file_pk, run_pk, control_id,
                        subject_id, action, resume_cursor, reason, started_at,
                        completed_at)
source_integrity_evidence(integrity_check_pk, ordinal, control_id, subject_id,
                          evidence_kind, algorithm, value, byte_length)
observations(observation_pk, source_file_pk, source_offset, source_cursor, raw_sha256,
             semantic_sha256, message_pk, disposition, run_pk, detail)
metadata_defects(message_pk, field, detail)
ingest_runs(run_pk, started_at, completed_at, result, detail)
```

`source_volumes.identity_json` is a canonical stable identity and
`metadata_json` retains the complete current OS/provider report. The historical
table names now represent source origins and containers: local files use a
volume-relative path and `path_kind='file'`; provider plug-ins use their
account/container identity and `path_kind='provider'`. Per-container
`metadata_json` preserves display name, hierarchy, and non-secret provenance;
`hierarchy_path` is the normalized path used by mailbox-tree filtering. The
metadata also carries a typed direct/cache relationship, upstream plug-in kind,
and optional account hint without replacing the local source-origin identity.
`work_id` is scoped by plug-in and source account and binds a container to its
messages; observations preserve both an opaque
source cursor and an optional numeric position. `message_pk` is
nullable in `observations` so malformed and autosave-excluded source records
are still reviewable. Source integrity attempts are append-only; only a
completed check is eligible as prior evidence, so a failed run cannot advance
a checkpoint. The legacy file SHA/check/run columns remain a local display
cache, not the authority for source decisions. Each observation directly stores raw (`h2`) and semantic
(`h3`) SHA-256 values for fast forensic lookup. The deduplication lookup is indexed on `(message_id_normalized, sha256)`;
`messages.sha256` has a separate index for the missing-Message-ID exception and
FTS result lookup. Thus repeated ingestion and byte-identical cross-source
copies are idempotent, while Apple- or transport-rewritten records with only an
`h3` match remain distinct canonical evidence. Do not make
Message-ID unique.  `email_addresses.address`, `messages.sender_address_pk`,
and `recipients.address_pk` are indexed. Recipient role preserves To, Cc, or
Bcc; ordering within a header is not preserved. The catalog also indexes
`(message_pk, role, address_pk)` for scoped address filters and
`(date_utc DESC, message_pk DESC)` for
bounded search pages, `(source_file_pk, source_offset DESC)` for ingest resume,
`(source_file_pk, source_cursor)` for provider cursor lookup,
`(run_pk, observation_pk)` for run review, and
`(generation_pk, byte_offset, byte_length)` for ordered, covering location
reads. Category/date and category/sender indexes support reports, rebuilds, and
owner-address suppression. Expression indexes on case-folded subject and email
address support alphabetical result pages. Earlier single-column and
forensic-hash indexes remain present.

Query-plan acceptance tests cover ingest identity, latest completed typed
integrity evidence and checkpoint lookups,
resume, run review, provenance, MBOX integrity traversal, category/date reports,
all three result sort modes, and FTS-to-catalog SHA-256 joins. Search explicitly
selects the matching date, subject, sender, or SHA-256 index for its bounded
candidate stage. Index rebuild walks the unique mailbox-name index and covering
location-order index, avoiding a message scan and temporary sort. Full scans
remain only where the command intentionally consumes the whole result set,
such as unfiltered review, complete checkpoint generation, and aggregate
reports; grouping those complete results may still require temporary B-trees.
Leading-wildcard `to:`, `from:`, and `subject:` substring predicates cannot use
a selective ordinary B-tree, but their bounded traversal and relational joins
remain indexed.

## MBOX mechanics and sorting

Input detection must validate a stream rather than trust filename extensions.
The MBOX reader recognizes separator lines and decodes declared `.mboxrd`
sources once. Unknown-dialect sources retain stored quoting except for explicit
legacy framing rules; unescaped body delimiters remain structurally ambiguous.
Physical `get_file()` reads avoid `get_bytes()` newline translation. It also
accepts a first separator within the first 16 lines when the separator has a
classic ctime timestamp and an RFC header block follows. This recovers short
terminal-capture preambles without claiming later `From ` text in documents.
An MMDF `0x01 0x01 0x01 0x01` line may frame such an MBOX record; opening and
closing control lines are source-container bytes, not RFC 5322 message bytes.
The packaged YAML supplies both the prefix byte budget and preamble line limit.

Writers produce an envelope `From ` line plus mboxrd-escaped message bytes under
`data/mbox/`.
They track the byte offset and byte length of each complete record.  Output
uses the highest numbered part for each year/category. Under the existing writer
lease, the publisher selects a new part when the exact framed record would bring
a nonempty part to or above `config.yaml`'s positive `mbox_max_bytes` limit
(default 4026531840). The publication journal records the selected part before
append; normal rollback removes an uncommitted newly created part. Oversized
individual records remain whole in their own parts. This applies equally to
Sent, Archive, INFECTED and attached-message publication. `make test-rollover`
checks 20 KiB rollover with four 6 KiB records, restart/deduplication, framing
boundaries, oversized records and orphan recovery without large test files.
The directory contains no nested
per-message files.
For any original message lacking a final line break, standard MBOX contributes
one before its record separator. Direct retrieval considers the stored form and
the form with one writer-added final line break removed, selecting only the
candidate matching the catalogued original SHA-256. This includes the
zero-byte-message case. The implementation hashes the complete stored candidate
first, then tries removing one terminal LF and one terminal CRLF in that order;
it fails closed if no candidate has the expected hash. The MBOX-level hash still
covers every stored byte.
The previous standard-library mboxo writer's `>From ` representation is ambiguous
when the source already contained a literal `>From ` line. New writes prequote
all `^>*From ` payload lines with one `>` and are reversible. Recovery tries
one mboxrd decoding first, then a bounded set of legacy interpretations, selecting
only the candidate matching
the authoritative raw-message SHA-256. Candidates are yielded once and not
retained as a second in-memory copy of the message set; unresolved
high-ambiguity input fails closed. A second ambiguity occurs when source bytes
begin with `From `: `mailbox.mbox` promotes that source line to the record
separator. Recovery tries payload-only first and then the stored separator plus
payload, applying the same quoting and terminal-line-break candidates to both.
The installed stdlib verifier uses the same bounded interpretation order
independently. See the per-container transformation ledger in
[INTEGRITY_CONTROLS.md](../INTEGRITY_CONTROLS.md).

## Manual state and documentation status

The [manual-state requirements](../requirements/ARCHIVE.md#manual-state-and-documentation-status) define the three databases and the backup boundary.

## Schema initialization and validation

SQL filenames follow the Flyway convention `V<version>__<description>.sql`;
only the naming convention is used. No Flyway runtime, Java, or JDBC is required
for schema management. Python loads the packaged SQL with `importlib.resources`
and executes it through the standard-library `sqlite3` module.

The authoritative current catalog schema is packaged as `sql/V1__archive.sql`.
It is initialized only for an empty database; unversioned databases and schema
versions other than V1 are rejected rather than migrated. Because an earlier
development schema also used the V1 label, startup validates the required
tables, columns, and named indexes before accepting an existing database. This
is validation of the current layout, not an upgrade mechanism. Application-run,
developer-written catalog migrations remain a [separate plan](../plans/ARCHIVE_EXTENSIONS.md#automatic-catalog-migrations);
the existing `schema_info` version check does not implement those safeguards.
`locations` and
`mbox_generations` are written as part of each message publication.
