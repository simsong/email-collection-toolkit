<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Search implementation

Current implementation and validation boundaries. Deferred feature designs
live in [plans](../plans/README.md); historical evidence does not validate this revision.

## Header parsing and verified display

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

## Live grouped names

`prepare_names` builds `observed_address_names` from header evidence, search
suggestions, canonical names and manual person aliases. `address_search_names`
projects those names across current `person_addresses` membership, then unions
organization-domain names without person expansion. Every search/completion
connection reads current membership; no index rebuild is required. Literal
address matching still uses the catalog address predicate. Email-fragment
recognition permits quoted local parts while retaining name lookup for labels
such as `Jane @ Lab`.
