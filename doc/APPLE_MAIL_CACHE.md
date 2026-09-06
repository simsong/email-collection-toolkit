# Apple Mail cache acquisition

## Conclusion

Mail Archiver can import complete `.emlx` messages found below an Apple Mail
store, but a live `~/Library/Mail` directory cannot be assumed to contain a
complete copy of every server message or attachment. It is suitable as a
best-effort offline source and may be the easiest route for mail already fully
downloaded by Apple Mail. It is not, by itself, proof of a complete Gmail or
Microsoft 365 acquisition.

For a deliberately complete export, Apple's supported **Mailbox > Export
Mailbox** command produces MBOX packages. That is preferable to relying on
undocumented live-cache layout when Apple Mail has fully synchronized the
mailboxes of interest.

References:

- [Import or export Apple Mail mailboxes](https://support.apple.com/en-ie/guide/mail/mlhlp1030/mac)
- [Apple Mail attachment-download settings](https://support.apple.com/en-gb/guide/mail/cpmlprefacctinfo/mac)

## This computer

Full Disk Access permitted a read-only structural inspection on September 6,
2026. The Mail store was live—Mail and its Spotlight extension were running,
and the Envelope Index had an active WAL—so these are point-in-time counts, not
a transactionally consistent snapshot.

| Measure | Observed result |
| --- | ---: |
| Mail-store layout | V10; 15 account directories; 169 `.mbox` packages |
| Store disk usage | 12,464,196 KiB (about 11.9 GiB) |
| Complete `.emlx` | 102,192 |
| `.partial.emlx` | 99,663 |
| `.emlxpart` | 1 |
| Other files below attachment directories | 23,526 |
| Envelope Index `messages` rows | 196,950 |
| Envelope Index `server_messages` rows | 189,084 |

All 201,855 complete and partial records had numeric length prefixes, and no
file was physically shorter than its declared payload. This validates the
physical framing of complete `.emlx` records; it does not make a record that
Mail names `.partial.emlx` complete.

Google-style `[Gmail]` paths contained 191,816 records: 93,531 complete and
98,285 partial. Paths named `All Mail.mbox` contained 189,180 records: 91,220
complete and 97,960 partial. Comparing the opaque account-directory and numeric
record identifiers found no partial record with a complete cached counterpart
elsewhere. The local cache would therefore omit just over half of the observed
Google-style records if Mail Archiver accepted only byte-complete input, as it
must.

No message header, body, subject, address, or attachment content was read. The
audit read only path/type/size metadata, EMLX numeric first lines, aggregate
SQLite counts in read-only/query-only mode, and process state. It did not copy,
change, or ingest any source data.

The practical conclusion is that this store is useful for best-effort recovery
of 102,192 complete records, but direct whole-cache import cannot produce a
complete provider archive. Prefer Google Takeout or a provider export. If the
cache must be used, quit Mail, configure **Download Attachments: All**, allow
synchronization to finish, rerun the preflight, and ingest only with an
explicitly chosen archive target.

## Current importer behavior

The local-source importer already:

- discovers complete `.emlx` files recursively;
- uses each file's leading byte count to isolate the RFC 5322 message;
- excludes Apple trailing plist metadata from the canonical message bytes;
- rejects `.partial.emlx` because detached payloads cannot be reconstructed
  byte-for-byte;
- ignores Mail databases, plist files, attachment directories, and
  `.emlxpart` fragments as independent messages; and
- derives logical mailbox names from the containing `.mbox` package hierarchy
  instead of exposing internal UUID and numeric-bucket paths.

These rules prevent known partial messages from being silently treated as
complete. They cannot prove that Apple Mail downloaded every server message.

## Required read-only preflight

Before treating a Mail cache as a substantive source, a preflight should:

1. Record the macOS Mail-store version and source-directory metadata.
2. Count complete `.emlx`, `.partial.emlx`, `.emlxpart`, and external attachment
   files without displaying subjects, correspondents, or message contents.
3. Validate every `.emlx` length prefix and report truncation.
4. Record mailbox-package structure and aggregate counts from a consistent,
   read-only view of the Envelope Index where feasible.
5. Warn when Apple Mail's **Download Attachments** setting is not **All**.
6. Require Apple Mail to be quiescent during canonical ingest or detect and
   stop on source changes.
7. Compare cache counts against a provider export or provider API when a claim
   of completeness matters.

No real canonical archive should be populated from this Mail store until the
target archive is identified and the ingest is explicitly authorized.
