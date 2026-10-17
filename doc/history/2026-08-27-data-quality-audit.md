<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# 2026-08-27-Data-Quality-Audit

**Historical record.** Archived October 16, 2026. Statements, counts and findings
describe the recorded revisions, not current support. Use the
[documentation guide](../README.md) for current behavior.

## Historical investigation

The following aggregate results were recorded on 2026-08-27. The generated
MBOX, CSV, and JSON evidence was deliberately not retained in the repository.

## Dates before 1984

The report contains 60 normal (`Archive` or `Sent`) messages before 1984. All
60 were reviewed from hash-verified catalog locations and exported to a
temporary MBOX with per-message evidence.

| Catalog year | Messages |
|---:|---:|
| 1904 | 3 |
| 1954 | 1 |
| 1956 | 4 |
| 1969 | 4 |
| 1970 | 23 |
| 1972 | 3 |
| 1980 | 22 |

Every bad catalog date came directly from `Date:`. In 56 messages, a valid
`Received:` timestamp supplies a credible replacement between 1993 and 2009.
The remaining four are outgoing 2002 messages with epoch-like 1969/1970
`Date:` values and no `Received:` timestamp; the adjacent messages and source
paths place them in 2002.

The resulting production rules are maintained in [DATE_HANDLING.md](../DATE_HANDLING.md).
Historical results below do not override that current policy.

The real Babyl corpus also contains a message originally dated 1982 but resent
and received in 1985. The same rule routes it by its 1985 `Received:` timestamp,
which is the relevant date for this personal archive.

## `1983-1987` source directory

None of the files in this directory appears in the catalog's `source_files`
table. Discovery skipped them before parsing because the mail files are Emacs
RMAIL Babyl containers beginning with `BABYL OPTIONS:`, while the existing
adapter recognized only MBOX files beginning with `From `.

The directory contains 119 files:

| Format | Files | Parsed messages |
|---|---:|---:|
| RMAIL Babyl | 108 | 1,216 |
| PDF | 5 | 0 |
| OCR/text derivatives | 5 | 0 |
| Empty | 1 | 0 |

The new Babyl adapter parsed all 1,216 messages with zero errors. `aliza` is a
CRLF Babyl file containing 21 messages dated 1985-09-17 through 1987-04-24.
Across all Babyl files, routing dates are: 1982: 1, 1985: 264, 1986: 280,
1987: 429, and 1988: 242. Thus the directory name is not a reliable date
constraint; no parsed Babyl message has a 1983 or 1984 routing date, and 242
are from 1988. The PDFs and OCR text are not structured mail containers and
remain unprocessed by this adapter.

## Missing senders

The normal archive had 14,969 records displayed as `(missing sender)`; another
13 were in `INFECTED`. The investigation used a deterministic random sample of
100 normal records with seed `20260827`.

This is mostly not a sender-header problem:

* 79 sampled records are Eudora/MBCP metadata stubs whose source envelope is
  `mbcp@s.eecs.harvard.edu`. They contain only fields such as `X-UID`, `Status`,
  and `X-MBCP-Flags`; they are not emails and should not be canonical messages.
* 11 are placeholder `From XXX` wrapper records. Their body begins with a
  quoted `>From actual-sender ...` record followed by the real RFC headers.
  The source adapter archived the wrapper instead of unwrapping the message.
* 4 have malformed or unusable `From:` values but recoverable `Reply-To` or
  `Return-Path` addresses.
* The remaining 6 need source-dialect handling. Several supposed MBOX envelope
  senders are ordinary body words such as `what`, `Ships`, `a`, `here,`, and
  `Marc`, proving false message splits on unescaped body lines beginning
  `From `.

The catalog concentration supports the sample: two legacy source files account
for 90.9% of the missing-sender population.

Resolution status:

1. Exact MBCP metadata-stub records are now retained as exclusion observations
   rather than published as messages.
2. Narrow `From XXX`/status wrappers followed by a quoted nested MBOX record
   are now unwrapped while retaining source-file provenance.
3. Future work must detect the relevant Eudora/legacy MBOX dialect instead of
   treating every unescaped body line beginning `From ` as a record boundary.
4. Apply fallback headers only after structural recovery. `Reply-To` and
   `Return-Path` can identify a route or responder, not necessarily the author,
   so they should be recorded with their source rather than silently treated
   as an ordinary `From:` value.
5. Reingest affected source files into a fresh test archive and compare logical
   messages, source observations, and canonical byte hashes before replacing
   any current archive derivative.
