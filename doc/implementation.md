<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Mail archive normalizer implementation

## Embedded antivirus migration status

[PR #124](EMBEDDED_CLAMAV.md) uses libclamav directly from Python and Rust.
The Python session shares one compiled engine across concurrent native threads
in a temporary worker process. Source workers scan before the publication lock,
and Python records results in the existing processing database. Rust adds
X-ClamAV-Detection, X-ClamAV-Engine-Version, and X-ClamAV-Definitions-Version
only to infected messages. Python reads those headers to quarantine the emitted
RFC message without rescanning. Clean API messages have no scan provenance.
Per-message hashing and deduplication belong to Python; there are no scan receipts
or additional database schema. Operators handle failures by re-importing.

About displays definition dates and an amber recommendation after three calendar
months, with explicit FreshClam refresh into immutable per-user generations.
Failed updates cannot replace active definitions. Project-owned Python, Rust,
tools, documentation, and website code use GPL-2.0-only, with additional licenses
available. The independent converter code uses GPLv3; upstream pypff/libpff
retains LGPLv3-or-later without relicensing. The host is GPLv2-only, whose linking
compatibility differs from GPLv2-or-later; see the
[licensing explanation](../THIRD_PARTY_NOTICES.md#gplv2-and-lgplv3-compatibility).
ClamAV uses GPLv2;
the remaining Apache-2.0 ftfy dependency needs compatibility resolution
(see THIRD_PARTY_NOTICES.md).
`make freshclam` updates ignored etc/clamdb, seeding it from
an installed database if available. The DMG bundles this copy, the engine and
FreshClam; release CI refreshes the definitions first. Its mounted self-test runs
real clean/EICAR scans using the bundled definitions. Application releases refresh
bundled definitions at least quarterly.
Before signature/dependency checks and the mounted self-test, the DMG validator
writes `dist/<DMG stem>.contents.json` with relative paths, sizes, and symlink
targets for every mounted file/link. The release job uploads this inventory with
its packaging reports even if validation fails. The release workflow passes
`--log-dmg-contents` to the builder, which prints every entry after mounting
and before executing the installed app. It separately checks for the
bundled `libclamav.dylib`; if present but unloadable, the scanner reports the
native loader's underlying error (PyInstaller's generic wrapper hides it).

## CI and release validation

All workflow jobs use macos-15. Release packaging invokes make dmg, which
mounts the candidate and runs its headless installed-app self-test. Native GUI
release checks remain an explicit local make check-release action. Pages uses
the same pinned Darwin Zola binaries/checksums as application CI.

## Manual state and documentation status

The archive has three operational databases: `archive.sqlite3` (catalog and
source observations), `search.sqlite3` (disposable search), and
`processing.sqlite3` (queues, evidence, identities, affiliations and tags).
Manual identity/tag decisions are durable user data, not reproducible from mail.
Back up the entire archive, including databases and `config.yaml`.
Current source code includes CLI and GUI processor integration and local PST/OST
adapters. Research plans and historical release inventories are not promises
of shipped features; authoritative matching, the tag editor, remote ingestion,
geography and compiled-platform trials remain future work.

## Recovered tool safety and validation

Native initialization has a 120-second deadline; production scans retain a
60-second deadline. A stuck/crashed native worker fails pending scans and is
reaped. Owned extraction/message temporaries are removed. Scan threads persist
typed evidence directly, and processor invocation history retains it too.

`make h3-ambiguous-review ARGS='--apple-mail SOURCE --archive ARCHIVE --output REVIEW'`
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

## Technology decision

Implement the normalizer in Python 3.12+.  It needs reliable streaming I/O,
RFC 5322/MIME parsing, SQLite/FTS5, Gmail OAuth/API support, IMAP, and
ClamAV-process integration; Python provides all of these with the standard
library plus small, well-supported dependencies.  The observed local corpus
(about 52 GB and 1.47 million input messages) is a streaming workload.

Use the standard-library `mailbox.mbox` reader and writer.  Pass raw `bytes`,
not parsed `Message` objects, to `mbox.add()` so that MIME serialization is
never rewritten. `mboxrd.quote()` supplies reversible quoting before
`mailbox.mbox` handles locking and container writes; Python alone writes mboxo.
Capture the pre-append and post-flush file offsets for the future
reader.  Use the standard `email` package only for header/MIME parsing while
retaining original RFC 5322 bytes for identity hashing and output.

Line-ending policy is tolerant reading with byte-preserving storage: accept LF
and CRLF without converting either representation, and retain lone CR content.
CR characters in emailed program output can be terminal controls, so their
presence alone does not identify a newline convention. The raw integrity hash
continues to describe the stored message bytes; semantic-hash normalization is
a separate derived calculation, not permission to rewrite mail. Windows archive
writing remains unsupported. Before enabling it, both reading and writing must
avoid platform newline translation, including the standard-library `mailbox`
conversion path; passing bytes alone is not sufficient on Windows.

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
contract is in [INTEGRITY_CONTROLS.md](INTEGRITY_CONTROLS.md#verifying-and-reconstructing-normalized-records).

## Native Mailbag storage

[Mailbag 1.0](https://archives.albany.edu/mailbag/spec/) is the native archive
layout, implemented directly without a `mailbagit` runtime dependency. The bag
root contains operational SQLite tag files, while canonical mboxrd files are
the only payload under `data/mbox/`. Rich per-message declarations live in the
top-level BagIt tag directory `integrity/`. The exact interoperability contract
is specified in [INTEGRITY_CONTROLS.md](INTEGRITY_CONTROLS.md).

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

### Apple Mail cache experiment

On September 6, 2026, the read-only `make compare-apple-mail` experiment
compared every complete `.emlx` record then present under `~/Library/Mail`
with 1,200,791 canonical records in `~/mail-archive`. For each EMLX file, the
program excluded Apple's decimal prefix and trailing plist, calculated `h2`
and `h3` over the declared RFC 5322 payload, tested the indexed raw hash first,
and then queried recorded observation `h3` values. For semantic-only pairs, it
retrieved the hash-verified canonical MBOX bytes and compared DKIM-relaxed
header-name/value multisets. Only aggregate header names and counts were
reported; message content, addresses, subjects, and header values were not
emitted.

The Apple store contained multiple mail services. Classification used only
local structural evidence: an IMAP account with a `[Gmail].mbox` or
`[Google Mail].mbox` hierarchy was classified as Gmail; Apple's `ews` scheme
was classified as Microsoft Exchange; remaining `imap`, `pop`, and `local`
schemes were retained as separate categories. No account address or opaque
Apple account identifier was included. This method establishes client adapter
type, not the legal or organizational identity of a provider; in particular,
an EWS row is not proof that every record came from the Microsoft 365 cloud.

| Apple Mail service | Complete EMLX | Partial excluded | Exact `h2` match | `h3`-only match | No archive `h3` | Archive records represented | Ambiguous `h3` | Formatting-only pair |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Gmail | 93,531 | 98,285 | 10,825 | 20,015 | 62,691 | 41,438 | 11,468 | 19,506 |
| Microsoft Exchange (EWS) | 7,273 | 1,154 | 1,601 | 31 | 5,641 | 1,628 | 0 | 31 |
| Other IMAP | 1,084 | 224 | 0 | 0 | 1,084 | 0 | 0 | 0 |
| Local | 204 | 0 | 0 | 0 | 204 | 0 | 0 | 0 |
| POP | 101 | 0 | 0 | 0 | 101 | 0 | 0 | 0 |
| **Total** | **102,193** | **99,663** | **12,426** | **20,046** | **69,721** | **43,066** | **11,468** | **19,537** |

An exact raw match was found for 12.16% of complete cache records. `h3`
identified another 19.62%, increasing the observed overlap to 31.78%; 68.22%
of complete cache records had no semantic match in the canonical archive.
Because one cache digest can match more than one canonical record, “archive
records represented” is a distinct count and must not be added to the cache
classifications. The 11,468 ambiguous cases were all in the Gmail category and
were reported rather than resolved heuristically.

Of the 20,046 semantic-only pairs, 19,537 (97.46%) differed only in header
formatting after DKIM-relaxed comparison. The remaining aggregate differences
were confined to Gmail-classified cache records: Apple-only `Received`,
`Return-Path`, and `X-Mailer` each appeared 508 times;
`X-Universally-Unique-Identifier` was absent from Apple Mail 508 times; and one
Apple copy lacked `X-GM-THRID` and `X-Gmail-Labels`. No selected header had a
changed normalized value. These observations empirically demonstrate that
raw-byte identity alone understates cross-client overlap, while also showing
why semantic equality should not erase the distinct source representations.

The store remained live during measurement. Its Envelope Index WAL changed,
and one additional complete Gmail EMLX appeared between the aggregate run and
the provider-stratified run. The table is therefore a point-in-time experiment,
not a transactionally consistent provider census or a completeness claim.
Known `.partial.emlx` records were excluded because they can omit detached
attachment bytes. The result supports `h3` as a reconciliation instrument but
does not measure false-positive identity against an independently labeled
ground-truth corpus.

### Diagnostic ambiguous-class review set

To support qualitative review of the ambiguity mechanism, a separate export
selected 20 distinct h3 classes by descending canonical-archive variant count,
then descending Apple-cache occurrence count, with h3 as the deterministic
tie-breaker. This is a purposive diagnostic selection of high-multiplicity
classes, not a random or representative sample. For each class, the exporter
copied every matching complete Apple EMLX payload and every hash-verified
canonical archive representation into separate subdirectories and wrote h2/h3
manifests without modifying either source store.

The resulting private review set contains 20 Gmail classes, 100 EML files, and
568,056 bytes. Every selected class has two Apple-cache occurrences sharing one
raw h2 and three canonical archive records with three distinct h2 values. Four
classes contain one raw representation shared exactly across the cache and
archive; the other 16 overlap only through h3. This structure makes the term
“ambiguous” concrete: one semantic identifier denotes three distinct raw
representations in four classes and four distinct raw representations in the
other 16. Every class has five source/canonical files because the cache
contains the same raw representation twice.

The augmented manifests assign each distinct raw digest a stable per-case h2
group and flag a group when both stores contain it. They also verify separately
that all occurrences of `Date` and `Subject`, after the same DKIM-relaxed
canonicalization used by h3, agree within each class. All 20 classes have the
same present normalized `Date` and the same present normalized `Subject`.
This does not imply byte-identical raw header lines: field-name case, folding,
and whitespace may differ. None of the 100 exported messages contains an
`X-Apple-Auto-Saved` header.

| Case | H2 relationship | Distinct h2 | h3 | Date component | Subject component | Autosave files |
| ---: | --- | ---: | --- | --- | --- | ---: |
| 01 | no cache/archive h2 match | 4 | same | same, present | same, present | 0 |
| 02 | no cache/archive h2 match | 4 | same | same, present | same, present | 0 |
| 03 | no cache/archive h2 match | 4 | same | same, present | same, present | 0 |
| 04 | no cache/archive h2 match | 4 | same | same, present | same, present | 0 |
| 05 | no cache/archive h2 match | 4 | same | same, present | same, present | 0 |
| 06 | one h2 shared across stores | 3 | same | same, present | same, present | 0 |
| 07 | one h2 shared across stores | 3 | same | same, present | same, present | 0 |
| 08 | no cache/archive h2 match | 4 | same | same, present | same, present | 0 |
| 09 | no cache/archive h2 match | 4 | same | same, present | same, present | 0 |
| 10 | no cache/archive h2 match | 4 | same | same, present | same, present | 0 |
| 11 | no cache/archive h2 match | 4 | same | same, present | same, present | 0 |
| 12 | no cache/archive h2 match | 4 | same | same, present | same, present | 0 |
| 13 | no cache/archive h2 match | 4 | same | same, present | same, present | 0 |
| 14 | no cache/archive h2 match | 4 | same | same, present | same, present | 0 |
| 15 | one h2 shared across stores | 3 | same | same, present | same, present | 0 |
| 16 | no cache/archive h2 match | 4 | same | same, present | same, present | 0 |
| 17 | no cache/archive h2 match | 4 | same | same, present | same, present | 0 |
| 18 | no cache/archive h2 match | 4 | same | same, present | same, present | 0 |
| 19 | no cache/archive h2 match | 4 | same | same, present | same, present | 0 |
| 20 | one h2 shared across stores | 3 | same | same, present | same, present | 0 |

The review material is stored under the project-local, gitignored
`.tmp/h3-ambiguous-review/` directory with owner-only permissions. Each case
contains `apple-cache/`, `canonical-archive/`, and `manifest.json`; the root
contains a warning, a compact h2/header/autosave table, CSV summary, and
complete manifest. A refresh mode re-hashes every private EML before updating
these derived reports and does not reread either source store. Raw
messages are private research evidence and must not be committed or
distributed. The checked-in exporter and synthetic test are reproducibility
infrastructure; the private corpus is not part of the software distribution.

The [on-disk format inventory](ON_DISK_MAIL_FORMATS.md) records PST/OST research;
the [executable importer specification](PST_DUAL_READER.md) defines the
filename-to-stdout-mboxrd interface. The standalone [PST adapter](PST_IMPORTER.md)
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
[PST_IMPORTER.md](PST_IMPORTER.md#relationship-between-pst-and-ost).

Packages will bundle selected OS/architecture-specific executables and native
dependencies. This avoids a mandatory JVM when only the Rust adapter ships.
The current macOS builder includes no PST importer. Windows installer and
Linux Snap builders remain unimplemented, as do full native ingest prerequisites.

The Cargo workspace now contains `rust/mct-importer`: the Rust library,
`pst-importer`, `mdti-validator` and `mcti-generator` implement/test
[MCT Importer API 1.0](MCT_IMPORTER_API.md). `make rust-programs` or each named
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

## Current package shape

`dev/addressbook-exporter.py` implements the experimental ePADD 11.1.3 text
handoff through `make addressbook-export ARGS='--archive ... --output ...
--owners-from-sent'`; alternatively repeat `--owner` for exact aliases. Sent mode
uses the persisted sender classification, never recipient membership or new
display-name matching. Legacy owner addresses without `@` stay in singleton
blocks and are reported explicitly. It opens a journal-free
catalog with SQLite read-only/immutable mode, streams referenced addresses into
a temporary SQLite set, lowercases/deduplicates them, and emits the owner first,
then one address per contact. It adds no correspondent display names. UTF-8
output requires an ePADD JVM using UTF-8; legacy local/UUCP addresses use singleton
blocks. Blank, non-printing, delimiter-prefixed, HTML-entity-bearing, and overly long entries are
reported rather than emitted. The source catalog SHA-256 must remain unchanged.
Non-`@` identifiers requiring HTML escaping are also reported because ePADD's
fallback double-escapes them at contact boundaries.
Files are published without replacement at mode 0600, outside the source archive;
`OUTPUT.report.json` records exclusions, owner choices, counts, and hashes.
`make test-addressbook-export` checks real SQLite fixtures, source immutability,
owner grouping, parser hazards, and refusal paths after lint/type analysis.
The export does not retain ePADD curation, mailing-list flags, or inferred names;
upload into a copy before treating it as a repair. Older archive owner-token
files may reflect former substring semantics and are not an exact owner-address authority.

```text
email-collection-toolkit/
  pyproject.toml
  src/mailarchiver/
    __main__.py         CLI, ingest framework, worker/status coordinator
    plugin_api.py       versioned immutable plug-in contracts
    plugin_loader.py    trusted manifest discovery and frozen registries
    pdf_mail.py         standalone printed-email PDF extraction and derived MBOX
    plugins/            packaged source and physical-file manifests
    sources.py          local source plus MBOX/Babyl/EMLX/message generators
    source_stubs.py     explicit unavailable provider/stream source names
    source_integrity.py local source integrity controls
    archive_integrity.py Mailbag archive-integrity controls
    layout.py           native data/mbox and integrity tag paths
    bagit.py            Mailbag CSV and BagIt checkpoint publication
    mbox.py             mboxrd publication and verified location reads
    message.py          header/MIME/date/classification parsing
    catalog.py          packaged schema loading and database helpers
    sql/
      V1__archive.sql   authoritative archive.sqlite3 V1 schema
      V1__search.sql    disposable search.sqlite3 V1 schema
    search.py           disposable search.sqlite3 and FTS5 rebuild
    scanner.py          on-demand ClamAV lifecycle and scan client
    ingest_status.py    shared typed per-run JSON status contract and store
    standalone_verify.py installed source-independent verifier
  gui/                  pywebview HTML, CSS, and JavaScript assets
  scripts/data_quality/ read-only forensic audit and evidence tools
  tests/
```

The data-quality scripts reproduce the investigation that motivated the date,
Babyl, MBCP, and `From XXX` rules. Makefile targets require explicit archive
and source paths and write private derived evidence under ignored `.tmp/` by
default. The scripts open the archive catalog read-only, verify bytes retrieved
from canonical MBOX locations, leave all source and archive files unchanged,
and refuse to replace existing evidence files. The generated MBOX, CSV, and
JSON files are investigation artifacts, not repository fixtures.

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
`Date:`/`Received:`/source/previous-message/path-year date resolution, a
temporary on-demand `clamd`,
`data/mbox/INFECTED1.mbox`, SQLite catalog/FTS files, native BagIt/Mailbag
metadata, versioned `integrity/*.mbox.integrity` tags,
per-run observation review, and year/correspondent reports.  The top-level
`MAIL_ARCHIVE_DIR` selects the archive for every command by default; the
`--archive` option overrides it. `ingest` takes one
or more source roots as positional arguments and `--owner-names-file` selects
a legacy include-rule list; otherwise archive `config.yaml` supplies owner rules. Ingest requires `--clamav` or explicit `--no-scan`.
With `--clamav`, it starts
a foreground daemon only when no healthy configured socket is available, then
removes the daemon's stale socket on exit; it never enables persistent or
on-access scanning. Workers enqueue typed phase/path/offset updates; the main
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
implement EMLX, Babyl, MBOX, and single-message EML/Maildir. Gmail, IMAP, O365,
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
[PLUGINS.md](PLUGINS.md) and [INTEGRITY_CONTROLS.md](INTEGRITY_CONTROLS.md).

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

Rollover, date sorting/repacking, complete recipient metadata, `verify`, richer
text extraction, Eudora, working IMAP cache directories, live
IMAP, Gmail, redaction, and research-oriented metadata remain planned work. The delivered
`mailsearch` command reads both databases without writing:
it applies `to:`/`from:`/`subject:` catalog filters, worldwide calendar-date
`date:`/`before:`/`after:` filters, and ANDed FTS5 terms; it prints stable
`message_pk` header lines and reads a numbered message directly from its
catalogued MBOX byte location, validating its SHA-256 before output.

Date search uses a worldwide calendar-date interval. Normalize ISO calendar dates, `m/d/yyyy`, and English
`Month day, year` values through the shared selector recognizer. For date D,
compute `start = midnight(D, UTC) - 14 hours` and
`end = midnight(D + 1 day, UTC) + 12 hours`. Compile `date:` as
`m.date_utc >= ? AND m.date_utc < ?`, `before:` as `m.date_utc < ?` using start,
and `after:` as `m.date_utc >= ?` using end. Bind normalized UTC strings in the
existing catalog format (`+00:00`), preserving indexed comparisons for counts
and every sort order. No archive rewrite or schema change is needed.
These 50-hour windows intentionally overlap by 26 hours on adjacent dates.
The shared CLI/GUI parser, suggestion recognizer, help text, and boundary/SQL-plan
tests use these same definitions.
The website's Advanced date-handling section records the exact user-facing
semantics.


GUI `SearchPage.error` carries query-parser feedback; the bridge does not save
invalid queries as window state, and the UI displays the error in result status.
`parse_query` produces `SearchTerms`; `_search_statement` and `_count_statement`
compile those terms into the typed `SearchStatement(sql, parameters)` used for
execution. `_candidate_source` shares filter-before-sort index selection between
pages and counts. Existing V1 indexes suffice; no archive rebuild is required.

| Primitive | Candidate access path |
| --- | --- |
| `from:` | Scan matching distinct addresses, then `messages_sender_address_pk` searches. |
| `to:`, `cc:`, `bcc:` | Address/role searches through `recipients_address_pk`, then message primary keys. |
| `any:` | Materialize matching addresses once; union indexed sender and recipient message IDs. |
| `date:`, `before:`, `after:` | `messages_date_message` range searches, including subject/sender sorts. |
| Words, phrases, attachment-inclusive terms | FTS MATCH, then `messages_sha256`; body-only counts use metadata's unique FTS-row-ID index. |
| Mailbox/volume scope | Source hierarchy/volume indexes, then `observations_source_file_offset` and message primary keys. |
| `subject:` | Scan the covering subject expression index, then look up matching message primary keys. |
| Unfiltered bounded listing | Date/subject order indexes, or ordered addresses and indexed sender-message lookups. |

`make test-mailsearch` includes a parameterized `EXPLAIN QUERY PLAN` regression
using the production parser and both SQL builders. It checks SEARCH operations
through the relevant filtering indexes, FTS MATCH constraints, absence of
per-message correlated subqueries, and result correctness under every sort field
and direction, plus exact/bounded counts. A shared fixture has 20,000 unrelated
messages, recipients, and source files; SQLite instruction budgets catch repeated
indexed probes that a superficial index-name assertion would miss. Subject
substring searches have a separate linear-work budget because an ordinary
B-tree cannot seek a leading-wildcard pattern. Tests check plan properties rather
than snapshotting SQLite's entire version-dependent explanation.

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
developer-written catalog migrations with checksummed history, consistent backups,
writer-lease protection, and transactional history updates remain planned;
the existing `schema_info` version check does not implement those safeguards.
`locations` and
`mbox_generations` are written as part of each message publication.

### CLI processor framework

During dispatch, ProcessingObject.job_id exposes the durable SQLite job ID;
retries retain it and new emissions/handoffs receive their own IDs. Provenance
uses source_metadata, parent_message_id, part_path and scan_provenance. Plugins
use check_cancelled() and remaining_seconds; cancellation leaves resumable work
within pending/running/failed statuses rather than inventing a cancelled state.


Processor reports count completed/failed attempts across archive history,
including explicit fail-import results. They show errors and typed timeout
counts; unfinished running attempts are not zero-duration samples. No-invocation
timings display n/a. Failed invocation JSON stores the typed response, including
its timeout flag and any scan evidence; successful JSON remains a ProcessingResult.
Legacy failure records without a timeout flag retain error counts only.


About lists processor versions by subscribed type and source/file acquisition
plugins by role and version. Both inventories include the archive's saved extra
plugin directories; acquisition discovery validates manifests without invoking
plugin factories during status refresh.


Identity address/group counts keep header and signature channels separate:
`messages` counts distinct messages containing the address in headers;
`signature_messages` counts distinct signature-bearing messages. The pickers
display Messages and Signatures columns. Dates cover either evidence channel,
and date filters apply to both counts. Group counts deduplicate within each
channel across all visible member addresses.


Scanner invocations persist typed clean/infected/not-scanned/unscannable/scanner-error
results with diagnostics and engine/signature versions (NULL if unavailable).
Errors and reported encryption/size-limit heuristics block filing and retain
raw input for retry. Version queries are cached per daemon configuration;
which unscannable conditions are reported depends on the daemon configuration.
Failed invocations may publish scan evidence only, never content or filing.


Attached-message promotion requires valid transfer decoding. Invalid base64
(including padding/trailing data), invalid quoted-printable escapes, and unknown
encodings leave failed extraction and retain the parent, without publishing a
child. Ordinary non-message MIME parts retain best-effort decode fallback.


MIME depth (default 40, `plugins.mime.max_depth`), attached-message depth
(default 20, `plugins.attached-message.max_depth`), and text size limits
(`max_text_bytes` on each text plugin) leave failed, retryable jobs. The GUI
offers continuation for these failures; increasing the configured limit and
resuming reprocesses the retained source. Limits never mark truncated work complete.

`MimeLimits` and a traversal-local `MimeBudget` charge all split/decoded output
before writing. A root MIME traversal visits nested attached messages before
releasing any child emissions, so budgets cover the complete tree without
per-message resets bypassing the root limit. Part and attached-message counts
are checked before creating/decoding further children. Limit exceptions make
the job retryable; per-invocation temporary files are cleaned. Quoted-printable
decoding preserves incomplete escapes across bounded chunks. `make test-mime-limits`
uses actual CLI archives to check failure, preservation and configuration retry.

The processing package implements API v2 manifests, typed objects/results,
in-process execution and a serial rank-barrier dispatcher. Make processor
provides init, plugins, submit, run, status and explicit reprocess commands.
Each archive is protected by the existing writer lease. Queue release and job
checkpoints use SQLite transactions; emission files are copied and fsynced
before committing references. Parent-job dependencies prevent work from
overtaking failed ranks. Restart recovers abandoned running work and reuses
completed invocations.

processing/sql/V2__processing.sql is a fresh framework schema, not a V1
migration. It is packaged independently of the production sql/ directory.
The focused framework target also runs catalog regression tests to enforce
that separation.
It includes the identity and organization relationships needed by the pickers.
Production CLI ingest now calls `ProductionPipeline` from the existing source
integrity/publication host. The three registered trees scan/file, extract headers
and queue content, then dispatch streamed MIME payloads to text, conversion,
identity and attached-message plugins. Canonical publication keeps its existing
journal and deduplication key. `processing.sqlite3` records raw references,
occurrences, jobs, evidence and picker state beside the catalog/search databases.
`process` resumes saved jobs without rereading sources; incomplete source
traversals are continued by repeating ingest. Configuration/code changes and
explicit replay rebuild automatic evidence while retaining manual decisions.
The default registry is packaged under `plugins/processors`; `processors` lists
it. The independent `make processor` fixture harness remains available.

Python plugins run synchronously in the host. A per-call context binds namespace
settings, deadline and cancellation checks. `remaining_seconds` bounds native
scanner I/O; POSIX main-thread invocations also receive an alarm. Portable or
background-thread execution requires cooperative checks. Late results never
publish. There is no separate Python worker executable. The Rust PST adapter
alone uses the external importer protocol, spooling output and retaining failed
tails with source/executable hashes and diagnostics under `processing-pst`.
`make test` builds the helper; real fixture tests cover partial-run recovery.

`make test-processors` runs lint/types and substantive framework tests;
`make test-cli-processors` validates production pipelines, deferred resume,
HTML/RTF selection, child byte preservation, identity edits and PST extraction.
No native windows or private archives are used. Desktop processor integration is
validated separately by `make test-gui-processing`.

On a positive ClamAV result, best-effort header metadata cannot prevent filing.
If parsing fails, quarantine records the raw digest as identity, labels its
required date/envelope placeholder `quarantine-unknown`, records the defect and
aborts subsequent processors. A real undated EICAR CLI test verifies exact bytes,
the positive scan checkpoint and exclusion from search/content processing.

HTML, RTF, text indexing and signature extraction use bounded reads and an 8 MiB
default UTF-8 output bound (`max_text_bytes` in each plugin's configuration).
Over-limit parts abort with a retained-content diagnostic. Generation invalidation
attaches the search database to the processing connection and uses SQLite's
rollback-journal multi-database transaction to remove FTS/suggestion data and queue
replacement jobs together; a SQLite trigger that rejects deletion checks rollback
of both databases after search-row changes have begun.
Provider domains use a built-in local set plus `plugins.identity-evidence.provider_domains`;
their evidence remains, but automatic affiliations are suppressed. PST receipts
record exit status after reaping even on timeout. Retained stdout/stderr prefixes
obey configured limits, with observed sizes and truncation flags in the receipt.

ProcessingObject.get_my_config() returns a defensive dictionary snapshot from
plugins.<manifest kind> in installation and archive config.yaml, with archive
values taking precedence. write_my_config(values, scope="archive") stages a
replacement of that plugin's selected layer; scope="installation" selects the
shared layer. Installation config.yaml lives beside application preferences;
the CLI --installation-config option provides an explicit path. The parent
preflights all writes after a successful rank using file locks and expected-value
hashes, then journals the batch before atomic file replacements. Recovery finishes
the batch before plugin reads. Equal-value replay is harmless; preflight conflicts
fail the job and require fresh invocations. See PLUGINS.md for the API.
Tests cover recursive precedence, namespace isolation, both write scopes,
abort/exception/timeout isolation and malformed settings without mocks.
Regression tests also cover later-plugin conflicts, interrupted multi-file
configuration publication, unreadable input recovery, invalid MIME tokens and
7z inventory size/digest checks.

The verifier SIGINT tests synchronize on consumption of FIFO data before
sending the signal, then close the writer so buffered I/O returns and Python
can deliver a pending signal. Holding the writer open indefinitely previously
caused intermittent CI timeouts. The tests require exit 130 with an incomplete
verification message, no traceback and no success output in normal/quiet modes;
they do not assert interrupt latency for I/O that never returns.

### Planned Contacts and geography

[The proposed processor graphs](PLUGINS.md#proposed-ranked-processing-graphs)
and website plugin page describe the agreed direction, including resumable work
selection, handoff plugins, first-class attached messages and durable tags.
Ingest owns scanning and filing, message processing owns header metadata, and
content processing owns MIME dispatch. Content-only resumption operates on
eligible already-ingested messages. Standard deduplication shares message
content/metadata while separate observations retain attachment provenance.
All plugins may access all headers and content of their current message.
Filing may read what it needs after ClamAV. Content-discovered child messages
enter message processing directly without another antivirus scan, retaining
parent scan provenance; the handoff uses shared deduplication/publication
services to establish their records and durable content references.
The graphic is a shared SVG in `website/static/images/processor-dag.svg`.
The CLI dispatcher, production extraction, attachment promotion and identity/tag
persistence are implemented. `gui_processing.py` supplies read-only status and
identity queries plus writer-leased manual edits. `GuiApi.resume_processing`
builds an `IngestRequest` from saved policy and starts the existing GUI import
thread; it never starts a Python plugin subprocess. Ingest saves resolved owner
rules and plugin/config paths in `processing_settings` before message processing.
Run history preserves unfinished source roots across later rootless content runs.
Content-only work reuses archived objects, and read-only access never creates a
missing processing database. Unsupported/missing saved policy reports an error.

`processing.js` shows the two checked resume options on opening and provides an
explicit **Continue Processing** action. `identity.html` and `identity.js` subclass the shared
matcher widgets with database queries, distinct per-group counts and immediate
manual writes. Name moves and separation update manual address assignments;
institutions retain domain-based membership and permit name edits. Prototype
undo/reset are not exposed for persisted edits. Authoritative matching remains
unconnected and disabled. About groups the registered processor manifests by
subscribed type. `gui_provenance.py` reads attachment tags and parent occurrences;
search rows show the gray tag and the viewer links to each parent and MIME path.
A parent outside the current search results opens in the viewer without trying
to select or scroll to an absent result row.
`make website-preview-screenshots` builds the website and renders its homepage,
Searching and Importing pages using local assets into `.tmp/website-previews`.
The headless tests use real bridges, queues and SQLite databases, including edits
surviving replay, competing writers, content resume without source files, and
attached-message navigation. Native macOS window behavior remains an opt-in check.

The standalone `make matcher-prototype` opens independent name and institution
windows through a temporary `LoopbackAssetServer` and pywebview. Use
`ARGS="--kind name"` or `ARGS="--kind institution"` to open just one. Closing both
windows stops the server. The launcher does not construct the archive
application, read preferences, or open databases. `gui/matcher.js` defines the
shared `MatcherWindow`, model, and immutable message/address record classes.
`matcher-types.js` supplies `NameMatcherWindow` and `InstitutionMatcherWindow`
subclasses with their respective labels and matcher callbacks.
`matcher-demo.js` supplies twelve fictional addresses, deterministic synthetic
message dates/identifiers, name groups, and institution groups (including an
explicit unassigned/personal group). Its two predefined name matches and one
institution match do not execute an authoritative research algorithm.

The shared window owns 39px disclosure triangles, mailbox/domain substring
filters, optional inclusive date bounds, header sorting, selection, parent/child
drag moves, a destination picker including hidden groups, separation, reset, and
session-only undo. Date filters project message observations without changing
the source records; first/last use and counts reflect that projection. Empty
date controls display “Any date”, overriding WKWebView's current-date placeholder
while leaving the native date editor available on focus. Group
counts use distinct message identifiers, including a shared-message fixture.
Clicking Messages initially sorts descending; other headers initially sort
ascending. Repeated clicks reverse direction, with `aria-sort` and visible
arrows. Sorting reorders groups and their children independently.
Parent moves include hidden children and every original observation; the
selection toolbar reports the complete source count. A matcher batch applies
to a separate model and becomes one undo step; failures leave data unchanged,
and mutation controls are disabled while it runs. Filter edits reveal matching
children; disclosure can then collapse them. A filtered group badge shows
matching/total addresses. `make test-matcher-prototype` runs Ruff, focused
lint/type checks, and real Chromium interactions for filters, inclusive/open
and reversed date ranges, count deduplication, sorting, disclosure, dragging,
separation, undo/reset, matcher batches, both subclasses, and minimum layout.
This is a synthetic UI experiment; catalog integration and authoritative
matching remain unimplemented. Browser evidence does not establish native Cocoa
drag behavior; manual testing uses `make matcher-prototype`.

Contacts are a derived address-level projection over the catalog. Extraction
will stream each message's `From`, `To`, `Cc`, and `Bcc` headers once and write
one deduplicated address appearance per message. It will separately derive the
default **Meaningful** relation: outgoing direct To/Bcc recipients and incoming
From addresses only when an exact configured owner address is in To. This
preserves all-header statistics while avoiding Cc and indirect mailing-list
traffic in meaningful-contact results.
The include/exclude owner rules route Sent mail. Archive creation and
File → Properties will later maintain a separate exact owner-address set for
the direct-owner predicate; fragment matching is not adequate for that use.
`mailarchiver human-contacts` is the initial read-only consumer. Its
`--owner-address-file` accepts the existing owner aliases with newline, comma,
or semicolon separators; it resolves them only to catalogued Sent sender
addresses. Optional repeatable `--owner-address` values are exact overrides.
The command defaults to meaningful Contacts and exposes the all-header
projection only with `--all`; table, TSV, and JSON output share one typed row
model.
Contacts opens the catalog with `Path.resolve().as_uri()` and SQLite `mode=ro`,
matching the schema validators. This preserves Windows drive-letter URI syntax
and encodes path characters without allowing database creation or writes.
Before rendering, it applies the strict versioned `contact_filters.yaml`
policy. It classifies malformed Unicode/control values and forbidden local-part
characters as bogus, configured provider/list patterns as mailing lists,
configured automated patterns as service identities, and only then includes
the remaining addresses. The policy deliberately keeps ordinary SMTPUTF8
addresses possible; it does not use non-ASCII alone as a rejection rule.
Bogus local-part and domain patterns report `invalid-local-part` and
`invalid-domain` respectively, with local-part rules evaluated first. Empty
domains are also rejected as `invalid-domain` after the local-part checks.
The policy resolver first checks `<archive>/contact_filters.yaml`; when
that file is absent it uses `src/mailarchiver/contact_filters.yaml` from
the installed package. A `mode: replace` archive policy is a complete strict
Pydantic replacement. A `mode: extend` policy may provide rule lists only; it
is unioned with the packaged lists in order, removing duplicates, while
packaged scalar values remain authoritative. The effective policy is therefore
complete and reproducible without field-by-field scalar merging.
Each pattern uses a Pydantic `AfterValidator` to compile with the same
case-insensitive flags used by classification. Regex and YAML errors become
path-qualified `ValueError` diagnostics handled by the Contacts CLI; invalid
rules cannot remain hidden behind earlier matches or an empty catalog.

Location evidence will retain a typed source, extraction method, observation
time, confidence, and `located` or `affiliated` relation. Signature extraction
may add located evidence; downloaded university domain/main-campus data adds
only affiliated evidence. A Contact remains an address even when future
authoritative-name work associates several Contacts with one Person.

The geography reference database is an installation-level SQLite database,
stored under `~/Library/Application Support/Email Collection Toolkit/geography/` on macOS,
`%LOCALAPPDATA%\\Email Collection Toolkit\\geography\\` on Windows, and
`$XDG_DATA_HOME/mailarchiver/geography/` (or
`~/.local/share/mailarchiver/geography/`) on Linux. The planned `make geography-data` and
Tools-menu updater will share a downloader that validates a versioned
bulk-data manifest and atomically installs the replacement. The planned package will ship
a US ZCTA seed. It contains representative coordinates and display geography;
the ZCTA lookup treats `02139` as an exact ZCTA, not ZIP3.

Future archive schema migration will use a dedicated
versioned Python-managed SQL migration history (Flyway filenames only). Search keeps its
independent disposable rebuild path. Geography data has its own database
version, while an optional geography snapshot copied into an archive uses the
archive migration stream. The snapshot is only copied or selected for reading
by an explicit user action. See
[CONTACTS_AND_GEOGRAPHY.md](CONTACTS_AND_GEOGRAPHY.md) for the complete design.

Header parsing decodes and unfolds RFC 2047 Subject values before catalog and
FTS insertion. `mailsearch` displays that catalog value directly. The verified
MBOX traversal in `refresh-index` rederives that catalog field while rebuilding
FTS, so existing archives acquire newly supported legacy-header decoding without
rewriting canonical mail.
Its result formatter determines number width from the returned `message_pk`
values and emits ANSI bold only for a terminal subject field.
Email-policy `compat32` header objects, including raw 8-bit `Received:`
and recipient fields, are converted to text before metadata parsing. Each
derived header field has an independent exception boundary. Broken RFC 2047
subjects retain their unfolded source text, and metadata defects are catalogued.
Sender resolution prefers a valid `From:` address, falls back to the RFC
`From:` inside a quoted nested-MBOX record and then the RFC `Sender:`
header. It recognizes Google Chat event payloads only when their Gmail thread
header and event fields are present. Chat actors are stored as
`Full Name (Google Chat)` so they cannot be mistaken for email addresses.
Reports render the remaining empty sender identity as `(missing sender)`.
Before ordinary message parsing, the MBOX adapter recognizes two narrow legacy
container records. Exact empty Eudora MBCP metadata stubs are emitted with a
`source-metadata-excluded` reason so ingest records their source offset and
hash without publishing them. An immediate quoted delimiter is normalized to
literal X-From framing, as described above. A `From XXX` envelope containing a
well-formed status-only outer header block before a quoted nested envelope
is stripped by one mboxrd quoting
level, and the nested RFC 5322 bytes are parsed and published. This fixes the
wrapper cause of missing senders without treating arbitrary body text as a
sender.

Remaining missing-sender remediation is staged: first identify each legacy
Eudora/MBOX dialect from container evidence and use its reliable boundaries or
content lengths so unescaped body lines beginning `From ` cannot create false
records. Then add explicitly tagged `Reply-To` or `Return-Path` identity
fallbacks only for structurally recovered messages; those fields describe a
route and must not silently masquerade as an ordinary author. Validate each
dialect by reingesting copied fixtures into a fresh archive and comparing
record counts, source offsets, logical identities, and canonical hashes before
any derivative archive is replaced.

Parsed dates before the ingest run's `--earliest-year` (default 1900) or after
the next calendar year are rejected. The same lower bound applies to `Date:`,
`Received:`, source timestamps, stream context, and path-year fallbacks. All
valid `Received:` timestamp suffixes are normalized to UTC and sorted. One
minimum and maximum are removed when at least three exist; the remaining median
uses the arithmetic midpoint for an even count. One- and two-header cases use
the untrimmed median. A valid `Date:` more than two days from that consensus is
replaced for catalog routing and tagged `received-median`; the original bytes
and header are unchanged. Missing or invalid Date values use the same median
with the existing `received` tag. If neither header supplies a date, a
message-specific `MailObject.source_date_utc` precedes the previous-message and
path-year fallbacks and is catalogued as `source-fallback`; the plug-in boundary
requires it to be timezone-aware and normalizes it to UTC.

Numbered-message display parses the verified raw bytes with the standard
library email parser. It renders the principal headers and prefers
non-attachment `text/plain` parts; it uses Beautiful Soup when only HTML is
available. XML-looking XHTML declared as `text/html` uses the forgiving HTML
parser while locally suppressing only Beautiful Soup's XML-as-HTML warning.
`--headers`, `--html`, and `--mime` select full headers, decoded
HTML parts, and exact original MIME source respectively.

Derived text decoding is centralized in `encoding.py`. It first strictly uses
the MIME charset, so `ks_c_5601-1987` is handled by Python's EUC-KR codec. On
an unknown or invalid declaration, or after a missing declaration fails a full
strict UTF-8 decode, it tries a bounded sample with `charset-normalizer`.
Detector candidates precede universal single-byte fallbacks. Candidate discovery
and strict-decoding quality ranking reuse one bounded sample, using printable
text and a modest CJK signal, then the highest-ranked viable codec strictly
decodes the complete payload. The
UTF-8 replacement decoder is only the last resort. `ftfy.fix_encoding` then
repairs recognizable mojibake in the resulting Unicode text; it is not used
as a byte-level charset detector and does not rewrite HTML entities. The
decoder returns encoding and recovery provenance for callers, while current
search/display callers use its value. There is intentionally no user checkbox:
these are loss-avoiding derived-text defaults, and the exact MIME view remains
available when the user needs the source representation.
Malformed raw 8-bit display headers are recovered from their original header
bytes with the message's declared body charset before RFC 2047 processing, so
catalog, search, Raw Source display, and MIME-part displays use the same
source-preserving recovery path.

`mailarchiver.application` is the platform-neutral desktop lifecycle layer.
`ApplicationController` owns the canonical archive-document registry, logical
search windows, preferences, recent documents, startup selection, active-window
routing, and host entry points for operating-system open/reopen events.
`ArchiveDocument` owns shared per-archive ingest and child-window state and
retains the typed `WriterLease` for an active import. `WriterLease` uses
nonblocking `flock` on supported POSIX systems against
`status/archive-write.lock`. Its UTF-8 JSON is diagnostic only; the open OS lock
is authoritative and is released automatically if a process dies. CLI ingest,
GUI import, and search-index replacement enter through the same lock contract.
`SearchWindow` is a typed record for the independent query, sort, selection,
mailbox-filter, and geometry state that the JavaScript UI maintains. Preferences
are written explicitly as UTF-8 and atomically replaced in the platform
application-data directory; their randomly generated temporary filename has no
user-derived component. They are treated as optional, discardable state.

`PyWebViewApplication` adapts that model to native pywebview windows without
putting platform imports in the controller. Its File menu asks for a new or
empty destination and initializes it before creating a blank window, opens an
archive in a new search window, runs Import, and closes the active window unless that window owns the
running Import. Window close events enforce the same rule. The Window menu
routes to the active archive's shared Ingests child window and enumerates every
About, search, and Ingests window. **Window → New Search Window** creates another
view of the active archive; the macOS adapter disables it without an active
saved archive search window and refreshes on native focus changes.
A narrow macOS adapter refreshes pywebview's
process menu when window or ingest state changes and disables the Close item
when the controller would refuse it. `handle_open_documents` and
`reopen` are stable host entry points for the extension/activation work in
issue 76. Windows shell work and frozen application/installers remain owned by
issues 73, 72, 74, and 75 respectively.

The CLI and GUI construct the same Pydantic `IngestRequest` and call
`run_ingest()`. The GUI acquires and publishes the per-document writer lease,
runs the service on a non-daemon worker thread, leaves readers usable, polls the
existing typed status files in every attached search window and the persistent
About window, and invalidates view caches after the run. Startup and import
errors are retained as typed notices instead of being available only on stderr.
Before creating NSApplication, `configure_macos_application` registers the
process-local `NSTreatUnknownArgumentsAsOpen` default with string value `NO`.
This prevents Cocoa from reopening the source launcher and option values as
documents; argparse and the Finder delegate retain their existing roles.
`make self-test-gui` launches the actual `mailsearch-gui` entry point using the
same isolated diagnostics as the packaged app and rejects unexpected startup
errors. The native application probe also checks startup notices and opens its
fixture through the document-open delegate.
The Ingests bridge exposes **Import Directory…** for its bound document. It
reuses that document's search window (or opens one to own the job) and runs the
normal confirmed import workflow.
The UI polls import availability and prevents repeated clicks while dialogs are open.
Import's final confirmation uses an application-owned macOS NSAlert with the
bundled icon, destination heading/path, and explicit Import/Cancel actions.
The shared alert helper marshals presentation onto the Cocoa main thread and
supplies the import confirmation icon.
An application-owned NSOpenPanel labels source selection **Import**, displays
the destination, and enables both file and directory selection, including
multiple selections. Both import entry points open this panel directly without
a source-type question; directories use recursive discovery.
`owner_rules.OwnerRules` validates and normalizes typed include/exclude lists.
Each glob matches the entire mailbox component; `@` explicitly introduces a
separate domain glob. Includes are evaluated first, then exclusions override.
Case is ignored; no display-name, substring, or automatic plus-address expansion
occurs. A bare exact rule also matches the same legacy sender without `@`.
`_run_ingest` resolves rules before creating the catalog and uses this matcher
for Sent routing after the infected-message check. CLI `--owner-names-file`
optionally supplies include rules while retaining configured exclusions;
without it the archive YAML supplies both lists.

`DocumentOptions` loads `config.yaml` defaults, falling back to legacy archive
and selected-source-root owner files only when no YAML owner settings exist.
Every import presents native include/exclude text areas on macOS and an
`options.html` prompt on the portable host. Both accept newline/comma-separated
rules, validate before continuing, and save nothing on Cancel. Final confirmation
passes typed rules to `start_import`, which obtains WriterLease, checks the
revision captured before the dialog, and saves changed lists atomically.
Sources and legacy owner files are never rewritten.

The document-bound `DocumentOptionsApi` uses the same lists in an options child
window. Save checks the lease and revision; status polling disables edits during
ingest without clobbering unsaved text. The import snapshot is
`status/owner-rules-used.yaml`, allowing change warnings across restarts.
After workers stop and canonical checkpoint publication completes, a streaming catalog
query regenerates `owner-names-detected.txt` from all matching archived senders,
including previous imports, with excludes applied. Addresses are sorted and
unique; unsafe line delimiters are omitted. The file is derived evidence, not
configuration. Neither this file nor owner configuration is in preservation
manifests. Tests in `test_owner_rules.py` exercise real fixture import, stored
categories, raw hashes, saved defaults, exclusion precedence, idempotence and
standalone archive verification; GUI service and native application tests cover
both fields, cancellation, revision conflicts and persistence.

`archive_config.py` writes version 2 YAML containing `owner.include`,
`owner.exclude`, and `last_import_directory`, while reading version 1 as well.
Unchanged lists are not rewritten. Successful import updates navigation only
when needed and preserves the rules. Malformed configuration raises an error
instead of discarding potentially important owner settings. The source picker
uses the saved directory if it exists, otherwise the archive parent.
Existing message categories remain unchanged by settings edits or FTS rebuilds.

When startup produces a placeholder, the shell discards it without creating a
native search window and opens `setup.html` with all three numbered steps.
`SetupApi` exposes only the two folder pickers, Start import, and Cancel through
`WindowBridge`; picker cancellation retains its server-side selection. The
source and destination NSOpenPanels accept only existing directories and disable
New Folder, even before a source is selected. This prevents browsing from
creating a directory inside an input tree before overlap validation. Folder-only panels treat `.mailarchive` packages as
directories so existing archives remain selectable. `SetupSelection` compares folder and ancestor filesystem identities for overlap
(including Cocoa Unicode and case aliases) before
any archive initialization. Start import opens a valid existing destination or
creates an empty one, reuses its search owner on retry, and passes the chosen
root into the existing confirmed import workflow. A started job opens Ingests
and hides setup after clearing its selections. The webview stays alive to
receive the bridge reply; destroying it inside that call would strand a reply
thread at exit. Cancel (also Escape) waits for its bridge reply thread to finish, then quits the
application using the existing stop/checkpoint policy. `request_quit` first calls
`prepare_quit` under the same application lock used to publish import jobs: a
job-free decision sets `_quitting` before new jobs can register; otherwise it
confirms active ingest, stops jobs, and waits on a background thread. An opt-in native regression
publishes a real leased job immediately before that decision and verifies the
confirmation, stop signal, lease retention, completion, and application exit.
Picker selection and the Cancel action do not persist setup paths or write
preferences. Normal startup may already have removed an invalid or missing
remembered archive through `_forget_recent()` before showing setup; Cancel does
not undo that cleanup. Start import calls `open_document()` or `create_document()`,
which records the destination in application preferences before import settings
are confirmed. After ingestion, the worker may save the selected source directory
in the archive configuration through `remember_import_directory()`.
Pending setup operations
disable Cancel and native File → Close and prevent window closure. Menu state
refreshes on every setup lock acquisition and release, including Cancel and error
recovery. The native Close gate checks the setup lock independently of the active
or fallback window, and the Close handler rejects queued actions while locked.
Native regressions inspect both Cancel transitions and a real modal picker over
an existing search window with no webview key window. Shared
NSOpenPanel instances clear their accessory view before setting a new warning. Errors stay
visible and controls recover for retry.
`make test-startup` covers launch precedence, preference preservation, folder
identity checks, and the three-box layout at default/minimum window sizes.
`make test-native-setup` selects disposable folders through the actual Cocoa
panels and bridge, checks cancellation and inline validation, imports one real
message, verifies its SHA-256 and untouched source, and requires clean exit.
`ApplicationController.startup(new=True)` bypasses explicit and remembered paths
without changing preferences. The GUI maps `--new` and macOS's current
`NSEvent.modifierFlags()` Option flag to that path before normal startup.
The Dock reopen delegate samples the same flag and restores or creates setup.
The native setup target also tests the sampler before application configuration,
explicit/remembered archive bypass, normal Dock reopen, and Option reopening one
existing setup window. Only the global hardware-modifier source is substituted
with real NSEvent flags to avoid sending keystrokes to the user's desktop; this
does not establish a physical Option-click/Finder launch test. Physical
Option-launch and Dock Option-click remain unverified.
The user holds Option through launch because the flag reports current key state.
About is created hidden to retain the event loop and File New/Open after setup
closes; the application menu explicitly shows it. Native dialog
paths normalize SAVE strings and OPEN/FOLDER sequences before indexing; New
validates the destination inside its error handler. Cocoa File menu items carry
explicit Command-N/O/W shortcuts. Archive opening uses the File menu rather than
a toolbar button.
The search toolbar omits the archive path; the native title bar identifies it.
The Cocoa adapter reorders File, Edit, View, Window after the application menu.
It resolves Cocoa focus on the main thread and still builds menus from the
logical active document or About when a background app has no native key window.
Closing About hides its retained native window instead of recreating it, keeping
the event loop alive even when no document is open. The hidden window is omitted
from the Window menu. The application menu's About command restores it; Dock
activation does not. Quit permits its actual destruction. The native lifecycle
probe exercises dismissal, background updates, and explicit menu reopening.
`WindowBridge` restricts pywebview introspection to explicit callable names,
excluding public controller and window object graphs. About allows the same
dynamic bridge generation as the other pages, retries bridge readiness, polls
status each second, and clears stale errors after successful refresh.
Archive Open validates schema metadata without `PRAGMA quick_check`, which
previously scanned multi-gigabyte databases before opening any window. Existing
extensionless directories remain accepted. `make check-archive-open ARCHIVE=...`
performs this read-only compatibility check without launching windows or saving
preferences. This check does not certify every database page against corruption.

`make test-native-application` runs the production About and search bridges with
a disposable extensionless archive, checks rendered notices and native menu order
and Open shortcut, and closes the application. It runs only on a logged-in Mac.

`LoopbackAssetServer` owns GUI delivery. Bootstrap redirects send
`Content-Length: 0`; HEAD obtains the asset length from filesystem metadata
without reading its contents, while GET sends the asset bytes. It binds `127.0.0.1:0`, issues a
different one-use bootstrap ticket for every new window, sets a random
session-cookie name and value, redirects away from the ticket, and serves only
resolved files below `gui/`. Direct unauthenticated requests, ticket replay,
foreign `Origin`, an incorrect `Host`, and traversal fail closed. It emits no
request log and no CORS allowance. WKWebView/WebView2 service calls continue to
use pywebview's native JavaScript bridge; there is currently no HTTP API.

The `gui/` prototype uses pywebview's Cocoa/WKWebView backend on macOS.  Its
Python API delegates query parsing, SQLite reads, and direct MBOX retrieval to
the same typed functions used by `mailsearch`. A nonempty query directly asks
the shared catalog/FTS predicate for up to 2,000 ordered headers. This avoids
placing an exact or threshold count on the interactive path; SQLite FTS5 has no
reliable approximate cardinality for arbitrary combined full-text/filter
predicates. A larger result set immediately shows that prefix and
then requests the unlimited remainder from offset 2,000 through a second
bridge promise using the same SQL predicate and stable sort, so the two phases
are complementary and cannot change membership or ordering. The result status
is red and says **Searching in background** until
the remainder arrives, at which point the materialized length supplies the
exact count. Search-generation checks discard a stale response when a newer
query starts. Preview requests are split into
100-message batches on one promise queue, and queued work from an older search
generation is skipped. An empty query clears the result list without invoking
SQLite and clones a static help template containing full-text, phrase, and all
selector forms into the result pane. The interface contains no manual
result-pagination controls. The UI is conventional: a search toolbar above a
result list and message pane. Independent
message windows load the same static application with a message-number parameter;
their message pane fills the window and scrolls independently so source-location
evidence remains reachable.
The headless regression explicitly makes the message taller than its viewport,
then scrolls to and checks the visibility of source locations; it does not rely
on font-dependent fixture height or iframe load timing to create overflow.
The main window polls the latest shared `IngestStatus` once per second and
renders it in a bottom status line. The separate `ingests.html` application
polls all typed status files and presents run history beside aggregate and
per-worker detail. Both the status-line action and the native
**Window → Ingests** menu route through a per-document singleton window owner: an existing
window is restored and ordered to the front, while a closed one is recreated
with its own normal close box. A running file whose heartbeat is older than
five seconds is displayed as stale without rewriting its retained JSON.

The native shell loads the checked-in 192-pixel PNG derived from
`gui/icons/rainbow-post.svg` before falling back to a system symbol, so the
Python application and its About/Dock identity use a stable project asset. The
`website/` directory is a Zola site using the local
`envelope-rainbow` theme. GitHub Pages builds it from `main`; the workflow
SHA-256 verifies the pinned Zola archive before extraction, resolves the newest
exact stable and beta tags into Zola data, then deploys a Pages artifact. The
home-page template uses a light rainbow design with capability and story cards
and equal individual and archivist columns. The cover displays the text-free
`images/cover-artwork.png` derived from the approved banner. CSS fits the
central artwork into the original wide banner proportions, excluding the
image generator's blank top and bottom margins. The image's HTML dimensions
match the displayed 1312:287 ratio rather than the source file's dimensions.
Its title, subtitle,
description, and "Email has a history. Keep it" tagline are semantic HTML
positioned over the artwork on desktop and in normal flow below it at widths
of 1000 pixels or less. The decorative image has empty alternative text;
the visible heading and paragraphs provide the accessible content. Banner text
uses capped viewport-relative sizes without container-query units. Literal
spaces between tagline spans are retained for selection and mobile flow;
Chromium checks verify exactly three desktop lines without whitespace gaps.
The story card stacks its text above the user-supplied `images/hands-typing.jpg`
(799 × 372). The photograph scales proportionally without cropping and has a
caption linking to Image Catalog on Flickr and its stated CC0 dedication.
`make website-preview` reuses its disposable `.tmp/website-preview` output
with Zola `--force` and runs a temporary preview on loopback port 1111
(overridable with `WEBSITE_PREVIEW_PORT`) without publishing. The Zola configuration retains the
existing project URLs under the Email Collection Toolkit title. The checker
parses its TOML before the generic required-file check and reports file-read, UTF-8 decoding,
and TOML syntax failures without a traceback; `make website-icons` regenerates PNGs
from the shared SVG using Chromium, closing the browser even on rendering or
write failure. Navigation wraps at every width; long
documentation code blocks scroll within the page. The `use-cases.md` content
page supplies the detailed personal-archive and donor
digital-estate narratives. It describes BagIt/Mailbag as native archive storage and standard
MBOX as the ePADD handoff, with planned direct-provider, first-class package
import, and automated interoperability work labeled explicitly.
The base template links every page to `privacy.md` and `rights.md`. The privacy
page describes planned Gmail and Microsoft 365 OAuth data access, local
storage, read-only use, revocation, and provider-policy commitments without
presenting the reserved adapters as implemented. The rights page records the
current GPL distribution, copyright, and possible non-GPL availability.
The reusable `section.html` template renders section content and child-page
cards through the site theme. The curation section adds a responsive five-part
summary of local file discovery, read-only ingest, archive creation, search and
reporting, verification, and sharing. Public copy describes implemented and
planned functions in language intended for archivists.
The primary navigation links to `about.md`, which describes Simson Garfinkel
and links to his personal website, GitHub profile, and project repository.
About links to `changelog.md`, a dated record of website changes distinct from
application release notes. The 2026-09-07 entry records the storage-format
wording correction and the new About/changelog pages.
The release workflow requires a version-matching annotated tag, builds a source
distribution, writes `SHA256SUMS`, and publishes a GitHub Release.

Result ordering is a server-side SQL whitelist over date, case-folded subject,
or case-folded sender with a stable message-number tie break. The Tabulator
result table owns focus, rendering, and row components, while the application
maps Up/Down to selection and message display.
The sole Tabulator column has `resizable: false`. A focusable vertical separator
uses pointer capture and keyboard controls to adjust a CSS grid track. Its
per-window fraction is clamped to 300-pixel list and 320-pixel preview minima
(half the available width when smaller). Resize observers account for the
folder tree and window dimensions and remeasure HTML previews; Tabulator's own
container observer refits its column without replacing data or selection.
The separator is hidden in standalone and print layouts. Headless browser tests
drag the real divider and check widths, overflow, selection, and keyboard limits.
The older bounded-recent optimization remains internal to the command-line
client, whose automatic exact fallback preserves its one-call behavior. The
GUI always invokes the complete SHA-256/FTS query because an archivist may be
looking for any period in the collection. It materializes the first ordered
page without a count probe; first and background queries use the same stable
sort and complementary offsets, so the combined set has no skips or duplicates.
Subject and Sender modes sort the
complete matching set rather than using recency to choose page membership.
Result paperclips use attachment counts joined from the disposable search
metadata without rereading MBOX content. Each row reserves a third line;
Tabulator 6.5.2 is vendored under `gui/vendor/tabulator/` (MIT) so the desktop
application remains offline-capable. Tabulator's virtual DOM paints only its
viewport and buffer while retaining the complete result data. Its formatter
queues preview IDs only when it paints a row through the Python bridge. A
single-worker executor reads the indexed 18-word previews, and JavaScript polls
the typed result batch until it can update visible rows. The result-table boundary
cancels native `selectstart`, and rows use `user-select: none` so file drags do
not select card text. Tabulator handles modifier-click selection and virtual
scrolling without custom pointer-range code. A single selected row displays its
message. Multiple selected rows replace the message with a count and the same
file well. An explicit drag from either source prepares the selected export;
clicking a row remains a normal message click. Global macOS Command-key handlers
select numeric MIME part IDs or raw source. Command-F opens an in-message
finder from the first current search-highlight term and selects its input;
Command-G opens the finder at its first match when it is closed, while
Shift-Command-G and subsequent Command-G presses cycle its matches. The finder adds a distinct
current-match outline without changing canonical bytes or the archive-search
predicate. Each accepted HTML render carries a viewer-generated per-render
marker; cross-frame navigation keeps direct references only to marks bearing
that marker, so email-provided IDs and CSS classes cannot redirect it. The
current mark receives an inline, important orange style and scrolls immediately,
after a short timer fallback, and after two animation frames. Image/font load,
error, and window-resize events remeasure the iframe and retain its active
target in the outer message pane. Finder updates are debounced and re-render the
selected part with any existing part-local remote-content authorization. Finder,
selection, part, and frame-identity generations discard stale responses and
stale key continuations. The viewer waits for the parsed local iframe document,
not every remote resource, so authorized slow images cannot blank local message
text. The sandbox grants same-origin access only to this inert, scriptless
sanitized document; scripts remain prohibited and the document keeps its
restrictive CSP. It does not grant popups: a frame-level link handler writes an
allowed destination to the bottom status bar on hover, prevents its default
click, and presents **Open Link**, **Copy Link**, and **Ignore**. The native
bridge independently validates only `http`, `https`, and `mailto` destinations;
it writes copied links as both text and a URL, and opens them through NSWorkspace
only after **Open Link**. Plain-text rendering tokenizes only those same allowed
URL schemes into links and installs the identical capture-phase handler; Raw
Source remains literal text. Selecting another message resets the finder index to its first
match without replacing the finder text.
Command-A uses the most recently clicked pane: it marks all result rows as
selected in the list, or creates a DOM range over the displayed body for plain
and raw text, excluding viewer headers, attachments, and provenance. Text
inputs retain their normal native select-all behavior. The toolbar copy control
passes visible plain/raw body text to the native pasteboard; for HTML it adds
the viewer subject and headers to the rendered document's visible text.
The toolbar's **Search attachments** checkbox passes an explicit boolean to the
typed search service. Ordinary terms search `message_fts` by default; when the
box is selected they search the union of `message_fts` and `attachment_fts`.
Metadata selectors are unchanged.

Each typed GUI search page also returns its deduplicated ordinary free-text and
textual-selector values; date selectors remain filters only. JavaScript marks
literal case-insensitive matches when it renders header, plain-text, or
raw-source text nodes. For sanitized HTML it parses the already
inert document, marks body text nodes, injects only the configured mark style,
and then supplies the result to the existing sandboxed iframe. The versioned
packaged `configuration.yaml` is loaded into strict Pydantic models and exposes
the initial `#fff59d` highlight background through the GUI status response;
the color accepts only six-digit hexadecimal syntax before it reaches CSS.

Search completion starts after three characters, waits 120 milliseconds after
the latest keystroke, caps each address and subject group at 20 entries, and
discards responses superseded by newer input. Address results rank by
deduplicated message count and then last-seen date.

The optional original-mailbox explorer is built from
`source_files.hierarchy_path`; volume-relative `source_path` remains the exact
physical provenance. Pydantic node identifiers encode a normalized logical
path and, only in explicit-volume mode, the stable source-volume identity;
browser input never supplies SQL. MBOX files, structurally recognized Maildir
roots, Apple Mail `.mbox` package chains, and directories containing only
direct EML/EMLX files become logical-mailbox leaves. Exact node counts use
`COUNT(DISTINCT observations.message_pk)` with the path/volume and
`observations_source_file_offset` indexes. Hidden-volume trees merge identical
volume-relative paths. The two tree modes are cached for the active archive.

Selected branches become one correlated `EXISTS` predicate inside the
materialized candidate query, before its `LIMIT` and `OFFSET`. The predicate
uses `observations_message_pk`, so multiple source observations provide union
semantics without duplicating a canonical result. Hiding the explorer sends no
selection while retaining its browser state. Versioned Pydantic filter sets
are fsynced to a temporary file and atomically replaced in the platform's
per-user preferences directory; the archive is never written.

MIME descriptions and API responses are Pydantic models.  Body content is
loaded only for the selected part.  HTML parsing removes active elements,
event handlers, file URLs, and unsafe URL schemes, replaces image CID references
with message-local data, and injects a restrictive CSP.  The HTML is displayed
inside a sandboxed iframe. Remote image URLs are omitted unless the user
explicitly enables them for that view. Reserved part IDs identify raw RFC 5322
source (`-1`) and a synthesized legacy x-html view (`-2`). Before enumerating
parts, the viewer tests a non-multipart message's raw body against an anchored,
case-insensitive `<x-html>...</x-html>` wrapper. This includes a malformed
multipart declaration that produced no parsed children, but excludes valid
multipart messages and bodies that merely mention the tag. The enclosed bytes
are decoded through the archive's best-effort text decoder, sanitized by the
same HTML path, and derived again from verified raw bytes when selected; the
canonical message is never rewritten. Individual image and PDF attachments
are base64-transferred only on an explicit preview action; other attachment
payloads are written to a private temporary directory before macOS opens them.
The viewer also reads the archive mailbox location and linked source
observations from the catalog, then displays archive path, source-volume label,
and source or forensic path at the bottom without treating an archive mailbox
as a source. It renders nonzero byte offsets as `?offset=N` and omits zero or
absent offsets. A local file source stored as `Users/...` on the root volume is
displayed as `/Users/...`. A local source-path control copies only the mounted pathname to
the macOS pasteboard, as both text and a file URL; provider and forensic paths
without a local pathname have no copy control. Direct provider observations sort before local evidence; retained
Apple Gmail observations are labeled as local cache copies rather than as the
authoritative cloud source.
The message-content and message-well containers are vertical flex layouts; the
source-location section uses an automatic top margin so it occupies the bottom
of any spare viewer height without affecting normal scrolling for long messages.

For a catalog row whose `date_source` is `received-median`, the typed message
response includes the decoded original `Date:` header, the stored UTC
`date_utc` as the Received-header median, and that same UTC value as the archive
routing date. The UI renders all three values in its warning banner. Keeping the
median and routing fields distinct makes the current routing decision explicit
without requiring the viewer to recompute archival date policy from headers.

`search.sqlite3` contains separate `message_fts` and `attachment_fts` virtual
tables so message text remains searchable without attachment matches.
`message_metadata`, keyed by message SHA-256, contains an
indexed mapping to the message and optional attachment FTS row IDs, an
attachment count, and deterministic 18-word body preview. FTS updates and
publication recovery resolve SHA-256 in this ordinary table and delete virtual
table rows by row ID, avoiding a full FTS scan. The `message_attachments` table
is keyed by SHA-256 and attachment ordinal, with the MIME-walk part ID, decoded
filename, and normalized MIME type.
Indexing parses each message once for FTS body text and attachment metadata;
`--index-attachments` additionally writes decoded text attachments to
`attachment_fts`.
The tables are derived and are replaced together with FTS by `refresh-index`.

`.eml` export writes the bytes returned by hash-verified direct retrieval.
Finder dragging uses an `NSPasteboardItem` with exactly one explicitly supplied
type, `public.file-url`, containing the exported path as a file URI. Cocoa may
add compatibility aliases, including `NSFilenamesPboardType`; the application
does not add URL-link or text types to the native writer. JavaScript carries only
an opaque registered export token with a copy-only operation mask; the Cocoa
adapter replaces that token before the native drag starts. Cocoa process setup
installs it before either normal document or native smoke windows are created.
The legacy
`dragImage:...` path clears WebKit link flavors and writes the file object; the
modern `beginDraggingSessionWithItems:...` path replaces the item writer before
AppKit builds its pasteboard. Unregistered text and link drags are unchanged.
Every preparation writes into a fresh `drags/<uuid>/` subdirectory, isolating it
from attachment basenames and later exports. Preparation and close share a lock;
close revokes its tokens and rejects later preparation before cleaning exports.
The status bridge disables the drag control on non-macOS backends. Startup
awaits Tabulator `tableBuilt` before clearing the initial result viewport. Queued
row-click callbacks verify the search generation and current row before selecting.
The browser acceptance test rejects unhandled page errors and verifies export bytes.
`make test-file-drag` checks exact
export bytes, token revocation, both native pasteboard representations, and
injected selector/superclass dispatch on a controlled AppKit host.
Result cards and the message-file icon well both call `installDrag`; selected
rows use the complete selection and an unselected row uses only its own message.
Modifier clicks select multiple rows; pointer drags export files. Message headers
remain normal selectable text. The browser never preloads an
`.eml` file on hover or selection: a drag-start event begins asynchronous
preparation, and a subsequent drag transfers the ready file. Each write uses a
unique same-directory temporary pathname before atomic replacement.
`window.print()` is handled by pywebview's WKWebView
print operation. Temporary exports live only for the application process.

The shell page permits `unsafe-eval` only for local scripts because pywebview
constructs its typed Python API wrappers with JavaScript `Function`. Message
HTML remains isolated in a sandboxed frame with its own restrictive CSP. The
`test-native-gui` Makefile target opens a hidden smoke-only page over Cocoa. The
page calls `status()` and one real `search()` through the injected bridge, then
reports exactly one result to Python instead of making Python synchronously
poll WKWebView JavaScript. A dedicated bridge exposes only those three methods,
and smoke mode supplies no custom application menu. The child atomically
records timestamped phases and requests shutdown from a watchdog; the pytest
parent has a separate timeout, captures a macOS process sample, and terminates
the process group if needed. Later shutdown failures add phases without
replacing the original bridge error.

`aisummarize.py` implements the `summarize` console entry point. It reads stdin
before doing any native work and invokes a content-addressed Swift helper built
into `~/Library/Caches/mailarchiver` from the packaged `apple_summary.swift`
source. The helper uses `SystemLanguageModel.default`, checks model
availability, and asks for one faithful abstractive sentence of at most 30 words while
treating input text as untrusted content rather than instructions. Neither the
input nor output is canonical archive data.

Use `uv` for dependencies and every test/run target through the repository
Makefile.  Typed Pydantic structures carry all message metadata and external
API responses; dictionaries are confined to API-boundary decoding.

`standalone_verify.py` is itself limited to the Python standard library. At
ingest startup it copies its own source atomically to
`verify_mail_archive.py` in the bag root. The installed script accepts only the
native format in [INTEGRITY_CONTROLS.md](INTEGRITY_CONTROLS.md). It validates
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

`search.sqlite3` has its own packaged, versioned `sql/V1__search.sql` schema and
does not use cross-database foreign keys. `create_search()` rejects unversioned
or incompatible layouts. Ingest catches that specific condition, completes any
pending publication recovery against a temporary current-layout index, and
uses the same validated, atomic rebuild as `refresh-index` before starting
workers. Its main FTS5 table includes an unindexed `sha256` column plus
searchable headers and selected body text: `text/plain` first, otherwise rendered
`text/html`, otherwise a safe single-part fallback. A second FTS5 table stores
text-attachment content only when requested, allowing the GUI to include it
without changing default body-search semantics. Binary attachment bytes are
excluded. An external-content trigram FTS5 table covers unique normalized email
addresses. Its aggregate source table retains one display name, a deduplicated
message count, and a last-seen date, while a SHA-256 mapping table retains the
per-message date for exact count and recency updates and replacement.
The live completion path resolves catalog addresses against header/authority
names from processing.sqlite3, with this legacy display-name table as an additional
source. It derives roles and distinct message counts through catalog sender and
recipient indexes, without requiring content processing. Subject matches scan
the canonical subject column rather than creating
a second subject store. Ordinary `message_metadata.sha256` is the indexed lookup key for the
corresponding FTS row IDs; updates and recovery delete FTS rows by row ID rather
than filtering the virtual tables on their unindexed SHA-256 columns. The
Makefile's `install-mac` and `install-linux` targets download, SHA-512 verify,
and unpack Tika 4's application ZIP distribution under the ignored
project-local `.tools/tika/<version>/` directory. The runnable JAR and its
adjacent `lib/` directory remain together. The checksum token must be exactly
128 hexadecimal characters. Failed extraction, layout validation, or rename
removes the private temporary directory. Tika is an optional future extractor
for PDF and Office attachments, not a service. Rebuild the
index in a temporary database,
validate row identities against `archive.sqlite3`, then atomically replace the
old search database. Live indexing and `refresh-index` both exclude
`INFECTED` and the reserved `MALFORMED` quarantine category; rebuild recognizes
numbered quarantine MBOX filenames.
`refresh-index` first renders terminal progress for the finite canonical-MBOX
validation phase weighted by catalogued message counts, then renders
message-count progress and an elapsed-rate ETA
while building the replacement database. Its replacement file is never opened
as the live index; a `KeyboardInterrupt` rolls back catalog subject updates,
removes the incomplete replacement, and reports that the existing index is
unchanged. A bounded, ordered worker pool verifies source hashes and parses
MIME content in parallel (all detected CPU cores by default, or two if the
count is unavailable, configurable with `refresh-index --workers`); a sole main-thread SQLite writer retains stable
catalog/update order and never shares a SQLite connection across threads.
Ordinary `mailsearch` listings and reports select only `Sent` and `Archive`;
the authoritative catalog still retains every quarantine record.
Bounded date-sorted listings materialize an indexed, ordered candidate page
before joining recipient rows. Year-scoped reports use half-open ISO 8601
`date_utc` ranges so SQLite can use the date index.
Normal ingest publishes canonical MBOX/catalog state first, then attempts the
disposable FTS insertion for normal Sent and Archive mail. Extraction or indexing failure records a
`search-index` metadata defect without rolling back canonical mail.

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
   completion-validated container evidence becomes a future checkpoint; API v1
   does not commit a per-message provider checkpoint.
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
[INTEGRITY_CONTROLS.md](INTEGRITY_CONTROLS.md).

Planned run-completion sorting will order each touched normal mailbox by
`(resolved_date_utc, sha256)`. The sorter will write a new MBOX under
`data/mbox/` and its integrity tag
under `integrity/`,
scan both end-to-end, compare the unordered identity sets, then atomically
update the relevant `mbox_generations`/`locations` rows. It must retain the old
file until validation succeeds and delete it only then. `INFECTED` and
`MALFORMED` quarantine mail is not FTS-extracted and is not moved into a normal
mailbox.

## Antivirus and text extraction

`IngestRequest.scan_policy` is `clamav` by default. Explicit `not-scanned`
requests skip scanner construction and persist an `antivirus` metadata defect
in the existing catalog transaction for each new message. Run status includes
the policy (older files default to `unknown`), without changing the catalog
schema or canonical bytes. Failed required scanning still stops import.
The native source picker and Ingests page show a missing-configuration banner;
the final native confirmation defaults to Cancel and gates the opt-out.
Import confirmations use a 560-point-wide selectable AppKit accessory label,
keeping archive/source/owner paths readable without changing default or Cancel
actions. `make test-packaging` checks real alert layout and both button sets
without showing a modal dialog.
The download action opens the application's release page. About reports configuration
presence separately from readiness, which remains an ingest preflight check.
`make test-packaging` exercises missing-scanner failure, explicit opt-out,
durable evidence, source immutability, and isolated headless diagnostics.

## Compiled desktop UI trials

[DIOXUS.md](DIOXUS.md) records the planned UI trials: Dioxus Desktop and Tauri
using the system webview, with ingest/search/preservation still in Python.
The planned typed local Python worker and Rust frontend are not implemented.
Current `PyWebViewApplication`, `WindowBridge`, HTML/JavaScript, and PyInstaller
sections describe the existing application. They remain the migration baseline,
not evidence of Dioxus support. Windows full ingest takes priority over Linux
packaging and still requires backend locking and scanner portability fixes.

Plan comparable trial implementations in Dioxus and Tauri before choosing a
framework. Either approach retains the Python archive engine.

## Windows development setup

[WINDOWS.md](WINDOWS.md) describes an unexecuted clean-Windows setup procedure,
including automatic and explicit manual ARM64/x64 uv installation:
MSYS2 supplies Git/Make/shell utilities while uv selects native Windows x64
CPython 3.12, including under Windows ARM emulation. Rust/MSVC and Dioxus tooling
prepare for the Dioxus trial. The guide reuses existing Makefile
targets and identifies required Windows work. The scanner's unconditional
`fcntl` import can prevent GUI startup, the writer rejects Windows, and native
GUI integration and executable/installer targets remain incomplete. This
documentation adds no Windows runtime or packaging support.

## macOS packaging

`make dmg-signed` calls `make dmg` with `--signing-identity` in `ARGS`, selecting
the first Developer ID Application certificate hash from
`security find-identity -v -p codesigning`. `SIGNING_IDENTITY` overrides this
default; an empty identity or `-` fails before building. `make list-signatures`
prints the same command's full list of valid code-signing identities. Existing
`ARGS` are retained, with the selected signing identity appended last.

`make ruff` passes a NUL-delimited list from `git ls-files --cached --others
--exclude-standard` to `uv run --locked ruff check --config pyproject.toml`. It is a required prerequisite
of `make check` and `make dmg`, and CI and source-release builds also run it.
Ruff retains its default error rules (`E4`, `E7`, `E9`, `F`), including unused
imports/variables and assigned lambdas; no per-file suppressions are
introduced. Pylint remains a complementary check.

`make syntax-check` uses standard-library `compileall` on `src`, `scripts`,
`tests`, and `e2e_tests`. It is a prerequisite of `make check` and `make dmg`;
the regression with a stray filename before the build script's opening
docstring fails this check before packaging starts.

`scripts/build_macos.py`, invoked by `make dmg`, uses project-local PyInstaller
dependencies, creates the app icon from the existing PNG, collects runtime
resources and dependency notices, declares `.mailarchive` document registration,
and signs the resulting bundle ad-hoc unless a signing identity was supplied
or both optional signing secrets are present. After PyInstaller collection, the
builder replaces its deduplicated OpenSSL pair with the two libraries named by
the installed ClamAV engine, rewrites `libssl` to load the adjacent `libcrypto`,
and signs both before resealing the app. This avoids an older Python-provided
`libssl.3.dylib` lacking symbols required by ClamAV; the mounted self-test
still checks actual native loading. The builder also re-signs `freshclam` as a
child executable, disabling hardened-runtime Team-ID validation for ad-hoc
development images. The mounted test launches its `--version` command with a
temporary configuration pointing at certificates in the mounted app, never the
host's `freshclam.conf`; this verifies that the updater and its bundled
libraries load. The runtime and development updaters use the same config writer.
`scripts/macos_signing.py` imports
`APPLE_CERTIFICATE_P12_BASE64` using `APPLE_CERTIFICATE_PASSWORD` into a temporary
keychain, selects exactly one valid Developer ID Application identity, and
restores the original keychain search list and deletes the imported key in
`finally`. Native keychain errors omit secret-bearing command lines and output.
Passwords remain visible in `security` process arguments. Automatic import
requires the GitHub Actions hosted-runner environment; local signing uses an
explicit existing keychain identity. GitHub supplies `GITHUB_ACTIONS` and
`RUNNER_ENVIRONMENT` to every step as [default environment variables](https://docs.github.com/en/actions/reference/workflows-and-actions/variables#default-environment-variables);
neither the build nor the lifecycle test needs a workflow override. The workflow gates its secret-bearing step
on the hosted-runner context as well. These checks prevent accidental shared
runner use, not hostile code within the same job.
The builder signs and verifies the completed DMG before publishing the candidate.
Missing either secret emits `::warning::` and produces `*_UNSIGNED.dmg`; invalid
configured credentials fail. An explicit `--signing-identity` overrides secrets;
`-` emits a distinct warning identifying that deliberate unsigned override.
The release workflow builds the DMG on its hosted macOS runner, passes protected signing and
App Store Connect notarization credentials only to its packaging step, submits
the signed image through `make notarize-dmg`, staples and validates it, and then
retests the mounted artifact before assembling the source and DMG checksums into
a draft release. Missing credentials leave no publishable DMG and fail the
release job. Assembly checks out the Mac job's verified
commit and checks that the tag still names that commit. Both jobs validate the
tag reference, checked-out commit, annotation, and project version before
installing dependencies. Unsigned annotated tags are accepted without a public-key
allowlist or GitHub signature verification. Repository permissions control
workflow changes and release-tag creation. `make test-signing` runs ordered focused lint/type checks and
regressions for credential selection, malformed input, identity ambiguity,
unsigned naming/warnings, sanitized errors, shared-runner rejection, and release
artifact ordering. A real Git fixture executes both workflow commit checks and
the tag/version validator: unsigned annotated tags pass; lightweight tags,
version mismatches, and a different checked-out commit fail.
Real Developer ID import/signing and hosted GUI execution require a credentialed
Mac release trial; pure policy tests do not establish those properties.

Release metadata is parsed with `packaging.version.Version`, rather than a
home-grown version regular expression. Package metadata and About use its
canonical spelling unchanged; Git adds only the `v` tag prefix. The supported
forms are stable `MAJOR.MINOR.PATCH`, alpha `MAJOR.MINOR.PATCHaN`, and beta
`MAJOR.MINOR.PATCHbN`; the parser rejects every other spelling or form. Alpha
and beta releases use Sparkle's `preview` channel and stable releases use the
default channel, with separate monotonically increasing numeric Sparkle build
values. The fixed website appcast starts empty; the Sparkle publication step
adds only a post-notarization, Ed25519-signed item.
`make sparkle-tools` pins Sparkle 2.10.0 and its upstream SHA-256, then places
the verified developer archive below `.tools/sparkle/`. `make sparkle-keys`
calls Sparkle's local `generate_keys`; the key generator retains the private
Ed25519 material in the developer's login Keychain and prints the public key.
The ordinary test suite skips the real-signer integration when these developer
tools are absent; `make test-sparkle-signing` installs them and requires that
integration test to run during release assembly.
The macOS bundle carries that public key and the fixed Pages appcast URL. The
appcast writer invokes `sign_update --ed-key-file -` on the final stapled DMG,
passing the protected exported Sparkle key only on standard input. Sparkle
2.10 explicitly supports this stdin form. New-format exported seeds decode to
32 bytes; legacy exports may decode to 64 bytes. Neither the key nor Apple's
credentials reach PyInstaller or mounted-app test subprocesses.
The release workflow creates a draft with the signed/notarized DMG, reads the
existing appcast from `main`, signs and prepends the new item, and commits the
feed to `main` with `[skip ci]`. It then publishes the draft, marking alpha and
beta tags as prereleases, and explicitly dispatches Pages from `main`.
GitHub's workflow token does not trigger a Pages run through its own commit or
release event; the feed commit alone does not deploy a URL pointing at a draft asset.
Preview entries have Sparkle's `preview` channel and stable entries remain in
the default release channel.
`scripts/desktop_entry.py` dispatches normal GUI launch, `--cli`, `--self-test`,
and `--self-test-gui`. Frozen GUI resources use PyInstaller's bundle root;
the verifier's actual `.py` source is explicitly bundled for archive installation.
The Cocoa document delegate routes quit through the shared stop/checkpoint policy
and closes pywebview windows only after processing workers finish.

`scripts/dmg_layout.py` uses build-only `dmgbuild` to write the Finder `.DS_Store`
and background into the image without changing global Finder preferences.
AppKit draws a 2x-resolution TIFF at a logical 720-by-420-point size. The two
scales are linked by the bitmap's logical size; no additional drawing transform
is applied. `make test-packaging` renders the background and checks its logical
and pixel dimensions, ink bounds, and presence of title, arrow, and instructions.
The same pixel check runs before image creation and against the mounted image,
catching the double-scaling regression that metadata-only checks missed. The
real icons are 128 points, positioned app-left and Applications-right; the
background contains the title, arrow, and install/eject instructions.
Finder's outer window is 720 by 480 points, reserving 60 points for window
chrome so the background footer is not cropped if Finder shows its status bar.
The mounted build test decodes `.DS_Store` to verify layout metadata and
requires exactly the app and Applications as visible root items.
`make preview-dmg DMG=...` opens the mounted image in Finder for visual review
and ejects it when Return is pressed. This preview does not install the app.

`self_test.py` uses temporary source and archive fixtures plus isolated application
preferences. It verifies ingest, original bytes, FTS search, fixity, idempotence,
and not-scanned evidence. The visible mode exercises production window bridges
and cancels the real source picker after inspecting its warning banner.
After GUI shutdown, it joins non-daemon workers within one five-second budget.
Remaining workers produce a failed JSON report with their names, a Python thread
dump, and immediate nonzero test-process exit. The 120-second watchdog also
marks failure and dumps stacks. `make test-self-test` validates the worker gate
with real finishing and blocked threads plus the existing packaging tests.
The builder announces each mounted test, including its visible windows, and
requires the subprocess to exit successfully before accepting its report.
The DMG build stages the app, Applications symlink, and instructions, mounts
the compressed candidate read-only, verifies its seal, runs the frozen headless
test with a system-only PATH, and detaches in `finally`. `make check-release`
adds `--check-release` to enable the frozen GUI test; `DMG=path` adds `--test-dmg`
to validate an existing image. Ordinary builds and `make test-dmg` open no GUI
test windows. A normal rebuild removes any stale GUI report for its output path.
It publishes the candidate and requested JSON reports only on success.
The Mach-O audit reads library import load
commands explicitly, excluding `LC_ID_DYLIB`, which `otool -L` also displays.
A compiled-library regression proves that an alternate self install name
passes while a real unresolved import still fails. See [MACOS_DISTRIBUTION.md](MACOS_DISTRIBUTION.md)
for commands, limitations, and Apple's renewal/notarization steps.

### Embedded scanner configuration

MAILARCHIVER_CLAMAV_LIBRARY and MAILARCHIVER_CLAMAV_DATABASE select explicit
development resources. MAILARCHIVER_CLAMAV_CERTIFICATES selects signature trust
material, MAILARCHIVER_FRESHCLAM the updater, and MAILARCHIVER_CLAMAV_UPDATES a
private update root. Frozen resource resolution uses the bundle rather than
Homebrew. Bundle assembly and CI provisioning still require migration; see
EMBEDDED_CLAMAV.md. Legacy MAILARCHIVER_CLAMD settings are ignored.


Current MIME traversal uses the standard-library parser without explicit size,
recursion, time, or decompression limits. Plain text and rendered HTML are
indexed first for normal mail; optional extraction covers decoded text
attachments only. Future binary attachment adapters need explicit bounds and
must record failures without affecting preservation. Quarantine categories are
omitted from FTS entirely.

## Planned remote sources

### Archive source registry and import actions

The planned top-level `archive.yaml` is an operational per-archive file, not a
payload file or portable fixity assertion. A strict Pydantic configuration
model will use a `kind` discriminator over `FileSource`, `LocalFolderSource`,
and `ImapSource`; reject unknown keys, duplicate IDs, invalid ports, relative
ambiguity, and secrets embedded as values; and preserve source order for the
user interface. Source checkpoints and observations remain in
`archive.sqlite3`, rather than being rewritten into YAML after each import.

```yaml
version: 1
sources:
  - id: takeout-2026
    kind: file
    path: /Users/your.name/Downloads/takeout-mail.mbox
  - id: historical-mail
    kind: local-folder
    path: /Volumes/Archive/Old Mail
  - id: personal-imap
    kind: imap
    server: imap.example.org
    port: 993
    username: your.name@example.org
    tls: implicit
    authentication: password
    credential_ref: keyring://mail-archiver/personal-imap
    folders: all
```

`credential_ref` is an identifier, never the password or token. Password
authentication prompts through a no-echo UI when the keychain item is absent;
OAuth profiles instead start system-browser authorization and retain their
tokens through the same secrets boundary. Noninteractive missing credentials
fail closed.

The planned **Import/Refresh** action enumerates every enabled source. For
`file` and every known file found beneath `local-folder`, it compares the
current nanosecond modification time with the last completed checkpoint. An
unchanged value skips opening and hashing that file; directory traversal still
discovers new paths. This deliberately misses a content change that preserves
mtime. IMAP Refresh uses `UIDVALIDITY`, UID, `UIDNEXT`, and `HIGHESTMODSEQ`
where supported and advances a checkpoint only after durable canonical
publication.

**Import/Rebuild** uses the same source registry and publication pipeline, but
bypasses the local mtime shortcut and hashes every local source file. It can
stop after a matching complete digest or reprocess changed content. IMAP
Rebuild performs a complete selected-folder/UID reconciliation; a changed
`UIDVALIDITY` is disclosed and handled through that same path. Neither action
deletes canonical mail or defeats message-level deduplication. This terminology
is distinct from `refresh-index`, which reads canonical MBOX and replaces only
derived search data.

The Google authorization prototype, mailarchiver-auth entry point, client
registration workflow, and its Google/Requests/keyring/DNS dependencies were
removed. Future live ingestion will be reimplemented; current Gmail acquisition
uses Takeout or complete local Apple Mail cache files. Historical registration
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

Gmail, IMAP, O365, Microsoft Exchange, and NUL-delimited standard input have
manifest-loaded reserved source plug-ins. They recognize only their explicit
source forms and raise a clear unavailable error. The generic provider pipeline
already accepts virtual containers, source-native cursors, provider integrity
evidence, per-account concurrency keys, and raw RFC 5322 messages. The reserved
adapters remain unavailable until each implements actual account/stream access
with substantive provider-local acceptance coverage.

The later Gmail API adapter will use least-privilege OAuth where the required
raw-message read scope is available, paginate message IDs, fetch raw bytes and
labels, and record Gmail ID/thread ID/labels as provenance. Incremental Gmail
API sync stores the last successfully committed history checkpoint, with a
complete-list fallback when history has expired.

IMAP will use TLS and read-only SELECT/EXAMINE where supported. It will enumerate
folders and UIDs, fetches RFC 5322 bytes without setting `\\Seen`, and stores
UIDVALIDITY plus UID so server reset/reuse is detectable.

The `--days N` option uses `newer_than:Nd` on `messages.list`; `--after`
accepts an epoch for timezone-precise collection. Google Takeout is an MBOX
directory input. The program does not automate personal Takeout creation or
download and does not yet extract Takeout ZIP parts.

## Public corpus validation pipeline

`mailarchiver-validation` loads strict Pydantic models from
`validation/datasets/*.toml`. The nine definitions cover Enron, SF-LOVERS,
SpamAssassin, bounded GNU emacs-devel, IETF-822, Apache httpd-dev, and GCC list
samples, a pinned lore.kernel.org public-inbox repository, and the historical
`comp.mail.mime` Usenet group. TREC07 and W3C mailbox exports are not configured
because their current official bulk routes are unavailable or authenticated;
the pipeline does not silently substitute account-gated mirrors.

Acquisition streams HTTP responses to temporary files and atomically installs
them after optional expected-digest validation. The source manifest records the
observed SHA-256 for every HTTP artifact and the resolved commit for Git. Safe
extractors reject traversal, links, special nodes, and oversized output. Normal
message/MBOX preparation uses hard links when possible and copies otherwise;
public-inbox Git blobs are streamed through one `git cat-file --batch` process.
Individual-message files with Unix MBOX envelope lines are parsed into derived
RFC 5322 files instead of being mistaken for multi-message mailboxes.
Babyl-to-RFC conversion is a derived SF-LOVERS preprocessing step, with every
downloaded source retained separately.

`make validation-run` invokes the ordinary ingest CLI and its private on-demand
ClamAV process, executes the verifier installed inside the resulting Mailbag,
then writes `data/results/<dataset>.mailbag.zip` and a hash-bearing JSON run
report. `make validation-run-all` performs this workflow sequentially. Downloads,
extractions, prepared inputs, Mailbags, reports, and ZIP files all remain beneath
the ignored repository-local `data/` tree.

`validation/template.yaml` is a SAM control plane, not an EC2 emulation. It
creates a Lambda launcher, egress-only worker security group, instance profile,
and least-privilege result-upload policy; the result bucket is an external stack
parameter. `make validation-aws-start-all` invokes the launcher once per enabled
configuration. Each invocation starts one Ubuntu EC2 instance with encrypted
delete-on-termination EBS, required IMDSv2, an on-demand ClamAV configuration,
and `InstanceInitiatedShutdownBehavior=terminate`. User data checks out the
configured public repository ref, runs the same Make target, uploads status and
logs plus any report and ZIP under a run-specific S3 prefix, and shuts down from
an EXIT trap. There is no SSH ingress or persistent worker fleet.

## Source adapters and planned derivatives

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

The Eudora adapter treats mailbox data, table-of-contents files, attachment
directories, and embedded-content directories as one source package while
retaining the physical origin of every recovered component. The IMAP-cache
adapter has layout-specific readers and emits explicit incomplete records for
headers-only placeholders, evicted bodies, and detached parts. It never falls
through to a network fetch.

Printed-email PDFs follow the staged, source-preserving plan in
[PDF_OCR_STRATEGY.md](PDF_OCR_STRATEGY.md). OCR text remains derived evidence;
it is not published as byte-preserved RFC 5322 mail. Canonical PDF payload and
document-derived catalog support require an explicit archive-format decision.

Redaction is a separate derivative pipeline over hash-verified canonical
messages. A versioned Pydantic policy selects header values, body spans, MIME
parts, attachments, or derived entities; the exporter writes a new corpus plus
an access-controlled audit manifest linking each output to its canonical hash
and transformation decisions. The canonical archive and catalog remain
unchanged. Research tables likewise remain rebuildable and carry extractor,
schema, and policy versions so correspondent, thread, entity, attachment, and
provenance reports can declare how they were produced.

## Validation and tests

`scripts/check_copyright.py` enumerates Git-tracked files and checks the
explicit project-owned, comment-safe policy without rewriting anything.
`make copyright-check` runs it as part of `make check`. The exclusions protect
canonical mail and test fixtures, binary/data files, the generated release
index, the vendored Tabulator tree, and the website theme (which carries its
own project license file). Upstream CC-BY-SA artwork and generated shared-workflow
wrappers are also excluded; Python stubs and JavaScript modules receive native comments.
Missing notices produce a warning listing the affected paths and exit status 0,
so `make check` and source builds continue. Checker execution failures still
propagate normally. `make test-copyright` exercises the ownership boundaries and
runs the real checker in temporary Git repositories, verifying that a dependent
Make target runs with both missing and complete notices.

`scripts/check_runtime_licenses.py` starts with the installed `mailarchiver`
distribution and follows evaluated PEP 508 runtime requirements, rather than
inventorying the development environment wholesale. It records typed package,
version, license, and complete-license-file paths; rejects missing license
evidence and development-only packages in the runtime
closure; and can copy exact license files plus a JSON inventory into a binary
staging directory. This does not establish license compatibility.
`make runtime-license-check` is a CI and release gate.
`make runtime-license-bundle LICENSE_OUTPUT=PATH` is the packaging interface;
it creates a complete notices directory containing the project and third-party
notices, typed inventory, and collected license files. The current macOS builder
retains its existing notice collector; wiring the audited bundle into frozen
macOS and Windows builds remains pending. The audit runs
independently on each target platform because pywebview's closure is
platform-specific.

The native About metadata uses the same copyright notice. PEP 639
`license-files` metadata places the repository notices and complete licenses
for stored Tabulator and website-theme code in source distributions.
`make test-native-setup` exercises the current owner email include/exclude editor
before antivirus confirmation and verifies the saved rules after a synthetic
import. Setup reuses File Import's current revision-checked owner-rule workflow.

The Cocoa termination delegate confirms active ingest, then returns
`NSTerminateCancel` while a background waiter arranges orderly window closure.
The event loop stays alive while `IngestJob.stop` requests cooperative cancellation. The shared service checks this event during discovery,
scanner startup, and worker status refresh; ordinary worker failures remain
distinct from cancellation. It follows the existing interrupted-run checkpoint
and lease-release path. Completion events and worker joins allow pywebview window closure only after
the GUI workers finish, including their final callbacks. The incomplete-work prompt offers resumption; File → Import also safely
retries the same source. `make test-application` tests partial publication,
interrupted status, verification, duplicate-free restart, and multi-document stop.

`make test-corpus-import` runs the single full-directory regression in
`tests/test_corpus_import.py`, also included in `make test`. It imports the
actual `tests/data` directory with `--clamav`, compares subjects/raw SHA-256
and per-source accounting against `tests/expected-corpus.json`, independently
checks canonical locations and the installed verifier, then reimports and
checks unchanged-source skipping. Each subprocess has a 600-second deadline
and a retained pytest-temporary log. The test fingerprints all input files
before/after; it never changes sources.
The configured on-demand ClamAV installation is required, just as for the other
scanner integration tests. Expectations include infected mail without fixing
its signature-dependent destination; new signatures cannot excuse lost bytes.
`make update-corpus-expectations` passes `--update-corpus-expectations` to
pytest and regenerates the expected JSON only after those integrity checks.
Review the generated diff: updating a golden file is not proof of correctness.
Git-ignored local additions are recorded separately in
`.tmp/expected-corpus-private.json`; neither their mail nor their subjects belong
in the public fixture manifest. CI uses the same test on its tracked directory.
New or missing files, wrong per-source message membership, and changed exclusion
counts also fail even when the overall canonical message set is unchanged.

The reported September 7 apparent loop was a completed 208-second import:
`email-korean-bad-encoding.eml` locally contained 6,884 MBOX records (82 MiB),
despite its suffix. Content-based MBOX recognition takes precedence over `.eml`;
the worker legitimately stays on that path while advancing through messages.

[`END_TO_END_TESTING.md`](END_TO_END_TESTING.md) defines the archive-lifecycle,
browser-acceptance, native-WKWebView, and optional XCUITest layers, including
which layer owns macOS menu-bar verification.

Tests use small, hand-authored MBOX, Babyl, and EMLX fixtures covering mboxrd
quoting, bounded terminal-preamble and MMDF-framed MBOX, silent empty/metadata
files, MBOX-formatted files under Maildir paths, trimmed Received medians and
Date outliers, exact MBCP exclusion,
`From XXX` unwrapping, parser registration, missing IDs,
same-ID/different-content messages, autosaves,
duplicate source trees, interruption recovery, and infected routing. The EICAR
signature is assembled from fragments only in a temporary test source and that
file is deleted immediately after ingest; the repository contains only a safe
message template. Tests assert message identities and bytes, not only record
counts. Small synthetic rollover fixtures and recorded scanner-failure routing tests
cover those paths; EICAR checks actual scanner version evidence. The separately runnable `make test-e2e` target starts a fresh
CLI ingest with the real configured on-demand `clamd`, includes a source message
without a final newline, requires checkpoint publication, and invokes the
installed standard-library-only verifier under isolated Python.

`make test` runs the ordinary test tree. `make check` first requires clean lint
and type analysis, then runs that tree, the separate end-to-end suite, and website
validation. The tracked source corpus has enough messages to
exercise complete scoped searches and rich MIME behavior.
`make test-e2e` drives
the complete interface in headless Chromium while binding every bridge method
to the real Python service and disposable test archive. It therefore works
without a visible desktop on macOS and Linux. `make test-native-gui` separately
drives a hidden Cocoa/WKWebView window on macOS against a purpose-built
one-message derived archive to retain the native bridge boundary without
ClamAV or the full lifecycle fixture. This explicit local development target is
excluded from `make check` and CI/CD; it retains its phase report and any
timeout sample under `.tmp/native-gui-diagnostics`. Making native behavior a
required gate would need a logged-in Mac and XCUITest/XCUIAutomation. `make test-bagit`
validates the database-independent three-message fixture and corruption cases.
The installed `verify_mail_archive.py
DIRECTORY` performs read-only validation of a supplied bag. The first acceptance run is against a copied
small subset of `SLG Mail`, followed by a full read-only inventory comparison
before any canonical archive is published.

`make test-pdf-mail` runs the real Poppler text extractor against the six-page
`tests/data/sipbadmin.pdf` scan and the human-reviewed
`tests/data/sipbadmin.mbox` ground truth. It verifies four printed messages on
pages 2 through 5, explicit exclusion of non-message pages 1 and 6, the page-2
handwriting flag, reviewed subjects, generated MBOX structure and provenance,
and byte-for-byte source-PDF immutability. Ordinary pytest skips this focused
integration test when `pdftotext` is unavailable; the focused Make target
requires it and fails clearly.

## Delivery sequence

1. Create the package, configuration model, versioned fresh schema, MBOX/EMLX
   readers/writer, and `verify` command.
2. Implement recursive local ingest, exact dedupe, autosave exclusion,
   integrity files, sorting, recovery, and ClamAV routing.
3. Add `review`, `refresh-index`, FTS5 rebuilding, and conservative body/HTML extraction.
4. Add the generic IMAP importer with resumable provider checkpoints, Gmail
   and Microsoft OAuth profiles, and archive-configured Refresh/Rebuild.
5. Add provider-specific Gmail API and Microsoft Graph importers when their
   richer metadata justifies the separate transports.
6. Build the local search/view interface on the stable database and MBOX
   retrieval API.

## Developer validation gates

Scanner deadline and helper-execution regressions use real POSIX subprocesses
and explicitly skip Windows before importing the `fcntl`-based scanner. They do not establish Windows
scanner support. Pages release-trigger checks parse YAML rather than relying on
indentation, accepting PyYAML's YAML 1.1 interpretation of an unquoted `on` key.

Release assembly checks the tag's commit, then runs the standard-library
tag/version validator through `make release-tag-check` with `uv --no-project`.
Only afterward does it install project dependencies and smoke built artifacts.
The website header and navigation wrap without positional hiding rules.
Browser geometry checks allow pixel rounding; GUI selection assertions locate
the live virtual-table row by message ID after asynchronous preview redraws.
`make test-website-navigation` checks the actual header and CSS at mobile,
tablet, and desktop widths, including reordered links, in headless Chromium.

The shared pr-to-ready source generates the repository skill and Copilot
instructions. Its preflight compares proposed changes with open PRs, active
tasks, and local unpublished work, including semantic and shared-resource
overlap. Potential conflicts require a concrete list and coordination plan plus
explicit user approval; the ledger records the approved scope and subsequent
checks before integration or publication. Separate worktrees do not waive this
gate. Copilot review requests use `gh pr edit <number> --add-reviewer '@copilot'`
as `simsong`, followed by restoration of `simsong-codex` for all other writes.
Review-request timeline or reviewer evidence verifies the request; no browser
control is used. Before handoff it inventories task checkouts, reconciles intended
uncommitted changes and unpublished commits into the delivery branch, and records
the resulting commit or evidence of inclusion/supersession for each checkout.
Preservation elsewhere is not a completed integration. Its ten-minute heartbeat
changes from review monitoring to post-merge cleanup after handoff. Cleanup
fetches/prunes, verifies GitHub merge state and current-main inclusion,
checks tracked/untracked/ignored files, and
removes only safe task-owned worktrees and represented local branches. It stops
on completion or a reported preservation decision. Repository Claude entries
are generated regular wrapper files, not directory symlinks.
The explicit handoff cleanup step records each task checkout's disposition.
This repository retains unmerged checkouts unless the user authorizes earlier
removal. Such removal verifies publication against the matching PR, preserves
non-rebuildable artifacts with hashes, and checks active use before non-force
worktree removal; branch refs remain until merge verification. Retired checkout
entries are removed from the shared skill inventory to keep synchronization valid.

`make check` runs Ruff and Pylint (`make lint`), then ty and Pyright
(`make types`), then pytest, Chromium end-to-end tests, and website validation.
The stages run sequentially even with parallel make and stop on failure.
Both type checkers cover source, scripts, tests, end-to-end tests, and the AWS
launcher. Project development dependencies and type stubs are locked with uv;
static analysis must produce no errors or warnings. Focused `make ruff`,
`make pylint`, `make ty`, `make pyright`, and `make test` targets remain available.

### Desktop review follow-up

The portability audit reads each Mach-O LC_RPATH command, expands loader and
executable-relative paths, rejects search paths outside the bundle, and requires
non-system dependencies to resolve to bundled files. Missing antivirus uses a
platform-neutral confirmation on non-macOS hosts. Source-picker navigation is
saved only after successful ingest while the writer lease is retained; a failed
import leaves the previous directory unchanged.

### Writer and desktop review boundary

Current archive writing is supported on POSIX. Windows writing fails before
creating an archive or lock metadata; the secure no-reparse-point implementation
and native Windows subprocess validation are deferred to v1.1.0. The former
untested msvcrt branch is removed; no Windows locking guarantee is claimed.
POSIX acquisition pins the archive/status directories and opens lock files
relative to directory descriptors without following links. Lock files must be
regular, single-link files. New targets are created under a parent-directory
creation lock before acquiring the archive lease; creation diagnostics may leave
`.mailarchiver-create.lock` in the parent. Its presence alone does not lock anything.
GUI creation rechecks destination emptiness under the lease. File New proceeds
into Import, About reports the active archive volume, and publication refreshes
mailbox-only queries as well as text queries.

### PR review validation follow-up

Archive documents identify directories by filesystem device/inode and reject
symbolic or hard-linked database entries before opening. GUI ingest records
publication evidence even when a later step fails; only published changes
advance the shared generation. Progress can write status files without a
terminal stream, including windowed builds with no stderr. Ingest child windows
route document actions to an attached search window. Informational notices stay
in About instead of appearing as errors. Closing an import owner offers waiting
or keeping the window open. Native macOS Quit offers Cancel or Stop Import and Quit while an import is
active. Confirmed quit signals all imports, retains their leases and windows
until checkpoint completion, and then exits. Shutdown joins tracked workers
before releasing resources. Makefile Ruff checks select this checkout's configuration explicitly. Git
selects tracked and non-ignored new `.py` and `.pyi` files, so linked worktrees
are checked without descending into ignored generated directories.

An Ingests child window retains document routing after all search windows close.
New Search and Ingests use that document directly; Import creates a new search
owner when needed.

Integrity hash-standard versions must be JSON integers. The standalone verifier
rejects boolean, floating-point, and string alternatives without coercion.

## Current acquisition boundaries

No release Desktop OAuth client is bundled yet. The shared-client end-user
flow remains deferred until a maintainer supplies and validates that public
configuration in release artifacts. Current authorization requires a developer
client override. Installing that override validates and writes the same bytes.
Known consumer domains need no DNS lookup; transient DNS and token-refresh
transport failures are disclosed as errors rather than negative detection or
fresh consent. Credentials are stored only after the profile matches.

Directory import reports and skips `.partial.emlx` records while retaining
complete supported records. Direct selection of a partial record is rejected.
This is not a complete mailbox acquisition: detached attachment bytes are not
reconstructed. Do not modify the source cache; export mail through Apple Mail
when a complete MBOX source is required.

## Toolkit branding and website media

The application, installer, documentation, and website use Email Collection
Toolkit. Repository and Pages URLs use the renamed project. The platform
settings helper chooses the current application directory containing preferences.json or auth/, then an
existing legacy directory, without
moving or rewriting settings; Linux configuration,
package imports, CLI names, and archive-format identifiers retain compatibility.

`make website-screenshots` ingests purpose-made messages using the normal
archive service, then binds real Python services to the shipped HTML in
Chromium. Media targets explicitly select the dev dependency group, and search
capture waits for the rendered result-card count. It captures search and completed import history with no private mail.
`make website-gmail-illustrations` renders explicitly labeled setup diagrams
with placeholder account details. The homepage includes the unmodified
Wikimedia Commons hands/laptop SVG with visible CC BY-SA 4.0 attribution.
`searching.md` covers query syntax, suggestions, background results, attachment
limits, source filters, saved sets, and message viewing.

The macOS dependency audit reads actual dylib load commands from `otool -l`,
excluding `LC_ID_DYLIB`. A real compiled-library test verifies that an install
name alone is accepted while an executable's unresolved load of that same name
is rejected. This avoids rejecting the packaged pydantic-core library's own
identifier while retaining dependency checks.

Derived PDF exports require a `.mboxrd` output suffix; data-quality exports and
generated source fixtures also use `.mboxrd` names so re-import removes exactly
one quoting level. Unknown external `.mbox` inputs retain their conservative
interpretation. Canonical archive `.mbox` names and hash-guided recovery remain
unchanged. This prevents generated files from silently gaining quote levels.

## PST corpus acquisition implementation

The Cargo workspace includes the `pst` package with `pst/pst-downloader.rs`.
`make pst-download-plan` validates inventories without network/filesystem output;
`make pst-download` streams HTTP through reqwest, ZIP through zip and 7z through
sevenz-rust. Transfers only copy bytes; validation hashes the completed temporary
files before publication. SHA-256-addressed PST objects, URL-keyed original artifacts/receipts,
and atomic per-run/latest reports live under ignored `var/pst/`. A cache lock
serializes writers. Supplied expected hashes differ explicitly from observations.
[The contract](../pst/README.md) documents reruns, limits and unavailable links.
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

Matcher rows use 2pt vertical cell padding and compact disclosure controls, with
black matrix text and darker supporting labels. `scheduleSuggestions` dismisses
stale choices immediately. `search_selectors.py` defines typed selector entries
with recognizer/normalizer, tag, label, family, model field, and parameterized
SQL builder. The parser and search predicates dispatch through that registry.
`search_completion.py` recognizes the active selector, preserves preceding terms,
and begins work only after three value characters. A materialized Any query
resolves names/addresses once and groups observed header roles, distinct message
counts, and recency. It returns bounded address choices plus aggregate roles;
Subject and recognized Date values use the shared predicates. Read-only attached
identity data supplies current canonical names and recorded aliases; a temporary
name view avoids persistent schema changes or rebuilding content indexes.
Generic GUI tiles consume typed tags and choices, including dates. Headless tests
exercise tag menus, explicit selectors, compound terms, and date normalization.

GUI jobs distinguish ingest from content-only processing. Quit confirms only
active ingest, then a background waiter closes pywebview windows after workers
checkpoint. The Cocoa delegate returns Cancel while that orderly close runs,
avoiding Cocoa termination before Python workers finish. Final import refreshes
are suppressed during shutdown. SIGINT requests the same stop without a dialog.
Headless regressions cancel a real cooperative processor and verify retained
pending work; deferred-but-unstarted jobs never become active merely on quit.
