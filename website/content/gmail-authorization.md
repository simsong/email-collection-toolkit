+++
title = "Archive Gmail"
description = "Use Google Takeout or read-only IMAP to preserve Gmail."
+++
<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

## Google Takeout

Use [Google Takeout](https://takeout.google.com/) for an offline snapshot. This
does not grant Email Collection Toolkit access to your Google account.

1. Choose **Deselect all**, then select **Mail**.
2. Select the mailboxes to preserve, then create a one-time export.
3. Download every ZIP part and extract them under one directory.
4. Import that directory with the application or `ect-import`.
5. Verify the resulting archive and retain the original export as a backup.

```sh
ect-create gmail.mailarchive --owner sam@gmail.com
ect-import --archive gmail.mailarchive /path/to/extracted-takeout
ect verify gmail.mailarchive
```

See [CLI installation and prerequisites](https://github.com/simsong/email-collection-toolkit/blob/main/doc/CLI_REFERENCE.md#install)
before using these terminal commands. Large exports can be split across ZIP files;
direct ZIP import and automatic Takeout creation/download are not implemented.

## Read-only IMAP

For accounts that permit app-password IMAP access:

```sh
ect-import --archive gmail.mailarchive --imap
```

The first run prompts for connection settings and a hidden app password. The
password stays in the native operating-system credential store. Later runs
resume saved progress and download new messages without changing Gmail flags.
Gmail acquisition downloads All Mail and records labels; messages found only in
Spam or Trash are outside this mode. Google controls app-password availability;
check its [current account guidance](https://support.google.com/accounts/answer/185833).

## Other acquisition paths

A synchronized Apple Mail cache is a best-effort source. Complete `.emlx` messages
can be imported, but partial records and missing detached attachments prevent a
completeness claim. See [Apple Mail cache limits](@/advanced.md#apple-mail-as-a-temporary-provider-adapter).

Direct Gmail API and OAuth acquisition are not available. End users do not need
to register a Google application. The [provider guide](https://github.com/simsong/email-collection-toolkit/blob/main/doc/GMAIL.md)
describes the current boundaries; future OAuth work belongs to the
[acquisition plan](https://github.com/simsong/email-collection-toolkit/blob/main/doc/plans/ACQUISITION.md).
