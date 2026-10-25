+++
title = "Importing"
description = "Configure local and IMAP sources, then choose a fast refresh or complete source rebuild."
+++
<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->


Email Collection Toolkit imports supported local email files and folders into
an archive without changing the sources. MBOX (including Google Takeout),
Maildir, EML, Babyl, and complete Apple Mail `.emlx` files can be used today.
The macOS application also reads Outlook PST and OST files, with
[best-effort extraction](https://github.com/simsong/email-collection-toolkit/blob/main/doc/PST_IMPORTER.md).

## Import with the application

Open or create an archive, then choose **File → Import…**. Select local files
or folders, review **Owner emails (include)** and **Exclude (applied after include)**,
then confirm the source and destination. A directory includes supported mail
files in its subdirectories.

Every import shows both rule fields, using the archive's saved defaults. Enter
one rule per line or separate rules with commas. `sam` matches `sam@any-domain`
but not `3sam`; use `*` or `?` to broaden a match. Domains are matched only when
the rule includes `@`. Exclusions always win.

The app saves confirmed rules in `config.yaml` and writes matching sender
addresses separately to `owner-names-detected.txt`. Editing rules affects future
imports; correcting old Sent/Archive classifications requires a fresh archive.

![Owner include and exclude fields with synthetic examples](../images/owner-rules-interface.png)

*The owner-rule editor, shown here with synthetic examples. macOS uses a native
dialog with the same two fields.*

The **Ingests** window shows retained runs, source paths, message counts,
progress, worker activity, and failures. Open it from **Window → Ingests** or
click the status line; **Import Directory…** starts another import into that
window's archive. If antivirus scanning is unavailable, the application warns
you and records scanner-error evidence while importing continues automatically.
Incomplete scans and encrypted attachments are recorded without a manual-review gate.

Use **Suspend Import** to checkpoint the current work without quitting. Wait for
the suspension to finish, then use **Continue Import** to resume the saved
settings, including after reopening the archive. Suspension also frees the
archive for manual name edits. A message already being downloaded may finish
before suspension completes.

![The import history window showing a completed synthetic collection import](../images/importing-interface.png)

*The working import-history interface after importing synthetic messages.*


When reopening an archive with unfinished work, review the source, import start,
latest interruption and saved imported/duplicate counts. An unclean stop shows
its last saved progress time because the exact interruption time is unknown.
Choose **Continue Import** to resume ingest and content processing using the saved
settings, **Leave import suspended** to keep work paused, or **Stop import** to
cancel remaining work while keeping archived
messages and history. A new explicit import can read the source again; use
explicit reprocessing to regenerate previously cancelled derived work.

## Standalone IMAP CLI

The Python `mailarchiver-imap` command runs without the desktop on macOS and
Windows. `--new ARCHIVE` creates an archive and prompts for connection settings
and a hidden password/app password, saved in the native OS keyring. Later use
`--archive ARCHIVE` to synchronize once and exit. `--workers` defaults to 8;
`--show-messages` adds per-download output to five-minute status. Ctrl-C saves
completed progress for the next run.

Generic IMAP excludes mailbox names containing `spam`, ignoring case. Gmail
downloads only All Mail and records Gmail IDs and labels; Spam and Trash-only
messages are outside that coverage. Complete messages and attachments use normal
ingest, including its current attachment-extraction limits. Scheduling and the
graphical source configuration below remain planned.

## Planned configured sources

The general saved-source interface described below is planned
work. They are separate from the local-file import interface available today.

## Sources belong to an archive

The planned top-level `archive.yaml` file gives every source a permanent ID.
That lets Email Collection Toolkit associate later observations and checkpoints with the
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

Import/Rebuild differs from a search-index refresh, which reads messages already in the
archive and replaces only the disposable search index.

See [Advanced → Command Line](@/advanced.md#command-line) for terminal commands
and the full command reference.

While **Processing saved mail** is displayed, ECT is finishing metadata, body and
attachment extraction, and search indexing for mail already stored locally.
The cards show finished processing tasks and their average rate. Each worker row
separates downloading/importing from processing and reports its own finished-task
count. Several processing tasks can belong to one email. IMAP source progress is
reported as download partitions rather than files.


**Messages requiring processing** counts archived emails with unfinished work, once
per email even when several body or attachment tasks remain. Pending, running,
failed and blocked work is included; mail still on the server is excluded.
The count refreshes during processing and can increase if more saved-message
work is discovered. Each email leaves the count only after its last task finishes.
The count can stay level while earlier stages extract bodies and attachments and
queue later work for the same emails. Older runs may show Unavailable.
