<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Search implementation

Current implementation and validation boundaries. Deferred feature designs
live in [plans](../plans/README.md); historical evidence does not validate this revision.

## Header parsing and verified display

Header parsing decodes and unfolds RFC 2047 Subject values before catalog and
FTS insertion. `ect-search` displays that catalog value directly. The verified
MBOX traversal in `rebuild-index` rederives that catalog field while rebuilding
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

## Search schema and indexed projections

`rebuild-index` rebuilds from verified canonical MBOX locations without constructing
a production processor pipeline or resuming its saved queue. It atomically replaces
the disposable index and excludes quarantine mail, including when ingestion is failed.
`make test-ingest-recovery` checks that processing state and MBOX bytes stay unchanged.

`search.sqlite3` has its own packaged, versioned `sql/V1__search.sql` schema and
does not use cross-database foreign keys. `create_search()` rejects unversioned
or incompatible layouts. Ingest catches that specific condition, completes any
pending publication recovery against a temporary current-layout index, and
uses the same validated, atomic rebuild as `rebuild-index` before starting
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
than filtering the virtual tables on their unindexed SHA-256 columns. PDF and Office
attachment extraction is planned with per-format extractors and OCR in
sandboxed workers, without Apache Tika or Java (#159, #163). Rebuild the
index in a temporary database,
validate row identities against `archive.sqlite3`, then atomically replace the
old search database. Live indexing and `rebuild-index` both exclude
`INFECTED` and the reserved `MALFORMED` quarantine category; rebuild recognizes
numbered quarantine MBOX filenames.
`rebuild-index` (CLI) and File → Rebuild Search Index (GUI) share
`rebuild_search_index`. Progress is measured in bytes known before reading: the
mailbox-validation phase totals the canonical MBOX file sizes and advances per
file; the indexing phase totals catalogued `locations.byte_length` for searchable
messages and advances by each record's length. `RebuildIndexReporter` draws a
terminal bar (rate and ETA in bytes) or, given an observer, hands each tenth of a
percent to the GUI, which shows it in the search window's bottom status line via
`GuiIngestOverview.index_rebuild`. A `should_stop` poll between mailboxes and
messages (the GUI passes Quit) discards the replacement like Ctrl-C. The GUI keeps
the replaced index's attachment indexing, uses the Preferences worker count, holds
the archive writer lease, refuses while an import runs, and refreshes the
archive's search windows after publication. Indexing parses each message after
`display_entity` drops a leading `>*From ` envelope line, so rebuilt previews,
subjects and FTS text are correct for older ingests that stored one. After committing, it closes the staging
SQLite connection before atomic replacement so Windows can rename the file.
CLI permission errors retain macOS Full Disk Access guidance only on macOS;
other hosts advise checking permissions and competing open-file handles.
Its replacement file is never opened
as the live index; a `KeyboardInterrupt` rolls back catalog subject updates,
removes the incomplete replacement, and reports that the existing index is
unchanged. A bounded, ordered worker pool verifies source hashes and parses
MIME content in parallel (all detected CPU cores by default, or two if the
count is unavailable, configurable with `rebuild-index --workers`); a sole main-thread SQLite writer retains stable
catalog/update order and never shares a SQLite connection across threads.
Ordinary `ect-search` listings and reports select only `Sent` and `Archive`;
the authoritative catalog still retains every quarantine record.
Bounded date-sorted listings materialize an indexed, ordered candidate page
before joining recipient rows. Year-scoped reports use half-open ISO 8601
`date_utc` ranges so SQLite can use the date index.
Normal ingest publishes canonical MBOX/catalog state first, then attempts the
disposable FTS insertion for normal Sent and Archive mail. Extraction or indexing failure records a
`search-index` metadata defect without rolling back canonical mail.

## Date predicates and query-plan acceptance

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

## Search progress display

`gui/app.js` leaves Tabulator's empty-table placeholder disabled. The existing
search status line reports **Searching…** while the bridge reply is pending,
then the completed count, including **0 messages**. `make test-search-placeholder`
delays delivery of real search replies and verifies pending, empty and matching
results without replacing the search service.

## Natural search matching

Natural search shares `search_text.search_fold`: Unicode case folding expands
sharp s to `ss`, then NFD decomposition removes combining accents. New body
and attachment FTS writes normalize only search content; previews and canonical
mail retain their original text. The existing V1 index needs no rebuild.
For legacy content, connection-local `fts5vocab` tables supply exceptional
spellings whose normalization differs from their native token. Query compilation
expands those words into FTS phrases; more than 64 phrase combinations use
ANDed word alternatives followed by a normalized token-adjacency check.
Legacy words split by combining accents are found with adjacent fragment pairs:
every split begins with (proper prefix, next fragment) and ends with (fragment,
proper suffix), so the split expression ANDs one OR-list of each, O(n²) phrases
filtered by one indexed vocabulary lookup. It replaces both per-fragmentation
phrases and the old one-letter prefix fallback, which made a 1.2M-message archive
read 68,000 bodies for `gandalf`; pairs leave about 2,000. Native matches whose
content contains combining marks and all extra
candidates pass the normalized token-adjacency check, including Latin words.
This also rejects legacy fragments that become interior parts of a normalized word.
A bounded process cache follows the index's path, inode, size and nanosecond
timestamps, sampled across attachment/read-lock acquisition. WAL indexes bypass
that cache. The full vocabulary scan takes 13–30 s on a 7.8 GB index, so each
scan is also stored as JSON under the user cache folder (or
`MAILARCHIVER_SEARCH_VOCABULARY_CACHE`), keyed by the same stamp, keeping the
eight newest; scans are serialized, and the desktop app starts one in the
background when a window first opens an archive. This keeps vocabulary changes and atomic replacements visible.
POSIX quote intent survives parsing for symbol-containing text; `search_literal`
checks the normalized literal and its own word boundaries in each matching FTS table before counts, sorts
or limits. Symbols-only quotes scan indexed text. Selectors and suggestions use
the same registered normalization function. CLI `--query` preserves search-box
quotation through shell parsing. Case sensitivity and stemming are not options.

## Frequent-word pages

A plain text search sorted newest first counts broad FTS hits up to
sqrt((offset + limit) × indexed rows). Above that, it walks
`messages_date_message` and probes a materialized hit set, running content
checks only on hits, until the page fills; below it, it sorts every hit as
before. Both give the same page. `information` (265,000 hits) fell from 15 s to
under 1 s.

## Live grouped names

`prepare_names` builds `observed_address_names` from header evidence, search
suggestions, canonical names and manual person aliases. `address_search_names`
projects those names across current `person_addresses` membership, then unions
organization-domain names without person expansion. Every search/completion
connection reads current membership; no index rebuild is required. Literal
address matching still uses the catalog address predicate. Email-fragment
recognition permits quoted local parts while retaining name lookup for labels
such as `Jane @ Lab`.

## Cancellable autocomplete

The method-only bridge exposes suggestion start/status/cancel calls backed by
one tracked executor per window. Generation checks discard stale results and a
SQLite progress handler interrupts canceled queries. Close cancels pending work.
Subject predicates fold the stored `lower(subject)` expression, allowing existing
expression indexes to filter candidates before fetching message rows. Completion
aggregates those candidates once, with a window sum retaining the exact total
when the list is limited. No archive schema or content-index rebuild is needed.

## Function audit repairs

`mime_tree.walk_mime` carries attachment ancestry iteratively with depth-first part
IDs. Viewer body selection, `preferred_body_text`, attachment text and inventory
use that boundary; the streaming processor inventory also excludes descendants
of an already inventoried attachment. `mailsearch._search_session` shares the
read-only attached-database transaction and vocabulary stamp between page and count.
Transform-codec `LookupError` is handled by the ordinary text decoding fallback.
ID-bearing reader walks receive `OriginalMimeParts` and parse decoded RFC child
trees, matching production's preflight numbering. Failed encoded children remain
opaque, as do encoded children without original-byte context. Attachment-text
traversal receives the same original-byte context, and CLI
body selection uses shared ancestry. Streaming extraction reuses its empty spool
across consecutive opening delimiters, avoiding phantom parts and excess part counts.
Streaming digest traversal passes
the parent-dependent default MIME type before classifying each child; explicit
headers still win. The existing decoded preflight/resource limits remain intact.
`MimeReaderLimitError` propagates through search preparation and the staged rebuild;
the failed staging database is closed/removed and the old live index remains.
Shared `decoded_leaf_payload` keeps ordinary best-effort body decoding, but validates
uuencode lengths before decoding. Malformed/oversized UU bodies remain opaque wire
text rather than expanding through stdlib zero padding; explicit file export still
fails strictly. Header sender/body-date recovery and CID-only display use the same
safe leaf decoder.
