+++
title = "Importing"
description = "Import email files, folders, and IMAP accounts into an archive without changing the sources."
+++
<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

Email Collection Toolkit copies mail into an archive and never changes the
source files or accounts. You can import the same sources again later: messages
already in the archive are not stored twice.

## What you can import

| Source | Notes |
| --- | --- |
| MBOX files, including Google Takeout | Most mail programs can export MBOX |
| Maildir folders | |
| Individual `.eml` files | A folder of them works too |
| Emacs RMAIL (Babyl) files | Recognized by content, with or without an extension |
| Apple Mail `.emlx` messages | Complete messages only; see [Apple Mail](#apple-mail) |
| Outlook `.pst` and `.ost` files | Best-effort extraction; see [Outlook](#outlook-and-microsoft-365) |
| IMAP accounts, including Gmail | Read-only download from the command line; see [IMAP](#imap-accounts) |

Files the program does not recognize are listed and skipped.

## Import with the application

1. Open or create an archive. **File → New** creates one in an empty folder.
2. Choose **File → Import…** and select files or folders. A folder includes
   everything below it.
3. Review your owner addresses (below), then confirm the sources and
   destination.

Keep the archive folder separate from the mail you are importing. Neither may
contain the other.

## Your own addresses

The archive files mail you sent separately from mail you received. To do that,
every import asks for **Owner emails (include)** and **Exclude (applied after
include)**. Enter one rule per line or separate them with commas.

| Rule | Matches | Does not match |
| --- | --- | --- |
| `sam` | `sam@example.org`, `sam@other.org` | `3sam@example.org`, `sam+tag@example.org` |
| `*sam*` | `sam.lee@example.org` | `alex@example.org` |
| `*@sam.net` | anything at `sam.net` | `sam@example.org` |
| `sam@example.org` | that address only | `sam@other.org` |

`*` matches anything, `?` matches one character, and exclusions always win.
Rules ignore case.

![Owner include and exclude fields with synthetic examples](../images/owner-rules-interface.png)

*The owner-rule editor with synthetic examples. macOS uses a native dialog with
the same two fields.*

The rules are saved in the archive's `config.yaml` and shown again next time.
After each import, `owner-names-detected.txt` in the archive lists the sender
addresses your rules matched, so you can check them. **File → Document
Options…** edits the rules between imports. New rules apply to future imports
only. To refile mail that was already imported, build a fresh archive from the
original sources.

## Watching progress

The **Ingests** window lists every import with its sources, counts, workers,
and any failures. Open it from **Window → Ingests** or by clicking the status
line. **Import Directory…** in that window starts another import.

![The import history window showing a completed synthetic collection import](../images/importing-interface.png)

*The import history window after importing synthetic messages.*

An import has three stages:

1. **Downloading or importing** reads the sources and saves each message.
2. **Processing saved mail** extracts message text and attachments and builds
   the search index. **Messages requiring processing** counts the emails that
   still have work left.
3. **Verifying archived messages and saving checksums** rereads the archive and
   updates its checksum files. This can take a while even after a small import.

Yellow notices, such as **Extra file detected** for an unrelated file in the
archive folder, are warnings only. Importing continues. Choose **Dismiss
messages** to clear them.

## Stopping and continuing

Choose **Suspend Import** to pause safely, or quit the application. Choose
**Continue Import** later, even after reopening the archive, and the import
picks up where it stopped. When you open an archive with unfinished work, you
can also **Leave import suspended** or **Stop import**. Stopping keeps
everything already archived.

If an IMAP connection times out, for example after the laptop sleeps, Ingests
shows **Timeout** and keeps its progress. Reconnect, then click **Resume**.

## Importing again

Run the same import again whenever you find more mail or a source has grown.
Unchanged files are skipped. Two messages count as duplicates only when their
`Message-ID` and their exact bytes both match, so a copy that a mail program
altered is kept as a separate variant. Every place a message was found is
recorded and shown with the message.

## Gmail

Use Google Takeout or read-only IMAP. See [Archive Gmail](@/gmail-authorization.md).

## Outlook and Microsoft 365

The macOS and Windows applications read Outlook `.pst` files and `.ost` cache
files. Outlook is not needed. Extraction is best effort, and an `.ost` cache
holds only what Outlook had downloaded, so it may not be complete. Microsoft
365 has no export like Google Takeout: export a `.pst` from Outlook for Windows,
or use IMAP if your organization allows it. The
[PST/OST notes](https://github.com/simsong/email-collection-toolkit/blob/main/doc/PST_IMPORTER.md)
describe the limits.

## Apple Mail

Apple Mail keeps local copies of messages from Gmail, Microsoft 365,
Outlook.com, and other IMAP accounts. You can import them from
`~/Library/Mail`, or export mailboxes from Apple Mail as MBOX.

Before importing from the cache, set Apple Mail's **Download Attachments** to
**All**, let it finish synchronizing, and quit Mail. Only complete messages are
imported. Partial messages are reported and skipped, so a cache can never prove
that every message on the server was saved. The terminal may need Full Disk
Access. See the
[Apple Mail cache notes](https://github.com/simsong/email-collection-toolkit/blob/main/doc/APPLE_MAIL_CACHE.md).

## IMAP accounts

IMAP download currently runs from the command line:

```sh
ect-import --archive collection.mailarchive --imap
```

The first run asks for the server, user name, and password or app password. The
password is stored in macOS Keychain or Windows Credential Manager, never in the
archive. Run the command again to fetch new mail; messages already saved are
not downloaded again. Downloads never change messages or mark them read. Gmail
downloads All Mail with its labels; other servers skip folders whose names
contain `spam`. See [Command line](@/command-line.md#imap-accounts) for more
options.

## Workers and connections

**Preferences → Maximum number of workers** sets how many processors the
import uses. The default is half the CPUs. **Maximum IMAP connections per
account** defaults to 8, which leaves room under Gmail's limit of 15 for your
other mail programs. Suspend and continue an import to apply new settings.
