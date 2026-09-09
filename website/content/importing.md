+++
title = "Importing"
description = "Configure local and IMAP sources, then choose a fast refresh or complete source rebuild."
+++

Mail Archiver is designed to keep an ordered list of acquisition sources in
each archive. The configured-source interface and live IMAP adapter described
on this page are planned work. The current release imports explicit local paths
from the command line; Google Takeout MBOX and complete Apple Mail `.emlx`
records can be used today.

## Sources belong to an archive

The planned top-level `archive.yaml` file gives every source a permanent ID.
That lets Mail Archiver associate later observations and checkpoints with the
same source.

| Source | Contents |
| --- | --- |
| **FILE** | One supported local mail file, such as MBOX, EML, Babyl, or EMLX |
| **LOCAL FOLDER** | A directory recursively searched for supported mail files |
| **IMAP** | A remote mailbox identified by server, port, and username |

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

The configuration contains no password, app password, access token, or refresh
token. `credential_ref` names an item in the operating-system keychain or
configured secrets provider. An interactive import prompts securely when a
password is missing. OAuth sources instead open the provider's browser sign-in.

## Import/Refresh

**Import/Refresh** visits every enabled source. It walks local folders to find
new files, but does not open or hash a previously imported file when that
file's recorded modification time is unchanged. Changed and new files are
processed normally. IMAP sources use saved folder and UID checkpoints to
retrieve new or changed messages without marking them read.

This fast path deliberately trusts modification times. It will not detect a
file whose bytes changed while its old modification time was preserved.

## Import/Rebuild

**Import/Rebuild** also visits every enabled source, but ignores the local
modification-time shortcut and recomputes the complete SHA-256 of every local
file. A matching hash can skip parsing after that check. Changed content is
processed through the normal importer. IMAP sources receive a complete folder
and UID reconciliation instead of only an incremental cursor check.

Both actions are idempotent: an exact message already found in a backup,
Takeout export, local cache, or IMAP account is not added to the canonical
archive twice. Every source observation is retained, and neither action deletes
canonical mail.

Import/Rebuild is not `refresh-index`. The latter reads messages already in the
archive and replaces only the disposable search index.

See the [user manual](https://github.com/simsong/mail-archiver/blob/main/doc/USER_MANUAL.md)
for the currently available explicit-path commands.
