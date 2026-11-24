+++
title = "Quick start"
description = "Install the application, import your mail, and search it."
+++
<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

This page gets you from download to your first search. Email Collection
Toolkit only reads your mail. It never changes the files or accounts you import
from.

## 1. Install the application

- **macOS (Apple Silicon):** download the signed macOS installer from the
  [homepage](@/_index.md), open the disk image, and drag
  **Email Collection Toolkit** to **Applications**.
- **Windows (x64 or ARM):** download the signed Windows installer from the
  [homepage](@/_index.md) and open it. Windows also needs the Microsoft Edge
  WebView2 Runtime, which most Windows 10 and 11 computers already have.

Python and the antivirus scanner are included, so there is nothing else to
install.

## 2. Import your mail

The first time you open the application it shows three steps:

1. **Select the root folder to ingest.** Pick the folder that holds your
   exported mail. Subfolders are included.
2. **Select where your archive is stored.** Pick an empty folder. It must not
   be inside the mail folder, and the mail folder must not be inside it.
3. **Start import.** Enter your own email addresses so that mail you sent is
   filed separately from mail you received, then confirm.

The application reads MBOX files (including Google Takeout), Maildir folders,
`.eml` files, Emacs RMAIL files, Apple Mail messages, and Outlook `.pst` and
`.ost` files. See [Importing](@/importing.md) for details, and
[Archive Gmail](@/gmail-authorization.md) for Gmail.

## 3. Watch progress

The **Ingests** window shows what is being read and how far along it is. Large
collections take a while. You can choose **Suspend Import** at any time and
**Continue Import** later, even after quitting. Messages already saved are not
read again.

## 4. Search

Type a word in the search box. Combine words and filters to narrow the results:

```text
budget from:alex@example.org after:2020-01-01
```

Click a result to read it. See [Searching](@/searching.md) for every filter.

## 5. Keep a backup

Your archive is an ordinary folder. Copy the whole folder to a second disk or
backup service, and keep the original exports too. See
[Archive care](@/archive-care.md).

## Using the command line instead

The command-line tools do the same work without the desktop application.
Install [uv](https://docs.astral.sh/uv/getting-started/installation/) first,
then run:

```sh
uv tool install 'git+https://github.com/simsong/email-collection-toolkit.git'
ect-create collection.mailarchive --owner sam@example.org
ect-import --archive collection.mailarchive /path/to/exported-mail
ect-search --archive collection.mailarchive budget
ect verify collection.mailarchive
```

The command line scans for viruses with ClamAV, which you install separately
(for example `brew install clamav` on macOS) before running
`ect update-definitions` once to download virus definitions. To try the tools
without it, add `--no-scan` to `ect-import`; messages are then recorded as not
scanned. See
[Command line](@/command-line.md) for more.
