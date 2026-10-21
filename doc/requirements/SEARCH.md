<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Search requirements

Current subsystem contract and acceptance boundaries. This is not a claim of
platform/release acceptance. Deferred features live in [plans](../plans/README.md).

## Search database

Search ignores case and combining accents and treats `ß`/`ẞ` as `ss`, without
stemming or a case-sensitivity option. The same normalization applies to query
text, indexed derivatives, subject/address/name selectors and suggestions.
Existing word indexes remain usable without reindexing: query compilation must
include native vocabulary spellings that normalize to the requested words.
Canonical bytes, hashes, displayed headers and previews remain unchanged.
Inside quotes, punctuation and symbols are significant, including prefixes,
suffixes and separators: `"CACM-"` requires the hyphen and `"Ham+Sandwhich"`
requires the plus. Candidate words retain native word/phrase boundaries;
quoted symbols are checked in normalized candidate text before counting,
ordering and pagination. Unquoted separators retain ordinary word-search
behavior. Quoted text consisting only of symbols uses a literal scan.
Attachment-inclusive searches apply each term's word and literal predicates
within the same body/header or attachment text, then intersect the terms.

`search.sqlite3` is a separate, disposable SQLite FTS5 database.  It indexes
normal Sent and Archive message SHA-256, normalized headers, `text/plain` body text when present,
otherwise rendered `text/html`, otherwise safe single-part message text.  It
also maintains a replaceable trigram index and address metadata for legacy
index consumers. Completion uses catalog header roles and live identity names,
so it does not depend on content indexing. Display
names are retained as suggestion metadata but are not trigram-indexed. Subject
completion reads the canonical subject column and requires no second copy.
It parses XML-looking content declared as `text/html` with the same forgiving HTML
rules without emitting parser diagnostics. Body/header text and attachment
text occupy separate FTS5 tables so callers can exclude attachments from the
default search. It does not index attachment bytes by default.
`--index-attachments` populates the separate table with decoded text
attachments. Binary attachment extraction through Apache Tika is not yet
implemented; the optional installer downloads, SHA-512 verifies, and unpacks
the complete Tika 4 `tika-app` distribution (JAR plus `lib/` directory).
It rejects checksum metadata unless its first token is exactly 128 hexadecimal
characters, and removes the private temporary extraction directory after any
failed extraction, layout validation, or rename.
The search database must be fully rebuildable from the
canonical MBOX files and `archive.sqlite3`, and is not backed up as a required
preservation object.
It excludes `INFECTED` and `MALFORMED` quarantine categories even when
attachment indexing is requested. `refresh-index` applies the same exclusion
when rebuilding from canonical MBOX files.
Text extraction must decode MIME payload bytes before introducing replacement
characters. A valid declared charset, including legacy labels such as
`ks_c_5601-1987`, takes precedence. If that declaration is absent, unknown, or
cannot decode the bytes, extraction may choose a bounded, quality-scored
candidate from common legacy encodings using `charset-normalizer`, with detector
ordering retained ahead of universal single-byte fallbacks. Candidate quality
is scored only on a bounded sample, which is reused for candidate discovery;
the complete part is strictly decoded only after ranking. Only a final
unrecoverable fallback may use UTF-8 replacement. `ftfy` encoding repair is
applied to already-decoded derived text to correct clear mojibake. These
repairs affect only the disposable index and display; the original RFC 5322
bytes, MIME headers, and raw-message hash remain unchanged. Recovery is
per-part and automatic; a count threshold for `U+FFFD` is not a safe trigger
because replacement has already discarded the evidence needed for recovery.
Malformed legacy headers that contain raw 8-bit bytes instead of RFC 2047
encoded words use the declared body charset through that same recovery path for
derived catalog, search, and viewer text; canonical header bytes remain exact.
For every indexed message, the disposable database records an attachment count
and one ordered metadata row per MIME attachment containing its MIME-walk part
ID, decoded filename, and normalized MIME type. This metadata is derived during
the same MIME parse as body indexing and is rebuilt by `refresh-index`; it does
not make attachment payload bytes canonical database content.
The message metadata also stores a deterministic, whitespace-collapsed preview
of the first 18 words of the preferred non-attachment body. An ellipsis marks a
truncated body.
The SHA-256 primary key in ordinary message metadata maps each message to its
message-body and optional attachment FTS row IDs. Updating or removing indexed
content must resolve SHA-256 through that ordinary index and address FTS rows
by row ID; it must never scan an FTS table by its unindexed SHA-256 column.
The canonical catalog separately indexes message SHA-256 for FTS-to-message
lookups. Unfiltered bounded date-ordered listings must use the date/message index to
select the requested page before recipient aggregation. Year-scoped reports
must express their bounds as indexed `date_utc` ranges rather than applying a
function to every stored date.
This is an archive search system, not a mail client. A GUI query must search the
complete selected collection without favoring recent messages or requiring an
archivist to request older results. The GUI directly materializes the first
2,000 headers in the selected stable order; it does not run a pre-count query,
because that would delay the first visible results and SQLite FTS5 has no
reliable approximate cardinality for arbitrary full-text/filter combinations.
When more rows exist, the GUI immediately displays that ordered prefix,
automatically retrieves the complementary remainder in the background, and
then displays the complete result set and exact count. During that continuation,
the result status is red and says **Searching in background** with the displayed
count. A newer
query supersedes the continuation; a failure retains the first 2,000 results
and reports that the background search failed. The GUI has no **Find older
ones**, **Load more**, or **Load all** interaction. Limiting an unordered FTS
hit list is forbidden because it can omit results required by the selected
ordering. Submitting an empty query displays no results and prompts for a
search. The empty result pane at application startup and after an empty query
must show the complete search language, AND and phrase behavior, every
supported operator, and concrete examples.
Index extraction and insertion happen after canonical MBOX/catalog publication.
An indexing failure is recorded as a metadata defect and does not reject mail;
`refresh-index` repairs missing disposable content.
