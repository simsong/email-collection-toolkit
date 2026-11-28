+++
title = "Troubleshooting"
description = "Fix common import problems and report the ones you cannot fix."
+++
<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

## An import failed

Select the failed run in **Window → Activity…** to see what went wrong. The
details name the source file, the position of the message in it, and the
message's hash, so you can find the exact email. The same details are saved in
the archive's `status/ingest-*.json` files.

Fixing the cause and running the import again is always safe. Messages already
archived are not stored twice.

## A file was skipped or flagged

- **Unrecognized files** are listed at the start of an import and skipped.
- **Extra file detected** means something unrelated, such as an editor backup,
  is inside the archive folder. It is left alone and importing continues.
- **Partial Apple Mail messages** (`.partial.emlx`) are skipped because they are
  incomplete. See [Apple Mail](@/importing.md#apple-mail).

## Antivirus warnings

If the scanner or its definitions are missing, the import screen warns you.
Importing still continues, and each message is recorded as not scanned. A
message the scanner flags is kept in `INFECTED1.mbox`, never deleted. Use
**Check for Anti-Virus Updates** to refresh the definitions. Messages already
imported are not rescanned automatically.

## IMAP problems

- **Wrong password:** run `ect imap --archive ARCHIVE --check-credentials`, or
  use **Update IMAP Password…** in Activity. A new password is saved only after
  it works.
- **Timeouts or "too many connections":** the download waits and retries on its
  own. Lower **Preferences → Maximum IMAP connections per account** if another
  mail program needs connections.
- **Laptop slept:** reconnect and click **Resume** in Activity. Nothing already
  saved is downloaded again.

## Settings errors

If `config.yaml` in the archive cannot be read, the application names the file
and explains the problem. Your import history stays visible. Fix the file
before importing again; it is never replaced silently.

## Unusual MBOX framing

Some old conversion tools added a second `From ` line in front of each message,
which hides the real headers from mail readers. The importer repairs this in the
archived copy by turning the extra line into an `X-From:` header, and records
the original framing. The source file is not changed. The
[MBOX reading notes](https://github.com/simsong/email-collection-toolkit/blob/main/doc/MBOX_READING.md)
explain exactly which patterns are repaired.

## Where to look

**About** (in the application menu) shows the version, free disk space,
network status, startup errors, warnings, and current import activity.
**Show processor graph…** in About shows which file types the archive can
process.

## Reporting a problem

Report bugs through [Contact Support](https://github.com/simsong/email-collection-toolkit/issues)
and ask questions in the [Community Forum](@/discussions.md). Failure details
can contain text from your email, so read them before you share them.
