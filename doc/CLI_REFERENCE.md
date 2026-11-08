<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Command-line guide

Use `ect` to create, import, search and maintain an email archive. The three
shortcuts `ect-create`, `ect-import` and `ect-search` run the same operations as
`ect create`, `ect import` and `ect search`. The old `mailarchiver` and
`mailsearch` executable names have been removed.

## Install

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then install
the CLI from the project repository:

```sh
uv tool install 'git+https://github.com/simsong/email-collection-toolkit.git'
ect --version
```

From a source checkout, use `uv sync --locked` once and prefix commands with
`uv run --locked`, for example `uv run --locked ect-create collection.mailarchive`.
All examples below use the installed commands directly. No Make installation is
needed to run them. Build and test commands are in [Developer commands](DEVELOPER_COMMANDS.md).

Python 3.12 or newer is required; uv can provide it. Antivirus scanning also
needs a native `libclamav` runtime, its one-shot updater and verified definitions;
the CLI package does not bundle these native components. Desktop installers
include them. See [antivirus setup](EMBEDDED_CLAMAV.md#definitions-and-platform-paths).
PST/OST import additionally needs the [importer helpers](PST_IMPORTER.md).

## Create an archive

```sh
ect-create collection.mailarchive --owner sam@example.org
```

Repeat `--owner` for additional owner email addresses or include rules. These
rules distinguish Sent mail from received mail. An empty/new destination is
required; existing archives and nonempty directories are never overwritten.
Omit `--owner` to configure rules later through the desktop or IMAP setup.
Creation does not download or import mail.

## Ingest

Import a directory or one or more supported files:

```sh
ect-import --archive collection.mailarchive /path/to/exported-mail
ect import --archive collection.mailarchive /path/to/first.mbox /path/to/second.mbox
```

The destination must already exist. Sources are read without modification;
subdirectories are searched for supported mail files. Exact duplicate messages
are not stored twice. Repeat the command to resume an interrupted import.
Back up the whole archive, including databases and configuration.

Antivirus scanning is on by default. `--no-scan` explicitly records unscanned
mail; positive detections are retained in the archive's INFECTED MBOX files.
Scanner failures are recorded and messages retained.

| Option | Purpose |
| --- | --- |
| `--workers N` | Override the saved worker limit; maximum twice the logical CPU count |
| `--owner-names-file FILE` | Read owner include rules from a legacy text file |
| `--index-attachments` | Include supported text attachments in search; PDF/Office extraction is not connected |
| `--defer-content` | Preserve messages now and process their content later |
| `--earliest-year YEAR` | Earliest plausible message date, default 1900 |
| `--plugin-dir DIR` | Load trusted local plugin code; repeatable |
| `--installation-config FILE` | Select installation plugin settings |

## IMAP downloads

```sh
ect-import --archive collection.mailarchive --imap
```

The first run prompts for server, port, TLS mode, username and a hidden password
or app password. Nonsecret settings are saved in the archive; the password goes
in macOS Keychain or Windows Credential Manager. Do not put passwords in command
arguments, environment variables or configuration files. Initial setup needs an
interactive terminal and a supported native credential store.

Subsequent runs download from saved accounts and exit:

```sh
ect import --archive collection.mailarchive --imap --show-messages
ect import --archive collection.mailarchive --imap --source SOURCE_ID
ect import --archive collection.mailarchive --imap --add-source
ect import --archive collection.mailarchive --imap --source SOURCE_ID --configure
```

`SOURCE_ID` is the ID printed during setup and saved in `config.yaml`.
`--configure` updates a saved account; `--add-source` prompts for another one.
`--workers N` and `--no-scan` also apply. Local paths and local-only import
options cannot be combined with `--imap`.

Acquisition uses read-only IMAP operations and does not mark messages read.
Gmail downloads All Mail and records Gmail labels; Spam/Trash-only messages are
outside this coverage. Other servers exclude mailbox names containing `spam`.
OAuth is not implemented. See [Gmail](GMAIL.md) and [Microsoft 365](M365.md).

Ctrl-C saves completed progress and exits 130. A connection timeout or disconnect
pauses acquisition and exits 75; rerun when connected. There is no background
scheduler. IMAP converts CRLF to LF before archiving and records the original
wire hash/size; local-file imports retain their original message bytes.

## Search and message display

```sh
ect-search --archive collection.mailarchive budget
ect-search --archive collection.mailarchive to:alice@example.org budget
ect-search --archive collection.mailarchive --query 'subject:"annual report" after:2024-01-01'
ect-search --archive collection.mailarchive --limit 0
```

The default limit is ten; `--limit 0` lists all matches. With no search terms,
the command lists normal indexed messages. Infected/excluded messages are outside
ordinary search. Search is read-only, ignores case and accents, and ANDs terms.
Quoted punctuation remains significant; `--query` preserves search-box syntax.

| Selector | Meaning |
| --- | --- |
| `any:ADDRESS` | Sender or any recipient |
| `from:ADDRESS` | Sender |
| `to:ADDRESS`, `cc:ADDRESS`, `bcc:ADDRESS` | Recipient in the named header |
| `subject:TEXT` | Subject text |
| `date:YYYY-MM-DD` | That date anywhere in the world |
| `before:YYYY-MM-DD` | Before that date begins anywhere |
| `after:YYYY-MM-DD` | After that date ends everywhere |

Read a result using its stable message number:

```sh
ect-search --archive collection.mailarchive 42
ect-search --archive collection.mailarchive --headers 42
ect-search --archive collection.mailarchive --mime 42
```

`--headers` shows all headers, `--html` prints decoded HTML, and `--mime` prints
the complete stored RFC 5322/MIME source. See the [search guide](USER_MANUAL.md#search-with-the-graphical-interface)
for shared query and date behavior.

## Verification

```sh
ect verify collection.mailarchive
```

Stop archive writers first. Verification reads the archive without modifying it
and checks BagIt/Mailbag metadata, checksum manifests and message integrity.
Failures exit nonzero; `--quiet` suppresses normal progress. It does not repair
mail or require source accounts. The archive-local portable equivalent is:

```sh
python3 collection.mailarchive/verify_mail_archive.py collection.mailarchive
```

The portable script is installed/refreshed during import. See [integrity controls](INTEGRITY_CONTROLS.md).

## Local archive commands

Use `ect COMMAND --archive ARCHIVE [OPTIONS]`. For import, search and these
commands, `MAIL_ARCHIVE_DIR` can supply the archive; explicit `--archive` wins.

```sh
ect report --archive collection.mailarchive --year 2024 --top 10
ect review --archive collection.mailarchive
ect processors --archive collection.mailarchive
ect processing-status --archive collection.mailarchive
ect process --archive collection.mailarchive --phase content
ect refresh-index --archive collection.mailarchive
```

`process` resumes saved work. `--phase` is `ingest`, `content` (default), or `all`;
`--max-jobs N` bounds a run; `--reprocess` explicitly queues a fresh generation.
`refresh-index` verifies stored messages and replaces only the rebuildable search
index. Both accept `--workers N`; `refresh-index --index-attachments` includes
supported text attachments. Source acquisition requires `ect import`.

## Identities and Contacts

```sh
ect identities --archive collection.mailarchive addresses
ect identities --archive collection.mailarchive organizations
ect human-contacts --archive collection.mailarchive --format tsv
```

`ect identities --help` lists filters and the manual rename, merge and affiliation
actions. Those actions write durable decisions. `human-contacts` reads likely
direct correspondents; `--all` includes all human header addresses. See
[Contacts policy](USER_MANUAL.md#contact-filter-policy).

## Desktop

```sh
ect gui --archive collection.mailarchive
```

Without a path, the desktop offers its recent/open/new flow. `--new` starts new
setup; `--workers N` overrides the worker preference for that launch. Native
runtime and GUI assets are provided by the desktop installers or source checkout.

## Apple Mail comparison

```sh
ect compare-apple-mail --apple-mail /path/to/Mail --archive collection.mailarchive
```

Read-only comparison reports exact, semantic-only and unmatched messages.
A local cache cannot prove server completeness; see [cache limits](APPLE_MAIL_CACHE.md).

## Printed-email PDF extraction

```sh
ect extract-pdf-mail printed-mail.pdf --output recovered.mboxrd
```

Requires Poppler `pdftotext`. The output must be new and outside the source.
`--handwritten-page N` records useful annotation pages and can be repeated.
This creates a derivative; it does not alter the PDF or import the result.

## Other commands

Use `ect COMMAND --help` for each command's actual options.

| Command | Purpose |
| --- | --- |
| `ect update-definitions` | Refresh application antivirus definitions; `--seed DIR` verifies/copies an existing set, `--directory NEW_DIR` downloads into a new directory |
| `ect imap` | Advanced account synchronization, including the combined `--new ARCHIVE` setup flow |
| `ect h3-review` | Export a private semantic-hash review with explicit input/output paths |
| `ect summarize` | Summarize standard input locally using Apple Foundation Models; requires the Apple SDK/runtime and available model |
| `ect validation` | List, fetch, prepare or run explicitly selected validation corpora; cloud runs need separately provisioned credentials and authorization |
| `ect processor` | Disposable plugin-development harness; not a canonical archive creator |

[Developer commands](DEVELOPER_COMMANDS.md) covers builds, signing, tests, Rust
helpers and research scripts. [The documentation guide](README.md) locates the
maintained user, developer and format references.
