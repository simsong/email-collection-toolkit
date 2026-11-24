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

Text search inside PDF and Office attachments, Gmail and Microsoft 365 sign-in
without an app password, and redacted copies are planned.

## Install

Download the signed installer for macOS (Apple Silicon) or Windows (x64 or ARM)
from the [project website](https://simsong.github.io/email-collection-toolkit/).
Everything it needs is included. Then follow the
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
(for example `brew install clamav`). Add `--no-scan` to try it without ClamAV.
See [Command line](https://simsong.github.io/email-collection-toolkit/command-line/).

## Documentation

- [User Manual](https://simsong.github.io/email-collection-toolkit/manual/):
  importing, searching, archive care, the command line, and troubleshooting.
- [Command-line reference](doc/CLI_REFERENCE.md): every command and option.
- [Release notes](doc/RELEASE_NOTES.md): what changed in each version.
- [Developer documentation](doc/README.md): building, testing, releasing, and
  how the archive works inside.

## Community

Ask questions and suggest features in the
[Community Forum](https://github.com/simsong/email-collection-toolkit/discussions),
and report bugs in the
[issue tracker](https://github.com/simsong/email-collection-toolkit/issues).

## Copyright and licenses

Email Collection Toolkit is distributed under the [GNU GPL version 2 only](LICENSE).
Additional licenses are available from the copyright holder. Original project
material is covered by [COPYRIGHT](COPYRIGHT). Bundled components keep their
own licenses; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and
[doc/LICENSING.md](doc/LICENSING.md).
