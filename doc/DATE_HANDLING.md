<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Date handling

The canonical archive stores a resolved UTC date for routing, reporting, and
search. The original RFC 5322 bytes and their original headers are never
rewritten.

## Resolution order

1. Parse the RFC `Date:` header and normalize it to UTC.
2. Parse timestamp suffixes from `Received:` headers. Drop the earliest and
   latest hop when at least three are valid, then use the UTC median. With one
   or two valid hops, use their untrimmed median. A `Date:` value more than two
   days from that median is replaced by the median and marked
   `received-median`.
3. If no usable `Received:` timestamp exists and the `Date:` value is missing
   or has an epoch-like year through 1980, inspect decoded text bodies. Parse
   embedded `Date:` header lines and standard quoted forms such as `On January
   1, 2003, at 07:21 AM, … wrote:`. Choose the most recent plausible candidate
   and mark the record `body-embedded`.
4. For an otherwise undated message, use typed source metadata, the previous
   resolved date in the same input stream, or a four-digit year in a
   filesystem source path, in that order.

Dates outside the configured plausible year range are retained as source bytes
but do not control routing. Date-resolution defects are recorded in the
catalog so a later review can distinguish a header date from derived evidence.

## Bounds and evidence

`--earliest-year` sets the first plausible header year (default 1900); dates
more than one year in the future are also implausible. Resolved timestamps are stored in UTC; typed source timestamps must be
timezone-aware and are normalized to UTC.
A valid Date is retained when it is within two days of the usable Received
median. An absent/invalid Date with usable Received evidence uses that median.
The body scan is considered only without usable Received evidence and with a
missing or epoch-like Date through 1980. Choose the most recent plausible
embedded candidate using packaged localized quote patterns, replacing a usable
epoch-like Date only when the candidate is later.

The catalog records `date`, `received-median`, `received`, `body-embedded` or
`source-fallback`, `previous-message` or `path-year` as applicable. With no header date, source-specific metadata
(such as IMAP INTERNALDATE) precedes the prior stream date and path-year fallback.
Do not route undated input into an invented year-zero mailbox. Source year
fallback and parser failures remain explicit; no current-time replacement is
permitted. This routing policy is separate from synthesizing a missing MBOX
envelope, whose byte contract is in [INTEGRITY_CONTROLS.md](INTEGRITY_CONTROLS.md).
