+++
title = "Advanced"
description = "Identity, repeatable ingest, Apple Mail cache recovery, comparison, and integrity details."
+++

Email Collection Toolkit preserves source evidence while making repeated acquisition safe.
These details matter when the same mail appears in provider exports, backups,
and working mail-client caches.

## Planned compiled desktop experience

The compiled desktop UI will evaluate Dioxus Desktop and Tauri with the system webview.
Ingest, search, and canonical archive preservation remain in Python. Windows
with full ingest is the next platform priority; Linux/snap delivery is deferred.
The current application remains Python/pywebview, and this decision is not a
Windows release announcement. See the
[architecture decision](https://github.com/simsong/email-collection-toolkit/blob/main/doc/DIOXUS.md)
for migration and validation requirements.

Plan comparable trial implementations in Dioxus and Tauri before choosing a
framework. Either approach retains the Python archive engine.

## Apple Mail as a temporary provider adapter

Until direct Gmail, Microsoft 365, and IMAP adapters are implemented, Email Collection Toolkit can read complete Apple Mail `.emlx` records from `~/Library/Mail`.
This works for any account Apple Mail has synchronized, including Gmail,
Exchange Online, Outlook.com, and ordinary IMAP.

Only complete `.emlx` payloads are accepted. `.partial.emlx`, detached
attachments, Mail indexes, and plist metadata are not treated as complete
messages. Set Apple Mail's **Download Attachments** option to **All**, let it
synchronize, and quit Mail before acquisition when practical. The terminal may
need Full Disk Access. A live cache is useful recovery evidence, but it cannot
prove that every server message was downloaded.

## Safe reruns and exact deduplication

Ingest is idempotent and may be rerun after Apple Mail downloads more messages
or after another backup is found. Unchanged sources are skipped. A message is
an exact duplicate only when both its normalized `Message-ID` and raw RFC 5322
SHA-256 match. A byte-identical message found in a backup, Google Takeout, and
the Apple Mail cache has one canonical copy while every source observation is
retained.

This exact rule is intentionally conservative. If a mail client adds, removes,
or refolds a header, the raw bytes differ and Email Collection Toolkit preserves that
variant instead of silently discarding evidence.

## Raw and semantic message hashes

The archive records two per-message SHA-256 identities:

- **h2 raw-message** hashes the recovered original RFC 5322 bytes exactly.
- **h3 semantic-message v1** applies DKIM-relaxed normalization to an ordered
  set of stable and delivery headers, combines those headers with the complete
  body under DKIM-simple-style canonicalization, and hashes the result.

Although h3 is sometimes called the “normalized message header hash,” it is
not header-only: the complete body is included. Selected fields include
`From`, `Sender`, `Reply-To`, `To`, `Cc`, `Bcc`, `Delivered-To`, `Date`,
`Message-ID`, `Subject`, `MIME-Version`, `Content-Type`,
`Content-Transfer-Encoding`, and `Content-Disposition`. Mutable fields such as
`Status`, `X-Status`, `Received`, and `Return-Path` are excluded. The exact
byte algorithm is in the [integrity controls](https://github.com/simsong/email-collection-toolkit/blob/main/doc/INTEGRITY_CONTROLS.md).

h3 is used for reconciliation and forensic lookup. It does not authorize the
importer to merge or delete raw variants.

## Compare Apple Mail with an archive

From the source checkout, run:

```console
make compare-apple-mail
```

The command compares `~/Library/Mail` with `~/mail-archive` read-only. Alternate
locations may be supplied through `ARGS`. It reports exact raw matches,
semantic-only matches, cache-only and archive-only messages, ambiguous h3
matches, excluded partial records, and aggregate header names that Apple added,
removed, or changed. It never prints message content or header values and keeps
its path/hash index in a disposable temporary database.

On the September 6, 2026 comparison, 12,426 complete cache records were exact
raw matches and 20,046 were h3 matches with different raw bytes. Of the latter,
19,537 differed only in formatting. The aggregate report found 508 occurrences
each of Apple-only `Received`, `Return-Path`, and `X-Mailer`, and 508 of
archive-only `X-Universally-Unique-Identifier`; one Gmail pair lacked
`X-GM-THRID` and `X-Gmail-Labels` in Apple Mail. These are point-in-time
results from a live cache.

## Source controls and archive verification

Source-file fingerprints and append checkpoints make normal reruns efficient.
A changed source is read again, but exact canonical identities remain
deduplicated. Every observation retains its physical origin even when several
observations refer to one message.

The canonical archive uses standard MBOX plus BagIt and Mailbag metadata.
`h1` hashes each complete MBOX, `h2` hashes each recovered raw message, and
`h3` supports semantic reconciliation. SQLite catalogs and search indexes are
derived and rebuildable. Run `make verify ARCHIVE=/path/to/archive` after
ingest or transfer; verification is read-only.

The [user manual](https://github.com/simsong/email-collection-toolkit/blob/main/doc/USER_MANUAL.md)
and [Apple Mail cache report](https://github.com/simsong/email-collection-toolkit/blob/main/doc/APPLE_MAIL_CACHE.md)
provide the complete operational guidance.
