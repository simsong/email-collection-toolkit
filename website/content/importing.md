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

Import notices stay in the window owning that import. A yellow **Extra file
detected** warning means an unrelated file, such as an editor backup, was found;
importing continues and the file is left alone. Choose **Dismiss messages** to
clear the warning. Missing or changed archived mail still produces an error.

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

IMAP imports automatically retry interrupted connections using fresh sessions
and short waits. Saved progress is retained. If recovery keeps failing, the
import pauses with a yellow circle and horizontal dash. Reconnect to the network,
then click **Resume** in Ingests. Messages already saved are not downloaded again.

The download details show the number of messages downloaded in this run, their
earliest and latest server-reported dates in UTC, and counts by year. Emails
extracted from attachments are counted separately from downloads. If the amount
remaining is unknown, the display says **ETA unavailable** rather than claiming
that downloading is finalizing.

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

## Import from the command line

Install the CLI with uv, then create an archive and import a directory:

```sh
uv tool install 'git+https://github.com/simsong/email-collection-toolkit.git'
ect-create collection.mailarchive --owner sam@example.org
ect-import --archive collection.mailarchive /path/to/exported-mail
```

For a read-only IMAP account, use the same import command:

```sh
ect-import --archive collection.mailarchive --imap
```

The first run prompts for connection settings and a hidden password or app
password. Settings are saved in the archive's `config.yaml`; passwords stay in
macOS Keychain or Windows Credential Manager. Run the command again to download
new mail and resume completed checkpoints. Use `--show-messages` for per-message
output and `--source SOURCE_ID` to select one saved account.

Generic IMAP excludes mailbox names containing `spam`. Gmail downloads All Mail
and records Gmail IDs and labels; messages found only in Spam or Trash are outside
that coverage. OAuth and a graphical account-setup interface are not available.
See [Gmail acquisition](@/gmail-authorization.md) for supported Gmail paths.

Imports scan for malware by default; the CLI needs the native antivirus runtime
and verified definitions. The desktop installers include these components.
The [command-line guide](https://github.com/simsong/email-collection-toolkit/blob/main/doc/CLI_REFERENCE.md)
covers prerequisites, options, adding accounts, interruption and verification.
A general source registry and Refresh/Rebuild interface remain
[planned work](https://github.com/simsong/email-collection-toolkit/blob/main/doc/plans/ACQUISITION.md).

## Processing saved mail

While **Processing saved mail** is displayed, ECT is finishing metadata, body and
attachment extraction, and search indexing for mail already stored locally.
The cards show finished processing tasks and their average rate. Each worker row
separates downloading/importing from processing and reports its own finished-task
count. Several processing tasks can belong to one email. IMAP source progress is
reported as download partitions rather than files.

**Messages requiring processing** counts archived emails with unfinished work,
once per email even when several tasks remain. Pending, running, failed and
blocked work is included; mail still on the server is excluded. Each email leaves
the count after its final task finishes.

## Verifying archived messages and saving checksums

This status means the application is checking saved message bytes and updating
the archive's local integrity files, message inventory and checksum manifests.
The step reads the whole archive, so it can take time after a small import while
download workers are idle. It does not upload or share email. The download
percentage does not measure this final step.
