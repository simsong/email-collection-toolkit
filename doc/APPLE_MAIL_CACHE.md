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

The read-only inspection attempted on September 5, 2026 established only that
`/Users/simsong/Library/Mail` exists and is owner-only (`0700`). macOS denied
directory enumeration with `Operation not permitted` both inside the workspace
sandbox and during an approved outside-sandbox read. No message content was
opened, copied, changed, or ingested.

Consequently the current machine-specific inventory is incomplete: message,
mailbox, `.emlx`, `.partial.emlx`, detached-part, and attachment counts could
not be measured. Codex needs Full Disk Access before that read-only inventory
can be completed.

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
