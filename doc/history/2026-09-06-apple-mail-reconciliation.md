<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Apple Mail reconciliation evidence

**Historical record:** September 6, 2026 measurements and private diagnostic
review. Counts and findings describe that snapshot, not current completeness.
See the [current comparator guide](../APPLE_MAIL_CACHE.md) for operation.

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

The review material is stored in a project-local, gitignored review directory
under the checkout root with owner-only permissions. Each case
contains `apple-cache/`, `canonical-archive/`, and `manifest.json`; the root
contains a warning, a compact h2/header/autosave table, CSV summary, and
complete manifest. A refresh mode re-hashes every private EML before updating
these derived reports and does not reread either source store. Raw
messages are private research evidence and must not be committed or
distributed. The checked-in exporter and synthetic test are reproducibility
infrastructure; the private corpus is not part of the software distribution.
