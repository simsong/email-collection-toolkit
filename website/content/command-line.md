+++
title = "Command line"
description = "Install and use the ect command-line tools."
+++
<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

The `ect` command does everything the desktop application does, plus IMAP
downloads and reports. The
[command-line reference](https://github.com/simsong/email-collection-toolkit/blob/main/doc/CLI_REFERENCE.md)
lists every option, and `ect COMMAND --help` shows them too.

## Install

1. Install [uv](https://docs.astral.sh/uv/getting-started/installation/). It
   provides Python 3.12 if you need it.
2. Install the tools:

   ```sh
   uv tool install 'git+https://github.com/simsong/email-collection-toolkit.git'
   ect --version
   ```

3. Install ClamAV for virus scanning, for example `brew install clamav` on
   macOS or `sudo apt install clamav` on Debian and Ubuntu, then download the
   virus definitions with `ect update-definitions`. The desktop installers
   include both, but the command-line tools do not. Without them, add
   `--no-scan` to imports, and messages are recorded as not scanned.

Outlook `.pst` and `.ost` imports also need the
[importer helpers](https://github.com/simsong/email-collection-toolkit/blob/main/doc/PST_IMPORTER.md),
which the desktop installers include.

## Create, import, search, and verify

```sh
ect-create collection.mailarchive --owner sam@example.org
ect-import --archive collection.mailarchive /path/to/exported-mail
ect-search --archive collection.mailarchive budget
ect verify collection.mailarchive
```

- `--owner` can be repeated. It takes the same rules as the
  [owner fields](@/importing.md#your-own-addresses).
- `ect-import` accepts several files and folders. Run it again to resume an
  interrupted import or add new mail.
- `ect-search` uses the same [search syntax](@/searching.md#words-phrases-and-selectors)
  as the application. Quote a query with spaces:
  `--query 'subject:"annual report" after:2024-01-01'`. It shows ten results;
  `--limit 0` shows all.
- `ect-search --archive collection.mailarchive 42` displays message 42.
  `--headers` and `--mime` show all headers or the complete original message.

## IMAP accounts

```sh
ect-import --archive collection.mailarchive --imap
```

The first run asks for the server, port, user name, and password or app
password, and stores the password in macOS Keychain or Windows Credential
Manager. Later runs download new mail from the saved accounts and exit.

| Command | Does |
| --- | --- |
| `ect-import ... --imap --add-source` | Add another account |
| `ect-import ... --imap --source ID --configure` | Change a saved account |
| `ect imap --archive ARCHIVE --check-credentials` | Test the saved password without downloading |
| `ect imap --archive ARCHIVE --source ID --set-password` | Replace a password after testing it |

Never put a password in a command, an environment variable, or a file.

## Other commands

| Command | Does |
| --- | --- |
| `ect report --archive ARCHIVE --year 2024` | Message counts by year, sender, and recipient |
| `ect human-contacts --archive ARCHIVE` | Likely correspondents with dates and counts |
| `ect identities --archive ARCHIVE addresses` | List and edit name groupings |
| `ect process --archive ARCHIVE` | Finish or resume processing saved mail |
| `ect reprocess --archive ARCHIVE` | Rebuild catalog details and the search index from saved mail |
| `ect compare-apple-mail --apple-mail ~/Library/Mail --archive ARCHIVE` | Compare an Apple Mail cache with the archive |
| `ect extract-pdf-mail printed.pdf --output recovered.mboxrd` | Recover printed email from a PDF into a new MBOX for review |
| `ect gui --archive ARCHIVE` | Open the desktop application on an archive |

## Environment variables

Most people need none of these. Never put passwords in environment variables.

| Variable | Purpose |
| --- | --- |
| `MAIL_ARCHIVE_DIR` | Default archive when `--archive` is not given |
| `MAILARCHIVER_CLAMAV_LIBRARY` | Path to the ClamAV library |
| `MAILARCHIVER_CLAMAV_DATABASE` | Fixed virus-definition folder; turns off automatic updates |
| `MAILARCHIVER_CLAMAV_UPDATES` | Where downloaded virus definitions are kept |
| `MAILARCHIVER_FRESHCLAM` | Path to the ClamAV `freshclam` updater |
| `MAILARCHIVER_CLAMAV_CERTIFICATES` | Certificates for checking virus definitions |
| `MAILARCHIVER_SEARCH_VOCABULARY_CACHE` | Folder for stored search-index word lists (default: the user cache folder) |
| `SSL_CERT_FILE`, `SSL_CERT_DIR` | Extra trusted certificates for private IMAP servers |
