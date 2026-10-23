<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Acquisition

**Planned work.** These requirements/designs are not current executable features.
Existing partial capabilities are identified explicitly. See the
[implementation overview](../implementation.md) for the current system.

## Per-archive sources and import modes

This general source registry and Refresh/Rebuild UI remain planned. The current
`config.yaml` stores document preferences and the standalone CLI's IMAP accounts;
it does not yet implement the general `sources` registry described below.

Each archive has a versioned top-level `archive.yaml` operational configuration
containing an ordered `sources` list. Every source has a stable, unique `id`
and exactly one of these kinds:

* `file` names one local file;
* `local-folder` names one recursively discovered local directory; and
* `imap` names one remote IMAP account and records its server, port, username,
  TLS mode, authentication mode, folder selection, and a non-secret credential
  reference.

Local paths and IMAP usernames are private operational metadata. Passwords,
app passwords, OAuth access tokens, and OAuth refresh tokens must never appear
in `archive.yaml`, either SQLite database, status files, logs, reports,
manifests, or fixtures. The credential reference identifies an entry in the
operating-system keychain or configured secrets provider. When that entry is
absent, an interactive import prompts securely for a password or starts the
configured OAuth authorization flow; a noninteractive import fails without
printing or persisting a secret outside the credential store.

**Import/Refresh** visits every enabled configured source. It walks each local
folder to discover new files, but a known local file whose recorded nanosecond
modification time is unchanged since its last completed import is not opened,
hashed, or parsed. A configured `file` source uses the same fast path. This is
an intentional performance tradeoff: a file changed while retaining its prior
modification time is not detected by Refresh. New paths and paths with changed
modification times are processed normally. IMAP sources use their native
folder/UID and version checkpoints to retrieve new or changed messages without
marking messages read or changing server state.

**Import/Rebuild** also visits every enabled source, but ignores the local
modification-time shortcut and recomputes the complete SHA-256 of every local
source file. An unchanged digest may skip parsing after hashing; a changed
digest is reprocessed. For IMAP, Rebuild performs a complete folder and UID
reconciliation rather than relying only on the incremental cursor. Both modes
remain message-idempotent, retain source observations, and never clear or
recreate canonical mail. `refresh-index` is unrelated: it rebuilds only the
disposable search database from already archived messages.

The user manual and the website importing page must show the three source
kinds, the secret-storage boundary, and a side-by-side explanation of
Import/Refresh and Import/Rebuild. Until the general registry and these GUI
actions are implemented, those pages must label this workflow as planned
and retain the current explicit-path CLI instructions.

## Additional local source adapters

* Eudora ingest recognizes mailbox files together with their table-of-contents,
  attachment, and embedded-content conventions. It records which companion
  files were present and never treats an absent or stale index as proof that a
  message or attachment does not exist.
* Working IMAP client-cache ingest is an offline, read-only source distinct from
  live IMAP. It recognizes supported cache/profile layouts, records account and
  folder context when recoverable, and explicitly reports placeholders,
  evicted bodies, partial downloads, and detached parts. It does not contact a
  server unless the user separately configures and authorizes live IMAP ingest.
  A live Apple Mail cache is best-effort evidence rather than proof of complete
  provider acquisition. Complete `.emlx` records are accepted, known partial
  records are rejected, and a cache-completeness preflight must report partial
  records and attachment policy before a completeness claim. The observed
  machine-specific access boundary and preflight are maintained in
  [APPLE_MAIL_CACHE.md](../APPLE_MAIL_CACHE.md). Complete staged EMLX remains a
  local bridge when direct account authorization is unavailable; it is not
  provider-complete acquisition.

## Legacy missing-sender remediation

Remaining missing-sender remediation is staged: first identify each legacy
Eudora/MBOX dialect from container evidence and use its reliable boundaries or
content lengths so unescaped body lines beginning `From ` cannot create false
records. Then add explicitly tagged `Reply-To` or `Return-Path` identity
fallbacks only for structurally recovered messages; those fields describe a
route and must not silently masquerade as an ordinary author. Validate each
dialect by reingesting copied fixtures into a fresh archive and comparing
record counts, source offsets, logical identities, and canonical hashes before
any derivative archive is replaced.

## Planned remote sources

### Archive source registry and import actions

The planned top-level `archive.yaml` is an operational per-archive file, not a
payload file or portable fixity assertion. A strict Pydantic configuration
model will use a `kind` discriminator over `FileSource`, `LocalFolderSource`,
and `ImapSource`; reject unknown keys, duplicate IDs, invalid ports, relative
ambiguity, and secrets embedded as values; and preserve source order for the
user interface. Source checkpoints and observations remain in
`archive.sqlite3`, rather than being rewritten into YAML after each import.

```yaml
version: 1
sources:
  - id: takeout-2026
    kind: file
    path: /Users/your.name/Downloads/takeout-mail.mbox
  - id: historical-mail
    kind: local-folder
    path: /Volumes/Archive/Old Mail
  - id: personal-imap
    kind: imap
    server: imap.example.org
    port: 993
    username: your.name@example.org
    tls: implicit
    authentication: password
    credential_ref: keyring://mail-archiver/personal-imap
    folders: all
```

`credential_ref` is an identifier, never the password or token. Password
authentication prompts through a no-echo UI when the keychain item is absent;
OAuth profiles instead start system-browser authorization and retain their
tokens through the same secrets boundary. Noninteractive missing credentials
fail closed.

The source actions and their mtime tradeoff are specified once under
[Per-archive sources](#per-archive-sources-and-import-modes). IMAP checkpoints
use `UIDVALIDITY`, UID, `UIDNEXT` and `HIGHESTMODSEQ` where supported, advancing
only after durable canonical publication.

The later Gmail API adapter will use least-privilege OAuth where the required
raw-message read scope is available, paginate message IDs, fetch raw bytes and
labels, and record Gmail ID/thread ID/labels as provenance. Incremental Gmail
API sync stores the last successfully committed history checkpoint, with a
complete-list fallback when history has expired.

The existing [standalone IMAP adapter](../implementation/ACQUISITION.md#standalone-imap-cli)
already handles verified TLS and account-scoped UID/Gmail checkpoints; the
provider API design here does not change that implemented path.

The planned Gmail API `--days N` option would use `newer_than:Nd` on `messages.list`; `--after`
accepts an epoch for timezone-precise collection. Google Takeout is an MBOX
directory input. The program does not automate personal Takeout creation or
download and does not yet extract Takeout ZIP parts.

## Local adapter design

Each source package retains the physical origin of recovered companion content.
Layout-specific offline cache readers emit incomplete records for placeholders,
evicted bodies and detached parts; they never fall through to network fetches.
The formats and completeness contract are defined above under
[Additional local adapters](#additional-local-source-adapters).
