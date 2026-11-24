<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Email Collection Toolkit

Email Collection Toolkit gathers email from old computers, backup drives, and
mail accounts into one archive that you can search today and still read decades
from now. It never changes the mail it reads. Messages are kept in standard MBOX
files inside a [BagIt](https://www.rfc-editor.org/rfc/rfc8493) /
[Mailbag](https://archives.albany.edu/mailbag/spec/) package, with SHA-256
checksums to detect damage and a record of where every message was found.

**Status: alpha.** It is safe to test with your email, and the archive format is
stable. Usability and performance are still being improved, so it is not yet
ready for production use. Feedback is welcome.

## What it reads

- MBOX files, including Google Takeout exports
- Maildir folders and `.eml` files
- Apple Mail messages
- Outlook `.pst` and `.ost` files
- Emacs RMAIL (Babyl) files
- IMAP accounts, including Gmail, read-only

## Desktop application

We build traditional desktop applications for Windows and macOS. Both include
an embedded Python runtime, so you do not need to install Python. Both applications bundle the Rust PST reader and the independent OST converter;
Outlook and separate import-tool installations are not required.

The toolkit imports email from local files and downloads email from IMAP servers
without changing the source mail. Messages are stored in MBOX files, typically
two per year: one for messages sent by the archive owner and one for messages
received. Large collections roll over into additional numbered files.

Program updates use Sparkle on macOS and WinSparkle on Windows to check for new
versions and download updates. The embedded ClamAV antivirus engine scans
imported mail; its virus definitions can be updated when the system is online.
See [Versioning and updating](#versioning-and-updating) for update choices.

## Goals

Email Collection Toolkit is preservation infrastructure for personal and research email
collections, not a mail client or a compliance appliance. Its goals are to:

* collect email from backup drives and active mail accounts into one long-term,
  deduplicated Mailbag archive whose standard MBOX and checksum files can be
  inspected with ordinary tools;
* provide one local search interface across decades of mail, regardless of the
  programs and providers that originally stored it;
* support creating redacted copies for confidentiality, donor, privacy, and
  public-release needs without modifying the original archived messages;
* maintain structured, rebuildable metadata for reproducible research reports
  about correspondents, timelines, threads, attachments, and where each message
  came from; and
* import mail from several programs, backup formats, or accounts into the same
  archive: Outlook `.pst`/`.ost`, Eudora, working IMAP client caches, MBOX, EML,
  Maildir, Apple Mail, Gmail exports, and read-only IMAP accounts.

“Plain-text archive” describes the standard, inspectable RFC 5322/MBOX
container. Binary attachments remain MIME-encoded so their original bytes are
preserved. Redaction and analysis outputs are derived products: they must be
recreatable and must never silently replace or rewrite canonical mail.

## Current status

Available now:

- Local import from MBOX, Emacs RMAIL Babyl, EML, Maildir, and complete Apple Mail
  `.emlx` messages.
- Outlook PST import through the Rust reader and OST import through a separate
  converter on macOS and Windows. These readers report partial extraction and reconstruction
  limits; see [PST/OST reader limits](doc/PST_IMPORTER.md).
- Read-only IMAP downloads on macOS and Windows through the standalone CLI,
  including Gmail accounts that support password-based IMAP access.
- Local search and message viewing, duplicate detection, source provenance,
  integrity verification, and interrupted-import recovery.
- Embedded antivirus scanning and online virus-definition updates.

Still unfinished or planned:

- Eudora-specific and general working IMAP-cache adapters, direct Gmail API/OAuth
  acquisition, and Microsoft 365 OAuth acquisition.
- A desktop account-setup interface and scheduled IMAP downloads.
- PDF/Office attachment text extraction, redacted derivatives, richer research
  reports, and archive sorting/repacking.

You do not need to read a manual to get started — see [Quick start](#quick-start)
below. For detailed format notes, troubleshooting, and archive care, see the
[user manual](doc/USER_MANUAL.md) and the [documentation guide](doc/README.md).
Name-matching experiments live in [`research/name-matcher`](research/name-matcher),
with their [research notebook](doc/research/name-matcher/README.md).

## Quick start

**Desktop app:** Download and install the app below, open it, create a new
archive, and point it at a folder or mail export (`.mbox`, `.eml`, Maildir,
`.pst`/`.ost`, or Apple Mail). Your original files are never modified.

**Command line:** Install with [uv](https://docs.astral.sh/uv/getting-started/installation/)
and archive a folder of exported mail in a few commands:

```sh
uv tool install 'git+https://github.com/simsong/email-collection-toolkit.git'
ect-create collection.mailarchive --owner sam@example.org
ect-import --archive collection.mailarchive /path/to/exported-mail
ect-search --archive collection.mailarchive budget
ect verify collection.mailarchive
```
Text search inside PDF and Office attachments, Gmail and Microsoft 365 sign-in
without an app password, and redacted copies are planned.

## Install

Download the signed installer for macOS (Apple Silicon) or Windows (x64 or ARM)
from the [project website](https://simsong.github.io/email-collection-toolkit/).
Python and the virus scanner are included; Windows also needs the Microsoft
Edge WebView2 Runtime, which most Windows 10 and 11 computers already have.
Then follow the
[Quick start](https://simsong.github.io/email-collection-toolkit/quick-start/).

To use the command line instead, install
[uv](https://docs.astral.sh/uv/getting-started/installation/) and run:

```sh
uv tool install 'git+https://github.com/simsong/email-collection-toolkit.git'
ect-create collection.mailarchive --owner sam@example.org
ect-import --archive collection.mailarchive /path/to/exported-mail
ect-search --archive collection.mailarchive budget
ect verify collection.mailarchive
```

The command line uses ClamAV for virus scanning, which you install separately
(for example `brew install clamav`), then run `ect update-definitions` once.
Add `--no-scan` to try it without ClamAV.
See [Command line](https://simsong.github.io/email-collection-toolkit/command-line/).

## Documentation

- [User Manual](https://simsong.github.io/email-collection-toolkit/manual/):
  importing, searching, archive care, the command line, and troubleshooting.
- [Command-line reference](doc/CLI_REFERENCE.md): every command and option.
- [Release notes](doc/RELEASE_NOTES.md): what changed in each version.
- [Developing](doc/DEVELOPING.md) and [Releasing](doc/RELEASING.md): building,
  testing, and publishing. The [documentation index](doc/README.md) covers how
  the archive works inside.

## Community

Ask questions and suggest features in the
[Community Forum](https://github.com/simsong/email-collection-toolkit/discussions),
and report bugs in the
[issue tracker](https://github.com/simsong/email-collection-toolkit/issues).

## Copyright and licenses

Email Collection Toolkit is distributed under the [GNU GPL version 2 only](LICENSE).
Additional licenses are available from the copyright holder.

Original project material is covered by [COPYRIGHT](COPYRIGHT). Vendored and
separately licensed components are identified in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). [doc/LICENSING.md](doc/LICENSING.md)
explains the licensing choices, lists every bundled component, and gives the
license of each shipped package.
