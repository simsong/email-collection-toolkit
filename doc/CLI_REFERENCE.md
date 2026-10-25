<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# CLI reference

This is the command reference for the current checkout. Run commands from the
repository root with locked dependencies (`uv sync --locked`). Installed packages
provide the same console names without `uv run --locked`. Development and test
workflows use Makefile targets; tests may keep their recipes in testing guides.
PST/OST stream examples also remain in [PST_IMPORTER.md](PST_IMPORTER.md).

`ARCHIVE` below means a full archive directory, normally ending in `.mailarchive`.
Local ingest/search accept `--archive` or `MAIL_ARCHIVE_DIR`; the explicit option
wins. IMAP requires its own explicit `--new` or `--archive`.
Do not put passwords in arguments, environment variables or shell history.

## Quick examples

```sh
# Configure a new IMAP archive, synchronize once, then exit.
make imap IMAP_ARGS='--new /path/to/archive.mailarchive'
# Resume saved accounts and print each completed download.
make imap IMAP_ARGS='--archive /path/to/archive.mailarchive --show-messages'
# List every normal, searchable message; omit --limit for ten results.
make search ARGS='--archive /path/to/archive.mailarchive --limit 0'
# Search and then read a result by stable message number.
make search ARGS='--archive /path/to/archive.mailarchive to:alice@example.org budget'
make search ARGS='--archive /path/to/archive.mailarchive 42'
# Import local sources; owner-names.txt contains owner include rules.
make run ARGS='--archive /path/to/archive.mailarchive ingest --owner-names-file owner-names.txt --clamav /path/to/source'
make verify ARCHIVE=/path/to/archive.mailarchive
```

Ingest mutates only the destination archive. Keep sources unchanged and use a
new/disposable destination for experiments. Verification/search are read-only;
run integrity checks with writers stopped. Back up the whole archive, including
configuration, operational history and manual decisions in databases.

## Local archive commands

The root command is `mailarchiver --archive ARCHIVE COMMAND [OPTIONS]`. Its
Makefile wrapper is `make run ARGS='--archive ARCHIVE COMMAND ...'`. Put the
archive option before the subcommand. Every parser supports `-h` / `--help`.

| Command | Purpose |
| --- | --- |
| `ingest ROOT [ROOT ...]` | Discover supported sources, preserve messages, scan and index |
| `review [--run ID]` | Show source observations and dispositions |
| `report [--year YEAR_OR_RANGE] [--top N]` | Catalog counts and top correspondents (default top 10); `--top 0` suppresses lists |
| `refresh-index [--index-attachments] [--workers N]` | Verify canonical messages and atomically replace the search index |
| `processors [--plugin-dir DIR ...]` | List registered production processors |
| `processing-status` | Show persisted jobs and invocation statistics |
| `process [--phase ingest|content|all] [--max-jobs N] [--workers N] [--reprocess]` | Resume saved work; `--reprocess` requests a fresh generation |
| `identities ACTION [OPTIONS]` | Read identity evidence or commit a manual decision |
| `human-contacts [OPTIONS]` | Read likely human direct correspondents |

### Ingest

Choose exactly one of `--clamav` and `--no-scan`. The first uses embedded
libclamav in a run-owned worker; the latter explicitly records unscanned mail.
There is no resident daemon. Positive detections are retained in numbered
`INFECTED` MBOX parts. Errors retain evidence rather than silently dropping mail.

| Option | Meaning |
| --- | --- |
| `--owner-names-file FILE` | Legacy owner include rules; otherwise use archive `config.yaml` |
| `--earliest-year N` | First plausible routing year; default 1900 |
| `--workers N` | Override saved worker preferences; default half the logical CPUs, maximum twice that count |
| `--index-attachments` | Index supported text attachments; does not add PDF/Office extraction |
| `--defer-content` | File messages now, leave content processing pending |
| `--plugin-dir DIR` | Repeatable trusted Python plugin root; code executes locally |
| `--installation-config FILE` | Installation plugin settings override |

Ctrl-C requests checkpointing and exits 130. Repeat ingest with the same source
to resume traversal; `process` alone cannot discover unread source messages.
`process` also accepts repeatable `--plugin-dir` and `--installation-config`.
It uses the current application worker preference unless `--workers N` overrides it.
Content-only processing defaults to phase `content`; already successful work is
reused. `refresh-index` uses the same worker preference and leaves the old
index intact if interrupted before publication.

### Identities and Contacts

Identity actions are `addresses`, `organizations`, `rename-person`, `merge-person`,
`rename-organization` and `affiliate`. Filters/options are `--name`, `--mailbox`,
`--domain`, inclusive `--start` / `--end` (`YYYY-MM-DD`), and positive numeric
`--subject` / `--target` entity IDs. Rename/merge/affiliation actions write durable
manual decisions under the writer lease; read actions return JSON.

Contacts options: `--owner-address-file FILE`, repeatable `--owner-address ADDRESS`,
`--all`, and `--format table|tsv|json` (default table). The default selects direct
correspondents using exact owner addresses resolved from Sent mail. `--all`
includes all human header addresses, still applying the versioned contact policy.
Policy configuration is documented in [USER_MANUAL.md](USER_MANUAL.md#contact-filter-policy).

## IMAP downloads

Wrapper: `make imap IMAP_ARGS='OPTIONS'`; entry point: `mailarchiver-imap`.

```text
usage: imap_cli.py [-h] (--new ARCHIVE | --archive ARCHIVE) [--source SOURCE]
                   [--configure | --add-source] [--workers WORKERS]
                   [--show-messages] [--no-scan]

Configure and synchronize read-only IMAP sources from a terminal.

options:
  -h, --help         show this help message and exit
  --new ARCHIVE      create an empty new archive; never overwrite
  --archive ARCHIVE  synchronize an existing archive
  --source SOURCE    synchronize only this saved source ID
  --configure        prompt for settings and update saved credentials
  --add-source       prompt for another account
  --workers WORKERS  override worker preferences (default: half CPUs; maximum: twice CPUs)
  --show-messages    print each completed message download and disposition
  --no-scan          explicitly import without antivirus; retain not-scanned
                     evidence
```

Initial setup prompts for server, port, verified TLS mode, username and hidden
password/app password. Only native macOS Keychain or Windows Credential Manager
stores the secret. Nonsecret accounts live in `config.yaml`; `--source ID`
selects one saved account. `--configure` updates it; `--add-source` adds another.
Do not combine `--source` with `--new` or `--add-source`.

One invocation scans once and exits; there is no scheduler or background agent.
The saved worker preference defaults to half the logical CPUs (at least one).
Explicit `--workers` values must be positive whole numbers and cannot exceed twice
the logical CPU count; unavailable CPU detection counts as one. Status prints at startup, every five minutes and
on exit; `--show-messages` adds disposition and subject for each download.
Ctrl-C checkpoints and exits 130; a blocked network operation has a 30-second
socket timeout. Rerunning resumes committed progress.

Generic IMAP includes selectable mailboxes except names containing `spam`,
case-insensitively. Gmail fetches bodies only from special-use All Mail and uses
`X-GM-MSGID` for account-scoped identity and `X-GM-LABELS` for membership/hierarchy.
Spam/Trash-only messages are outside this mode. OAuth is not implemented.

The main counter includes newly archived messages only; duplicate downloads,
exclusions and errors have separate UTC hourly totals. Metadata refreshes do not
count as downloads. Full MIME and attachments enter the ingest pipeline, with
attachment indexing enabled and existing text-extraction limits. UID checkpoints
prevent repeat transfers; changed generic UID epochs may require a refetch that
canonical content deduplication then recognizes.

## Search and message display

Search ignores case and accents and treats `ß`/`ẞ` as `ss`, without stemming.
Existing indexes need no rebuild. Quoted symbols remain significant: `"CACM-"`
requires the hyphen and `"Ham+Sandwhich"` requires the plus. Word boundaries apply.
Use `mailsearch --query '"Ham+Sandwhich"'` to preserve quoted search-box syntax
through the shell.

Wrapper: `make search ARGS='OPTIONS SEARCH ...'`; entry point: `mailsearch`.
With no search terms it lists normal indexed messages. Infected/excluded records
are outside ordinary search; use `review` for source dispositions.

```text
usage: mailsearch [-h] [--archive ARCHIVE] [--limit LIMIT] [--headers]
                  [--html | --mime]
                  [SEARCH ...]

Read-only search of a mailarchiver archive.

positional arguments:
  SEARCH

options:
  -h, --help         show this help message and exit
  --archive ARCHIVE  directory containing archive.sqlite3 and search.sqlite3;
                     defaults to $MAIL_ARCHIVE_DIR
  --limit LIMIT      maximum matches to print; 0 prints all (default: 10)
  --headers          with a message number, show all headers
  --html             with a message number, print decoded text/html parts
  --mime             with a message number, print the full RFC 5322/MIME
                     source

Ordinary words search indexed headers and message text. Every term is ANDed.

Search selectors:
  any:ADDRESS           sender or To, Cc, or Bcc recipient address
  from:ADDRESS          sender address
  to:ADDRESS            To recipient address
  cc:ADDRESS            Cc recipient address
  bcc:ADDRESS           Bcc recipient address
  subject:TEXT          subject text
  date:YYYY-MM-DD       messages during that worldwide calendar date
  before:YYYY-MM-DD     messages before that date begins anywhere
  after:YYYY-MM-DD      messages after that date ends everywhere

A sole message number prints selected headers and the preferred text part. Use
--headers, --html, or --mime for alternate views.

Examples:
  mailsearch to:alice@example.com budget
  mailsearch --limit 0 after:2024-01-01
  mailsearch --archive OTHER_ARCHIVE 42
```

The displayed number identifies a message: supplying it alone reads that
message. `--headers` adds all headers, `--html` prints decoded HTML and `--mime`
prints complete MIME source. `--limit 0` removes the result limit.
The GUI search guide in [USER_MANUAL.md](USER_MANUAL.md#search-with-the-graphical-interface)
explains shared selectors and date interpretation. The selector inventory above
matches the current parser.

## Desktop

Use `make gui ARGS='--archive ARCHIVE'` or `mailsearch-gui`. Without a path the
application uses its recent/open/new startup flow. `--new` requests new setup.
The parser also has `--smoke-report PATH` and `--smoke-html-find` for native test
drivers; use the dedicated testing targets rather than personal mail.

### Packaged macOS executable

The bundled executable accepts `--self-test`, `--self-test-gui` and
`--cli` (dispatch the remaining arguments to `mailarchiver`). Ordinary launches
start the desktop. These are package diagnostics, not extra local ingest modes.

## Verification

`make verify ARCHIVE=ARCHIVE` runs `verify-mail-archive ARCHIVE` (also copied
into an archive as `verify_mail_archive.py`). `-q` / `--quiet` suppresses progress
and success output but retains errors. Supply the path explicitly: the portable
script's default is its containing directory, not the shell's current directory.
It checks BagIt/Mailbag files, manifests and message integrity without source mail
or a database rebuild. Ctrl-C exits 130. See [INTEGRITY_CONTROLS.md](INTEGRITY_CONTROLS.md).

The separate Rust `archive-verifier` checks catalog/MBOX/search consistency;
it is not a replacement for complete portable BagIt verification.

## Apple Mail comparison

Wrapper: `make compare-apple-mail ARGS='OPTIONS'`; entry point:
`mailarchiver-compare-apple-mail`. Reads cache and archive without importing.

```text
usage: apple_mail_compare.py [-h] [--apple-mail APPLE_MAIL]
                             [--archive ARCHIVE]
                             [--progress-every PROGRESS_EVERY] [--json]

Compare complete Apple Mail EMLX records with an Email Collection Toolkit
archive read-only.

options:
  -h, --help            show this help message and exit
  --apple-mail APPLE_MAIL
  --archive ARCHIVE
  --progress-every PROGRESS_EVERY
  --json
```

A local cache cannot prove server completeness. Keep the cache intact; see
[APPLE_MAIL_CACHE.md](APPLE_MAIL_CACHE.md) for interpretation and source-safety
preflight.

## Printed-email PDF extraction

Wrapper: `make extract-pdf-mail ARGS='PDF --output NEW.mboxrd'`; entry point:
`extract-pdf-mail`.

```text
usage: pdf_mail.py [-h] --output OUTPUT [--handwritten-page HANDWRITTEN_PAGE]
                   pdf

extract printed email from a standalone PDF into derived MBOX

positional arguments:
  pdf

options:
  -h, --help            show this help message and exit
  --output OUTPUT       new .mboxrd output file
  --handwritten-page HANDWRITTEN_PAGE
                        source page containing useful handwritten annotations;
                        repeatable
```

Requires Poppler `pdftotext`. The output must be new and outside the source.
Repeated `--handwritten-page N` records useful annotation pages. Extraction
creates a derivative; it does not ingest it or modify the PDF. See
[PDF/OCR plan](plans/PDF_OCR.md) for remaining OCR requirements.

## Local summaries

`summarize` reads text from standard input and sends it to the local Apple
Foundation Models helper. It takes no CLI options or archive path. The first
run compiles a content-addressed Swift helper in the user's cache; it requires
the Apple SDK/runtime and model availability. It is not a Windows feature.
`make summary-smoke` is the synthetic developer exercise.

## Validation corpus CLI

`mailarchiver-validation` is wrapped by the Makefile targets below.
Global options: `--config-dir DIR`, `--data-dir DIR`, `--owner-names-file FILE`.
Commands: `list`; `fetch DATASET`; `prepare DATASET`; `run DATASET`; `run-all`;
`aws-start DATASET --stack STACK`; `aws-start-all --stack STACK`. Downloads and
AWS runs are explicit operations; `list` does not start either. AWS commands
require separately provisioned credentials, stack and authorization. See
[REGRESSION.md](REGRESSION.md) for retention and comparison policy.

| Make target | Parameters / purpose |
| --- | --- |
| `validation-list` | List configured datasets |
| `validation-fetch` | `DATASET=NAME`: acquire input |
| `validation-prepare` | `DATASET=NAME`: prepare extracted input |
| `validation-run` | `DATASET=NAME`: run one corpus |
| `validation-run-all` | Run all configured local corpora |
| `validation-aws-start` | `DATASET=NAME STACK=NAME`: start one remote run |
| `validation-aws-start-all` | `STACK=NAME`: start remote configured runs |
| `validation-sam-build`, `validation-sam-validate`, `validation-sam-deploy` | Infrastructure preparation/validation/deployment; deployment requires authorization |

## Processor harness

`make processor ARGS='--archive DEMO --plugin-dir tests/processing_plugins COMMAND'`
runs `python -m mailarchiver.processing`. This is a disposable framework harness,
not a canonical Mailbag creator or substitute for production `ingest`/`process`.

Global options are required `--archive`, repeatable `--plugin-dir` and optional
`--installation-config`. Commands: `init`, `plugins`, `submit FILE`,
`run [--retry] [--max-jobs N]`, `status`, `reprocess`. It snapshots submitted
objects, persists jobs and prints typed JSON. Ctrl-C retains work and exits 130;
a failed run exits 1. Only load trusted plugin directories.
[PLUGINS.md](PLUGINS.md) owns the API/dispatch/storage contract.

## Research and diagnostic commands

These create private derivatives outside source stores. They do not infer
authoritative identities or replace archive content.

| Target | Required parameters and purpose |
| --- | --- |
| `data-quality-audit` | `ARCHIVE=PATH EARLY_SOURCE=PATH AUDIT_OUTPUT=NEW_DIR`: verified early-message/missing-sender evidence |
| `data-quality-babyl-audit` | `EARLY_SOURCE=PATH AUDIT_OUTPUT=DIR`: inspect Babyl inputs |
| `data-quality-summary` | `AUDIT_OUTPUT=DIR`: summarize exported evidence |
| `name-matcher-observations` | `ARCHIVE=PATH OUTPUT=NEW.sqlite3 ARGS='--limit N --workers N'`: read-only identity/signature evidence |
| `addressbook-export` | `ARGS='--archive PATH --output NEW.txt [--owner ADDRESS ...] [--owners-from-sent] [--owner-name NAME]'`: experimental ePADD replacement address book |
| `h3-ambiguous-review` | Intended private semantic-hash review export; its Make recipe currently references an unregistered console entry point. Not a working supported CLI |

For the h3 prototype's actual interface, `python -m mailarchiver.h3_review` accepts
`--apple-mail`, `--archive`, required `--output`, `--limit` (default 20),
`--progress-every` (default 10000), and `--refresh-existing` (rebuild reports from existing output without rereading source stores). Use only in an explicitly
authorized private diagnostic run. See source safety in
[APPLE_MAIL_CACHE.md](APPLE_MAIL_CACHE.md), historical results in
[DATA_QUALITY_AUDIT.md](DATA_QUALITY_AUDIT.md) and the
[name research notebook](research/name-matcher/README.md).

## Rust tools

`make rust-programs` builds the supported workspace release binaries in
`target/release/` (`RUST_TARGET_DIR` overrides it). Build a single tool with
`make TOOL_NAME`. Runtime syntax:

| Binary | Interface |
| --- | --- |
| `archive-verifier` | `ARCHIVE`: read-only catalog/MBOX/search consistency; stop writers first |
| `mcti-generator` | `COUNT`: synthetic API mboxrd to stdout; zero emits an empty stream |
| `mdti-validator` | `[--max-message-bytes N]`: validate stdin, discard input, nonzero on failure |
| `mcti-scan` | `INPUT.mboxrd`: scan reconstructed messages and emit API stream |
| `pst-importer` | `[--only-invalid] [--offset N] [--limit N] [--] FILE`: read-only PST extraction to stdout |
| `pst-downloader` | Corpus planning/downloading; use wrappers below and [pst/README.md](../pst/README.md) for manifest/retry details |

Generator, validator and PST importer accept `--api-version` / `--version`;
`--help` is supported by those tools and archive-verifier. `mcti-scan` requires
an input file rather than `--help`. Extraction diagnostics go to stderr; nonzero
status can mean partial results, which must be retained and reported.

PST downloader options: `--inventory-dir DIR` (default `pst`), `--output DIR`
(default `var/pst`), `--scope fixtures|archives|all` (default all), `--dry-run`,
`--limit N` source URLs, `--max-download-bytes N` (default 16 GiB),
`--max-expanded-bytes N` (default 64 GiB) and `--timeout-seconds N` (default 1800).
`PST_DOWNLOAD_ARGS` passes these options through both acquisition wrappers.

The separate Python `pff-converter FILE` accepts `--receipt FILE`,
`--diagnostics FILE`, `--timeout-seconds N` (default 60),
`--max-message-bytes N` (default 64 MiB), `--max-folder-depth N` (default 64),
`--offset N` (default 0) and `--limit N`. Build through `make pff-converter`;
its dependencies live in a separate environment and are not imported by the host.
See [PST_IMPORTER.md](PST_IMPORTER.md) for OST scope and stream composition.

`make verify-database ARCHIVE=ARCHIVE` builds/runs the independent Rust verifier.
`make pst-import PST=FILE ARGS='OPTIONS'` runs the importer;
`make pst-download-plan` inspects acquisition and `make pst-download` downloads
into ignored `var/pst/`. PST-specific pipeline examples are intentionally kept
in [PST_IMPORTER.md](PST_IMPORTER.md) and [MCT_IMPORTER_API.md](MCT_IMPORTER_API.md).
The supported MIME decoder is a library, not another CLI.
The Rust GUI is retired; its old commands are history in
[RUST_GUI_EXPERIMENTS.md](RUST_GUI_EXPERIMENTS.md).

## Windows packaging

These targets run on a Windows development/CI host; outputs are local artifacts
unless a workflow explicitly publishes them.

| Target | Parameters / purpose |
| --- | --- |
| `prepare-windows-clamav` | Stage verified x64 runtime and baseline definitions |
| `prepare-windows-updater` | Stage pinned WinSparkle SDK and notices |
| `msix-test` | `MSIX_ARGS='-Architecture x64'`: build Python payload |
| `test-msix` | `MSIX_PACKAGE=PATH MSIX_EVIDENCE=NEW_DIR`: validate extracted payload |
| `msix-bundle` | `MSIX_PAYLOADS=DIR MSIX_ARGS='OPTIONS'`: assemble/sign bundle; `-PublicSigning` prepares Microsoft-action input |
| `verify-public-msix` | `MSIX_PUBLISHER='approved subject'`: validate public trust/timestamp/identity |

[WINDOWS_MSIX_TEST.md](WINDOWS_MSIX_TEST.md) owns package/trust boundaries;
[WINDOWS_PUBLIC_SIGNING.md](WINDOWS_PUBLIC_SIGNING.md) owns Azure provisioning.

## Build, release and maintenance entry points

Makefile variables are parameters to targets, not automatically process
environment variables. The root [README](../README.md) documents environment
variable names and purposes; no secret values belong in documentation.

| Target | Purpose / parameters |
| --- | --- |
| `sync-dependencies` | Synchronize Python environment |
| `build-sdist` | Source distribution; `DIST_DIR` output |
| `gui` | Launch supported desktop; `ARGS` passes its options |
| `dmg`, `python-dmg` | Build Python macOS application/DMG; `ARGS`, optional `SIGNING_IDENTITY` |
| `dmg-signed`, `list-signatures` | Build with Developer ID / list available identities |
| `notarize-dmg` | Submit/staple local signed DMG; `DMG`, `ARGS` |
| `check-release`, `test-dmg`, `test-mounted-dmg` | Candidate and mounted-artifact acceptance; see distribution guide |
| `preview-dmg` | Explicit preview package, not publication |
| `sparkle-tools`, `sparkle-keys` | Verify/stage pinned tooling / key operation |
| `update-appcast`, `check-appcast` | Construct/validate selected feed; `RELEASE_TAG`, `DMG`, `APPCAST`, `ARGS` |
| `release-files`, `release-tag-check` | Stage validated assets / enforce metadata-matching immutable tag |
| `release-candidate-base`, `release-candidate-feed`, `release-appcast-base` | Shared candidate/feed preparation gates |
| `release-package-check` | Package acceptance prerequisite |
| `sign-historical-appcast` | One-time reviewed historical migration; requires explicit pinned historical inputs |
| `freshclam` | Seed/update checkout definitions in `etc/clamdb` |
| `clamav-update` | Refresh application per-user definition generation |
| `clamav-seed` | Explicit baseline preparation; consult Makefile parameters |
| `website-check`, `website-build-check`, `website-download-check` | Asset/link, built-site and rendered download acceptance |
| `website-preview` | Build/open local site preview |
| `website-release-data` | Derive installer metadata; `RELEASES_JSON` |
| `website-screenshots`, `website-preview-screenshots` | Synthetic interface/site screenshots |
| `website-icons`, `website-gmail-illustrations` | Developer illustration generation |
| `install-mac`, `install-linux`, `install-test-browser`, `install-tika` | Explicit developer prerequisite installation; not ordinary application startup |
| `ocr-inventory`, `ocr-profile`, `ocr-analyze`, `ocr-run`, `ocr-experiment` | Experimental OCR fixture pipeline; see PDF strategy |
| `check`, `check-static`, `check-tests` | Ordered full/static/test verification |
| `ruff`, `pylint`, `ty`, `pyright`, `lint`, `types`, `syntax-check` | Individual static checks |
| `rust-check`, `rust-fmt`, `rust-lock`, `rust-toolchain` | Workspace validation/format, dependency update, selected compiler |
| `runtime-license-check`, `runtime-license-bundle`, `copyright-check`, `distribution-check` | Distribution/license/source audits |

The remaining `test-*`, `fixture-*`, smoke, native-check and golden-update
targets are test interfaces documented in [END_TO_END_TESTING.md](END_TO_END_TESTING.md),
[REGRESSION.md](REGRESSION.md) and beside their Makefile recipes. Running a build
or check is not authorization to sign with production credentials, install
trust, create cloud infrastructure, publish a release or change a source account.

## Platform signing recipes

These are explicit maintainer operations. Read the linked credential, signing
and publication prerequisites first. Examples do not authorize installing trust,
using production credentials or publishing a tag.

### Build the Python DMG

Preconditions and interpretation: [MACOS_DISTRIBUTION](MACOS_DISTRIBUTION.md).

```sh
make dmg
```

### Check the installed macOS executable

Preconditions and interpretation: [MACOS_DISTRIBUTION](MACOS_DISTRIBUTION.md).

```sh
"/Applications/Email Collection Toolkit.app/Contents/MacOS/Email Collection Toolkit" --self-test
"/Applications/Email Collection Toolkit.app/Contents/MacOS/Email Collection Toolkit" --self-test-gui
"/Applications/Email Collection Toolkit.app/Contents/MacOS/Email Collection Toolkit" --cli --help
```

### Select a Developer ID identity

Preconditions and interpretation: [MACOS_DISTRIBUTION](MACOS_DISTRIBUTION.md).

```sh
make list-signatures
make dmg-signed
```

### Publish a matching annotated tag

Preconditions and interpretation: [MACOS_DISTRIBUTION](MACOS_DISTRIBUTION.md).

```sh
git switch main
git pull --ff-only origin main
release_tag=vVERSION  # Replace VERSION with the canonical version in pyproject.toml.
make release-tag-check GITHUB_REF_NAME="$release_tag"
git tag -a "$release_tag" -m "Release $release_tag"
make release-tag-check GITHUB_REF_NAME="$release_tag" ARGS=--require-annotated
git push origin "refs/tags/$release_tag"
```

### Inspect signing identities

Preconditions and interpretation: [CERTIFICATE_MANAGEMENT](CERTIFICATE_MANAGEMENT.md).

```sh
security find-identity -v -p codesigning
```

### Copy the encrypted certificate export

Preconditions and interpretation: [CERTIFICATE_MANAGEMENT](CERTIFICATE_MANAGEMENT.md).

```sh
base64 -i "$HOME/Signing/DeveloperIDApplication.p12" | pbcopy
```

### Clear the credential clipboard

Preconditions and interpretation: [CERTIFICATE_MANAGEMENT](CERTIFICATE_MANAGEMENT.md).

```sh
pbcopy < /dev/null
```

### Build with an explicit signing identity

Preconditions and interpretation: [CERTIFICATE_MANAGEMENT](CERTIFICATE_MANAGEMENT.md).

```sh
make dmg ARGS='--signing-identity "Developer ID Application: YOUR NAME (TEAMID)"'
```

### Build an unsigned local artifact

Preconditions and interpretation: [CERTIFICATE_MANAGEMENT](CERTIFICATE_MANAGEMENT.md).

```sh
make dmg ARGS='--signing-identity -'
```

### Submit a signed DMG for notarization

Preconditions and interpretation: [CERTIFICATE_MANAGEMENT](CERTIFICATE_MANAGEMENT.md).

```sh
DMG="$PWD/dist/Email-Collection-Toolkit-VERSION-ARCH.dmg"
NOTARY_PROFILE='mail-archiver-release'

# Prompts securely for authentication; do not enter passwords in shell history.
xcrun notarytool store-credentials "$NOTARY_PROFILE"
xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" \
  --wait --timeout 30m --output-format json > "$DMG.notarization.json"
```

### Hash the validated DMG

Preconditions and interpretation: [CERTIFICATE_MANAGEMENT](CERTIFICATE_MANAGEMENT.md).

```sh
(cd "$(dirname "$DMG")" && shasum -a 256 "$(basename "$DMG")")
```
