<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Apple Mail cache acquisition

## Conclusion

Email Collection Toolkit can import complete `.emlx` messages found below an Apple Mail
store, but a live `~/Library/Mail` directory cannot be assumed to contain a
complete copy of every server message or attachment. It is suitable as a
best-effort offline source and may be the easiest route for mail already fully
downloaded by Apple Mail. It can serve as a read-only bridge for accounts
already synchronized with Apple Mail; direct password-based IMAP is also available. It is not, by itself, proof of
a complete provider acquisition.

For a deliberately complete export, Apple's supported **Mailbox > Export
Mailbox** command produces MBOX packages. That is preferable to relying on
undocumented live-cache layout when Apple Mail has fully synchronized the
mailboxes of interest.

References:

- [Import or export Apple Mail mailboxes](https://support.apple.com/en-ie/guide/mail/mlhlp1030/mac)
- [Apple Mail attachment-download settings](https://support.apple.com/en-gb/guide/mail/cpmlprefacctinfo/mac)

The [September 2026 investigation](history/2026-09-06-apple-mail-reconciliation.md)
records one machine's structural inspection and comparison counts. Those results
are historical evidence, not a description of another user's cache.

## Current importer behavior

The local-source importer already:

- discovers complete `.emlx` files recursively;
- uses each file's leading byte count to isolate the RFC 5322 message;
- excludes Apple trailing plist metadata from the canonical message bytes;
- reports and skips `.partial.emlx` during directory import, and rejects direct
  selection, because detached payloads cannot be reconstructed byte-for-byte;
- ignores Mail databases, plist files, attachment directories, and
  `.emlxpart` fragments as independent messages; and
- derives logical mailbox names from the containing `.mbox` package hierarchy
  instead of exposing internal UUID and numeric-bucket paths.

These rules prevent known partial messages from being silently treated as
complete. They cannot prove that Apple Mail downloaded every server message.

## Compare the cache with an existing archive

```sh
ect compare-apple-mail --apple-mail /path/to/Mail --archive collection.mailarchive
```

The command reads both inputs without importing. It distinguishes exact raw-byte
matches, semantic-only matches, cache-only and archive-only messages, ambiguous
semantic matches and excluded partial records. It reports aggregate header names,
not private header values or message content. A semantic match is evidence for
review, not authorization to rewrite or deduplicate different raw messages.

See [the command-line guide](CLI_REFERENCE.md#apple-mail-comparison) for options
and the [historical investigation](history/2026-09-06-apple-mail-reconciliation.md)
for dated comparison results.

## Rerunning ingest and duplicate identity

Ingest is idempotent and is intended to be rerun as Apple Mail downloads more
complete messages. An unchanged source is skipped; a changed source is scanned
again, and a message with the same normalized `Message-ID` and raw RFC 5322
SHA-256 is not stored twice. Thus a byte-identical message found in both a
backup and the Apple Mail cache has one canonical copy with two retained source
observations.

The h3 comparison does **not** change that admission rule. If Apple Mail adds,
removes, refolds, or otherwise rewrites headers, its raw SHA-256 differs and the
current importer preserves the variant as a separate canonical record. The h3
hash records the semantic relationship for reconciliation; it is deliberately
not used to discard source variants.

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
