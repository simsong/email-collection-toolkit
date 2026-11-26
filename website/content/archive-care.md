+++
title = "Archive care"
aliases = ["advanced"]
description = "What is inside an archive, how to back it up and verify it, and how dates, duplicates, and settings work."
+++
<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

An archive is an ordinary folder that stays readable without this program. This
page explains what is in it and how to look after it.

## What is in an archive

Each archive is a [BagIt 1.0](https://www.rfc-editor.org/rfc/rfc8493) package
that follows [Mailbag 1.0](https://archives.albany.edu/mailbag/spec/). This is
the archive's normal format, not a separate export.

| Item | What it holds | Can it be rebuilt? |
| --- | --- | --- |
| `data/mbox/` | Every message, as standard MBOX files | No. This is the archive. |
| Checksum and BagIt files | SHA-256 hashes of every file and message | Yes, from the MBOX files |
| `archive.sqlite3` | Where each message was found | No. Treat it as private. |
| `processing.sqlite3` | Your name groupings, tags, and other decisions | No |
| `search.sqlite3` | The search index | Yes |
| `config.yaml` | Owner rules, IMAP account settings (no passwords) | No |

Messages are usually filed in two MBOX files per year, one for mail you sent
and one for mail you received. Large years roll over into numbered files.
Messages the antivirus scanner flags go to `INFECTED1.mbox` instead of being
deleted.

## Back up and verify

- Keep at least two copies of the whole archive folder on different storage.
  Include the hidden and small files.
- Do not edit files inside the archive by hand, and do not edit anything under
  `data/mbox/` while an import is running.
- Verify the archive after each import, after every copy, and from time to time
  while it is stored.

In the app, choose **File → Revalidate Archive**. It rereads every message
and file, then opens a report in your browser that lists every file in the
archive, how many messages each MBOX holds, and how many of them validate.
Imports themselves only recheck MBOX files that changed since the last import,
so finishing an import stays quick on a large archive.

From a terminal, stop any import and run:

```sh
ect verify collection.mailarchive
```

Verification only reads the archive. It checks the BagIt and Mailbag files,
every file hash, and every message hash, and reports any problem. Each archive
also contains its own `verify_mail_archive.py`, which needs only Python:

```sh
python3 collection.mailarchive/verify_mail_archive.py collection.mailarchive
```

## Duplicates and repeated imports

Two messages are duplicates only when both their `Message-ID` and their exact
bytes match. The archive keeps one copy and records every place it was found.
If a mail program changed even one header, the copies differ and both are
kept, so no evidence is lost.

## Message hashes

The archive records two SHA-256 hashes for each message:

- **h2 raw-message** covers the archived bytes exactly.
- **h3 semantic-message v1** covers the stable headers and the full body, and
  ignores headers that change in transit, such as `Received` and `Status`.

h3 helps match copies that differ only in delivery headers. It is never used to
merge or delete messages. The
[integrity controls](https://github.com/simsong/email-collection-toolkit/blob/main/doc/INTEGRITY_CONTROLS.md)
give the exact algorithm.

## Dates

Each message is filed by its `Date:` header. If that date is more than two days
from the median date in its `Received:` headers, the archive files it by the
`Received:` date instead and shows a **Date adjusted** banner with both dates.
The original header is never changed. If you know an archive cannot contain mail
before a certain year, add `--earliest-year YEAR` when importing from the
command line, and earlier dates are ignored.

Date searches use a 50-hour window: `date:2020-01-05` covers every moment that
was January 5 anywhere from UTC+14 to UTC−12. In UTC, that is January 4 at
10:00 up to January 6 at 12:00.

| Search | UTC times included |
| --- | --- |
| `date:2020-01-05` | From January 4 at 10:00, up to January 6 at 12:00 |
| `before:2020-01-05` | Before January 4 at 10:00 |
| `after:2020-01-05` | January 6 at 12:00 or later |

Because of this, adjacent date searches overlap, and a message can appear in
both. Do not add daily counts together to get a total.

## Rebuilding derived data

**File → Reprocess Saved Mail…** rebuilds the search index, extracted text, and
automatic name evidence from the archived messages. It can take a long time,
and search may be incomplete until it finishes. Your own decisions, such as name
groupings, are kept, and no mail is downloaded again. From the command line,
`ect refresh-index` rebuilds only the search index.

## Settings

- **Archive settings** live in the archive's `config.yaml`. Besides owner rules
  and IMAP accounts, `mbox_max_bytes` sets the size at which MBOX files roll
  over. The default is 3.75 GiB.
- **File → Customizable Default Lists…** edits owner-rule templates, reply-address
  patterns, contact filters, and the search highlight color. The
  [list reference](https://github.com/simsong/email-collection-toolkit/blob/main/doc/CUSTOMIZABLE_DEFAULT_LISTS.md)
  describes each list.
- **Preferences** on this computer hold worker counts, update choices, and saved
  filter sets. They do not travel with the archive.
- Passwords are kept in the operating system's credential store, never in the
  archive.

## Updates

The application can check for new versions when it opens. In **Preferences**,
choose **Release only** or **Alpha / beta / development and release**. Manual
checks are always available from **Check for Program Updates**.

Antivirus definitions update separately. Definitions older than a day are
refreshed before scanning when the computer is online, and **Check for
Anti-Virus Updates** refreshes them on request. Imported messages are not
rescanned automatically.

Versions are numbered MAJOR.MINOR.PATCH. Previews add `a` (alpha) or `b` (beta)
and a number, as in `1.0.0a1`. The archive format is already stable.
