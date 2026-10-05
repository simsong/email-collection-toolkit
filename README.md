<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Email Collection Toolkit

`mailarchiver` turns scattered email exports into a durable archive that you
can inspect with ordinary tools decades from now. It preserves the original
RFC 5322 message bytes in standard MBOX files inside a native BagIt 1.0 /
Mailbag 1.0 package, hashes every file and message for long-term integrity,
records where each message came from, and builds a local search index that can
always be discarded and rebuilt.

_NOTE --- THIS PROGRAM IS UNDER ACTIVE DEVELOPMENT. DO NOT USE OPERATIONALLY UNTIL VERSION 1.0 SHIPS_

## macOS application

For the macOS drag-to-Applications build, run `make dmg`. Python and GUI
dependencies are bundled; ClamAV is optional. The target mounts its DMG and
runs headless self-tests before publishing the local artifact. Visible native
validation is an explicit local `make check-release` operation.
See [macOS distribution](doc/MACOS_DISTRIBUTION.md) for installation, test commands,
architecture limits, and Developer ID renewal/signing instructions.

Release jobs use `SPARKLE_ED25519_PRIVATE_KEY_BASE64` to sign the DMG and XML
feed. `APPLE_CERTIFICATE_P12_BASE64` and `APPLE_CERTIFICATE_PASSWORD` import the
Developer ID signing identity on the hosted runner. `APPLE_NOTARY_KEY_ID`,
`APPLE_NOTARY_ISSUER_ID`, and `APPLE_NOTARY_PRIVATE_KEY_BASE64` authenticate
Apple notarization. These protected variables are never needed by the installed
app or its mounted self-test.

For alpha (`aN`), beta (`bN`), and stable versions, set the canonical version in
`pyproject.toml`, let branch CI run the real Sparkle signer target, merge, then
push a new annotated matching `v*` tag. The tag workflow verifies the prior
published feed, builds and notarizes the DMG, signs the DMG and complete XML,
validates both, and only then creates and publishes a complete release. It
dispatches the Pages workflow on `main` with the exact release tag; Pages
downloads and verifies that published signed feed before deployment. This keeps
Pages within its `main` environment rule. After a transient Pages failure,
rerun that workflow on `main`. Retire a failed unpublished tag only by explicit
release decision; publish a corrected build under a new version and tag.

The original `v1.0.0a10` release signed its archive but omitted a complete XML
signature. The audited `Prepare historical signed appcast` workflow produced a
separate signed copy, which was verified and published as the a10 release asset
and Pages feed before the next tag. The failed `v1.0.0a11` run left a draft release;
its tag is retired by explicit request while the draft remains as failure
evidence. The published preview release is `v1.0.0a13`; the next candidate is
`v1.0.0a14`, built from matching source on `main`.

Release workflow variables: `GITHUB_REPOSITORY` names the repository used to
retrieve the prior feed; `GH_TOKEN` authorizes release API reads and publication;
`GITHUB_REF_NAME` is the pushed tag validated against `pyproject.toml`.
The Makefile's `RELEASE_TAG` selects the candidate tag and `APPCAST` identifies
the copied feed output. `RUNNER_TEMP` is the hosted runner's temporary area.
No variable should contain secret material except the protected signing and
notarization variables named above.

## Rust reader experiment

`work-rust-gui` includes a separate, read-only Rust desktop reader. Its native
shell targets macOS and Windows and hosts the existing HTML/CSS/JavaScript search interface using Wry/Tao,
with Rust owning the archive operations. It opens an
existing archive, searches indexed words, and displays a selected message after
SHA-256 verification. It requires Rust 1.95+; the built app needs no Python.
Use `cargo run-ect --archive "/path/to/archive"` to build and launch the GUI
on Windows or macOS. The macOS Makefile launch target delegates to this alias.
Run `make rust-gui ARCHIVE="/path/to/archive"`, or create the synthetic fixture
with `make rust-gui-demo` and then run
`make rust-gui ARCHIVE=".tmp/rust-gui-demo"`. `make rust-gui` opens the reused interface; `make rust-gui-egui` opens the
earlier widget prototype for comparison.

`make test-rust-gui` runs headless Rust and widget tests;
`make test-rust-gui-interop` checks a Python-created archive.
`make test-rust-webview` tests the reused page against the real Rust dispatcher
in headless Chromium. `RUST_WEBVIEW_BINARY` selects that dispatcher for the test
and is set automatically by its Makefile target. `ARCHIVE` is the
archive path and `QUERY` supplies words to `make rust-gui-smoke`.
`RUST_GUI_DEMO` overrides the new synthetic fixture destination; creation refuses
an existing directory. `RUST_GUI_BINARY` selects the compiled executable for the
Python interoperability test and is set automatically by its Makefile target.
Set `ECT_RUST_WEBVIEW_DIAGNOSTICS=1` to log native navigation and IPC document
URLs to stderr while diagnosing the shell. It does not log request bodies or
message contents. Leave it unset for ordinary use.
`make rust-webview-probe ARCHIVE="/path/to/archive" QUERY=words` prints
counts and timings for the two preview windows and comprehensive search without
exposing message text. Results are paged on scroll; message reads remain usable
while the separate search worker runs, and replacement queries cancel old work.
See [scope and limitations](doc/RUST_GUI_EXPERIMENT.md).

`make test-rust-gui-native` builds with the opt-in `native-smoke` feature and
opens the actual macOS Wry/WKWebView window. A Rust test creates synthetic EML,
ingests it with the CLI into `Sample.mailarchive`, runs the portable verifier,
searches and selects a message through the native UI, and captures a WebKit PNG.
It checks source/archive fixity and fails on timeout or missing evidence.
`RUST_GUI_ARTIFACT_DIR` chooses the evidence directory (default
`.tmp/rust-gui-native`); each run retains its synthetic archive, logs, hashes and
`rust-gui.png`. CI runs this in the **Rust GUI (macos-latest)** matrix job and
uploads a `rust-gui-macos-latest-<commit>` artifact even on failure. It requires
a macOS GUI session; ordinary reader builds contain no smoke driver.

Native Windows evidence is produced with `cargo reader-native-check` (opens
WebView2 on synthetic mail and exits). `RUST_GUI_ARTIFACT_DIR` selects retained
native evidence; the default Windows directory is `.tmp/rust-gui-native-windows`.
`ECT_RUST_NATIVE_CLOSE_SMOKE` is a feature-gated test switch for quitting during
an unfinished search. Production builds do not embed the smoke driver.
Preferences use `LOCALAPPDATA` on Windows, `HOME/Library/Application Support`
on macOS, and `XDG_CONFIG_HOME` or `HOME/.config` on Linux. Tests isolate these
paths; email archives never contain reader preferences.

`ECT_RELEASE_VERSION`, `ECT_RELEASE_BUILD`, and `ECT_RELEASE_CHANNEL` are
build-time metadata generated by `scripts/rust_release_metadata.py` through the
shared release mapper. Optional build-time `ECT_WINSPARKLE_APPCAST_URL` and
`ECT_WINSPARKLE_PUBLIC_KEY` configure the shared HTTPS feed and public signing
key; never embed a private signing key. Until the shared feed is migrated and
Windows installers are published, development builds leave updates unavailable.
`ECT_WINSPARKLE_TEST_DLL` selects the staged, checksum-verified native DLL for
`cargo reader-updater-check`. `scripts/prepare_winsparkle.ps1` stages that DLL
and license notices without a system installation. `CARGO_TARGET_DIR` overrides
Cargo build output; macOS Make passes its `RUST_TARGET_DIR` there automatically.

## Windows development

Start with **[Windows GUI: build and run](doc/WINDOWS.md)** for prerequisites
and copyable PowerShell commands using Cargo. Native ARM64 builds and WebView2
launch/navigation/IPC have been exercised on the development VM; full native
interactive acceptance remains incomplete. The detailed
[Windows Rust handoff](doc/WINDOWS_RUST_HANDOFF.md) covers architecture and
remaining acceptance criteria.
Full Windows application parity also requires imports, processing,
attachments, exports, and packaging. The existing packaged application still
uses Python/pywebview. The earlier [Dioxus/Tauri plan](doc/DIOXUS.md) is historical
context, not a prerequisite to porting this prototype.

The Windows guide also retains historical full-application setup notes.
This is a development reader, not a supported Windows application release.

## Rust importer development

Rust importer development uses `make rust-programs` to build `mdti-validator`
`mcti-generator`, and [`pst-importer`](doc/PST_IMPORTER.md). See [MCT Importer API 1.0](doc/MCT_IMPORTER_API.md) for
the contract, individual targets and validation pipeline. Rust/Cargo are
required to build these tools. The standalone PST helper uses Microsoft's Rust crate;
the CLI archive host runs it through the common processing pipelines. Native
installer bundling remains planned. Packaged PST users will
need the compiled helper, not a compiler.

PST test inventories and the Rust corpus downloader live in [`pst/`](pst/README.md);
use `make pst-download-plan` to inspect the plan and `make pst-download` to fetch
into ignored `var/pst/`.

## Goals

`mailarchiver` is preservation infrastructure for personal and research email
collections, not a mail client or a compliance appliance. Its goals are to:

* harvest email from backup drives and active sources into one persistent,
  deduplicated Mailbag whose canonical MBOX and integrity files can be
  maintained with ordinary, independently implemented tools;
* provide one local search interface across decades of mail, regardless of the
  programs and providers that originally stored it;
* support policy-driven redacted derivatives for confidentiality, donor,
  privacy, and public-release requirements without modifying the canonical
  messages;
* maintain structured, rebuildable metadata suitable for reproducible digital
  humanities reports about correspondents, chronology, threads, attachments,
  entities, and provenance; and
* interoperate with heterogeneous backups, including Outlook `.pst` and `.ost`
  stores, Eudora backups, working IMAP client-cache directories, MBOX, EML,
  Maildir, Apple Mail, Gmail exports, and live read-only IMAP accounts.

“Plain-text archive” describes the standard, inspectable RFC 5322/MBOX
container. Binary attachments remain MIME-encoded so their original bytes are
preserved. Redaction and analysis outputs are derived products: they must be
recreatable and must never silently replace or rewrite canonical mail.

For each year it creates two logical archives: one for messages sent by the
archive owner and one for messages received. They begin as
`{YEAR}-Sent1.mbox` and `{YEAR}-Archive1.mbox`; additional numbered filenames
are used when appending would reach `config.yaml`'s `mbox_max_bytes` limit
(default 3.75 GiB; use 20480 for a 20 KiB synthetic test). Messages are never rewritten just
to make them easier to search: search indexes and automatic evidence are derived data. Manual identity
decisions and tags in `processing.sqlite3` require backup, while the BagIt payload, Mailbag metadata, and versioned
integrity tags are the portable durable record.

This is the initial local-ingest implementation, not yet the complete email
archiving system. It currently ingests local MBOX, Emacs RMAIL Babyl, EML,
Maildir, complete Apple Mail `.emlx` messages, Outlook PST through the Rust reader,
and OST through the external libpff converter. Both Outlook readers report partial extraction
and preserve reconstruction evidence; see [reader limits](doc/PST_IMPORTER.md).
Eudora, working IMAP cache directories, Gmail, live IMAP, redaction, richer
research data and sorting/repacking remain planned; see
[doc/implementation.md](doc/implementation.md).
The [archivist-facing user manual](doc/USER_MANUAL.md) gives step-by-step
instructions for ingest, verification, and search.
The [plug-in architecture](doc/PLUGINS.md) documents the implemented
source-neutral generator, trusted-directory discovery, threading, status, and
integrity boundaries, plus the work required by real Gmail, O365, IMAP, and
stream adapters.
The [on-disk mail format inventory](doc/ON_DISK_MAIL_FORMATS.md) records the
current and planned source formats, PST/OST fixture sources, open-source parser
options, and the selected import backend.
See [release notes](doc/RELEASE_NOTES.md) for changes not yet included in a
release.
Visit the [Email Collection Toolkit project site](https://simsong.github.io/email-collection-toolkit/)
for release links, digital email curation resources, and project discussions.
Announcements are posted in [Discussion #55](https://github.com/simsong/email-collection-toolkit/discussions/55);
please direct feature requests to [Discussion #56](https://github.com/simsong/email-collection-toolkit/discussions/56).
The [data-quality audit](doc/DATA_QUALITY_AUDIT.md) documents the read-only
diagnostic scripts used to investigate implausible dates, missing senders, and
previously unsupported Babyl sources. Its generated mail and metadata evidence
is private and deliberately excluded from Git.
The [historical August source-code audit](doc/source-code-audit.md) distinguishes completed
tightening from the remaining architectural gaps.
The [competitive analysis](doc/competitive_analysis.md) explains how this
combination differs from preservation, migration, search, forensic, and
commercial compliance products. [Project direction](doc/project_direction.md)
turns those findings into product principles, reuse decisions, non-goals,
acceptance criteria, and a phased roadmap.

Public-corpus validation can run either locally or on one ephemeral EC2 worker
per dataset. See [doc/REGRESSION.md](doc/REGRESSION.md) for the longitudinal
comparison and baseline policy, and [validation/README.md](validation/README.md)
for configured datasets, Make targets, SAM deployment parameters, and result
locations.

## Install

Install [uv](https://docs.astral.sh/uv/) and ClamAV, configure the ClamAV
daemon and signatures, then prepare this project:

```console
cd email-collection-toolkit
uv sync
```

Run the project command with `uv run mailarchiver`.

Set the archive directory once for the shell session:

```console
export MAIL_ARCHIVE_DIR=/path/to/mail-archive
```

Every `mailarchiver` and `mailsearch` command below uses this value. Pass
`--archive DIRECTORY` before the command to override it for one invocation.

### Apache Tika status

Apache Tika is **not used by the program yet**. The Makefile can download,
verify, and unpack its command-line application distribution in preparation
for opt-in extraction from PDF and Office attachments, but no current ingest
or search path invokes it.
Normal mail ingest and body-only search do not need Java. The current
`--index-attachments` option indexes text attachments only.

Tika requires Java 17 or newer. The following downloads Tika 4's ZIP
distribution, verifies Apache's published SHA-512 checksum, and unpacks the
application JAR with its required `lib/` directory below this checkout's
ignored `.tools/tika/4.0.0/` directory. It does not install a service or
schedule any work:

```console
make install-mac
# or, on Linux:
make install-linux
```

The installer uses `TIKA_VERSION=4.0.0`. To install a later Apache release,
set that variable after checking its release notes and checksum:

```console
make install-mac TIKA_VERSION=X.Y.Z
```

## Batch import from an external drive

Batch import currently runs through the Python archive engine on macOS, not
through the Rust GUI. Windows archive writing is explicitly unsupported in
this checkout. From a prepared macOS development checkout, with current ClamAV
definitions and an owner-names file (one owner name or address per line):

```sh
export MAIL_ARCHIVE_DIR="$HOME/EmailArchive"
make run ARGS='ingest --owner-names-file owner-names.txt --clamav "/Volumes/BackupDrive"'
```

Choose the destination deliberately and keep it outside the source tree. The
command creates or adds to that archive and recursively discovers supported
mail below the source root without modifying the source. Multiple source roots
can be supplied at the end of the same command. PST/OST formats need their
[importer prerequisites](doc/PST_IMPORTER.md). Reruns use source-idempotence and
message-deduplication checks.

A whole external-drive root is accepted, but this is **not yet a quiet drive
harvester**: empty files and known metadata are silently ignored; other
unrecognized files produce `skipped input` notices. Unreadable directories can
stop discovery. There is currently no CLI quiet-discovery switch. Keep genuine
read, parse, and antivirus errors visible; do not discard stderr to hide the
ordinary skipped-file notices. This command imports recognized mailbox data,
not arbitrary documents or mail hidden inside every possible container format.

## Ingest local mail

The following command recursively reads MBOX, Emacs RMAIL Babyl, EML, Maildir,
and `.emlx` files below
`SOURCE`.  It never changes those source files.  It starts `clamd` temporarily
for the ingest run if no daemon is already listening on the configured local
socket.

Maildir message files are grouped at the Maildir root. Apple Mail `.emlx`
cache files are grouped by their containing `.mbox` package hierarchy rather
than exposed as UUID, `Data`, `Messages`, and individual filename nodes.

### `--clamav`

Every CLI ingest requires either `--clamav` or `--no-scan`. The latter is an
explicit antivirus opt-out and records each new message as not scanned; it
does not certify mail as clean. A scanner failure never selects it automatically.

Choose `--clamav` to scan each new
message through the locally configured `clamd` socket before the message is
written to a normal MBOX. Before starting any mailfile workers, the main
ingest thread verifies that ClamAV is ready. If no healthy daemon is listening,
mailarchiver starts one foreground daemon for this ingest only, reusing its
loaded signatures, and stops it afterward. Each daemon started by mailarchiver
uses a verified private per-run log and no PID file, so stale configured log or
PID paths cannot prevent startup. An advisory lock serializes mailarchiver-owned
daemons that share one configured socket. If a healthy external daemon already
owns that socket, mailarchiver uses it and leaves it running.
`MAILARCHIVER_CLAMD`, `MAILARCHIVER_CLAMDSCAN`,
`MAILARCHIVER_CLAMD_CONFIG`, and `MAILARCHIVER_CLAMD_SOCKET` override the
macOS Homebrew defaults for another local environment, including CI.

This option does **not** enable on-access scanning, a login service, or a
scheduled scan.  It requires a configured ClamAV signature database; scanner
startup or scan errors stop the current ingest rather than silently treating
mail as clean.  A positive detection is retained in `INFECTED1.mbox`.

To create a new archive from a directory and then search it, run from the
repository root after installing the development dependencies and configuring
ClamAV. Replace the paths with your source directory and a new archive location;
`owner-names.txt` lists the owner's names or addresses, one per line.

```sh
export MAIL_ARCHIVE_DIR="/path/to/new-archive"
make run ARGS='ingest --owner-names-file owner-names.txt --clamav "/path/to/email-source-root-directory"'
make search ARGS='subject:invoice after:2024-01-01'
make search ARGS='--limit 0 from:alice@example.com'
```

Ingest creates the archive and reads supported files recursively. Search uses the
same `MAIL_ARCHIVE_DIR`; ordinary words search headers and body text. To resume
content work left by an interrupted or partial import:

```sh
make run ARGS='process --phase content'
```

Use `--no-scan` in place of `--clamav` only when deliberately opting out of
antivirus, such as for purpose-made test fixtures. Build the Rust helper with
`make pst-importer` before importing PST files. PST always uses the Rust
`outlook-pst` adapter. OST uses the separate `libpff` converter process.

For example, with the project's supplied owner-token list and a new archive:

```console
MAIL_ARCHIVE_DIR="$HOME/arch-local/normalized-mail" uv run mailarchiver ingest --owner-names-file owner-names.txt --clamav "$HOME/arch-local/SLG Mail"
```

Framework workers default to the CPU count, capped at eight. Override the limit
for a benchmark or a less capable machine with a positive `--workers N`.
After the ClamAV preflight succeeds, independent source containers are read,
parsed, and scanned concurrently;
canonical MBOX and SQLite publication remains single-writer. Before workers
start, mailarchiver makes a lightweight read-only pass to count recognized
source files and bytes; it does not hash or retain message contents during this
inventory. It spools only typed container metadata to a temporary work
snapshot. Every ordinary file that no parser recognizes is printed once with
its path and reason.

Messages are classified as `Sent` when their parsed `From:` address matches
an owner include rule and no exclude rule. Rules use case-insensitive whole-mailbox
exact/glob matching; archive `config.yaml` supplies the defaults and a legacy
`owner-names.txt` file can supply include rules. They go to the year's
`{YEAR}-Sent1.mbox` series. Other clean messages go to the year's
`{YEAR}-Archive1.mbox` series. `X-Apple-Auto-Saved` messages are logged but not
copied.

Routing dates compare the original `Date:` with the trimmed median of all valid
UTC-normalized `Received:` timestamps. A difference of more than two days uses
the median and records `received-median` in the catalog without changing the
message bytes. The graphical viewer then shows a warning banner and a slight
red tint. Use `--earliest-year YEAR` when an archive has a known start date;
earlier header dates become implausible and normal Received/stream/path
fallbacks apply. Exact empty Eudora MBCP metadata records are logged as
exclusions, and narrow `From XXX` status wrappers are unwrapped to expose their
nested message.

Source and file-format generators have separate immutable, manifest-loaded
registries. Local file/folder traversal and MBOX, Babyl, EMLX, EML, and Maildir
parsing are active. Gmail, IMAP, O365, Microsoft Exchange, and NUL-delimited
standard input are reserved stubs, not supported ingest modes yet. Repeatable
`--plugin-dir DIRECTORY` options load an explicit external plug-in root; Python
code there executes, so only name directories you trust. See
[doc/PLUGINS.md](doc/PLUGINS.md).

Google Takeout MBOX is the supported Gmail acquisition path today; see
[doc/GMAIL.md](doc/GMAIL.md). Microsoft 365 currently has no supported end-user
acquisition path; its Outlook export and future Graph design are documented in
[doc/M365.md](doc/M365.md). Apple Mail cache limitations are documented in
[doc/APPLE_MAIL_CACHE.md](doc/APPLE_MAIL_CACHE.md).
Until provider adapters are implemented, complete Apple Mail `.emlx` records
can serve as a best-effort local bridge for synchronized Gmail, Microsoft 365,
and IMAP accounts. Rerunning ingest adds newly completed messages; byte-identical
cross-source messages remain one canonical record with multiple observations.
Use `make compare-apple-mail` to reconcile the default Apple Mail cache with
`~/mail-archive` by raw and semantic message hashes without changing either.

The Google authorization prototype has been removed. Live Google ingestion will
be reimplemented when needed; no Google authentication or Requests packages are
required by the application.

The archive directory is a native BagIt/Mailbag package containing:

* canonical MBOX payloads under `data/mbox/`;
* `bagit.txt`, `bag-info.txt`, `mailbag.csv`, `manifest-sha256.txt`, and
  `tagmanifest-sha256.txt` interoperability tags;
* `integrity/*.mbox.integrity` tags containing versioned `h1` complete-MBOX,
  `h2` raw-message, and `h3` semantic-message SHA-256 digests as specified in
  [doc/INTEGRITY_CONTROLS.md](doc/INTEGRITY_CONTROLS.md);
* `archive.sqlite3`, the ingest catalog and observation log;
* `search.sqlite3`, a separately rebuildable FTS5 index; and
* `status/ingest-*.json`, one typed operational status/history file per ingest.

Ingest also places `verify_mail_archive.py` in the archive. It is a small,
single-purpose Python program with no third-party dependencies. Run it to
verify BagIt payload/tag fixity, Mailbag structure, and every declared
whole-MBOX and per-message hash without changing the archive:

```console
python3 /path/to/mail-archive/verify_mail_archive.py
```

From this checkout, the equivalent command is:

```console
make verify ARCHIVE=/path/to/mail-archive
```

`bag-info.txt` explicitly records that MBOX framing adds a final LF when a
source message lacks one. The original source-byte SHA-256 disambiguates stored and recovered newline
representations. Narrow double-envelope normalization can add literal `X-From:`;
PST/OST reconstruction adds importer provenance headers. These explicit
transformations are documented in [integrity controls](doc/INTEGRITY_CONTROLS.md).

The default index contains normalized headers and message body text only:
`text/plain` when available, otherwise rendered `text/html`.  It excludes
attachments, MIME structure, and base64 payloads. Use `--index-attachments`
to build a separate text-attachment index. PDF and Office attachment
extraction is not implemented. For a large archive, omit attachment indexing
during initial ingest and build it later with `refresh-index
--index-attachments`; the canonical mail is already safe before that derived
work begins.

One or more source roots belong at the end of `ingest`; rerunning the same
input records new observations but does not rescan or copy an already archived
message with the same normalized `Message-ID` and raw SHA-256.

After a completed or interrupted ingest, mailarchiver prints the archive
report: yearly sent/received/people totals and the 10 most frequent senders
and recipients.

During ingest, a heartbeat is written to standard error immediately, every 250
milliseconds, and when the run finishes. Its highlighted top line shows total
source byte and file completion plus an estimated time remaining. It also shows
elapsed time, processed-message count, average messages per second, earliest
and latest resolved message dates, current message year, that year's count,
and active and peak worker counts. Each worker has a numbered row showing its
current mailfile, byte-completion percentage, and phase. Workers send status
events to the main thread, which alone renders the terminal. Long paths are
fitted to the terminal width so the dashboard does not scroll. Redirected
output stays line-oriented for logs. It also counts archived mail,
previously-seen duplicates, autosave exclusions, infected messages,
unrecognized files, and unchanged containers skipped by source integrity.
The same state is atomically written to a new run-specific file under the
archive's `status/` directory. The final update preserves the run statistics;
later ingests create new files rather than replacing the history.

## Interrupts and disk space

Pressing Control-C performs a controlled shutdown: mailarchiver closes the
on-demand scanner and MBOX files, commits work through the last completed
message, publishes a BagIt/Mailbag checkpoint, prints `interrupted: ...`, and exits with status
130 rather than a traceback.  It then prints the normal `report` output for
the committed partial archive.  If an MBOX append reports `ENOSPC`, mailarchiver
truncates that attempted append back to its prior size where possible, prints
`disk full: ...`, and exits nonzero.  Free space before continuing; do not
assume an integrity file can be refreshed when the filesystem is full.

## Review ingest observations

Every ingest receives a sequential run number and records one observation for
each source record: `archived`, `duplicate`, `autosave-excluded`, or
`source-metadata-excluded`. `review`
is the audit-log viewer; it does not reindex or refresh anything.  Without a
selector it prints all observations.  Use `--run` only to restrict the output
to one numbered ingest run:

```console
uv run mailarchiver review --run 1
```

A **source** is where mail was found. Its source volume and source or forensic
path are retained separately from the **archive mailbox**, the canonical MBOX
where the deduplicated message was saved. The graphical message viewer displays
both locations; source metadata stays in the private catalog and is excluded
from public or redacted derivative packages by default.

## Report archive contents

`report` reads only `archive.sqlite3`.  It lists each year with the number of
sent and received messages and the number of distinct email addresses
appearing as a sender or recipient.  It also lists the top 10 senders and
recipients for the selected scope, excluding the archive owner's addresses.
Addresses remain in the database and in the yearly `people` count.  `--year` accepts one year or an inclusive
range; `--top N` changes the number of names (`--top 0` suppresses them).

```console
uv run mailarchiver report
uv run mailarchiver report --year 2016 --top 20
uv run mailarchiver report --year 2010-2020 --top 50
```

## Rebuild the search index

Build or rebuild the disposable index from canonical mail:

```console
uv run mailarchiver refresh-index
```

Add `--index-attachments` when text attachments should be searchable. The
rebuild keeps body/header text and attachment text in separate FTS tables so a
search client can include attachments explicitly.

## Search mail

`mailsearch` is a read-only search command.  Ordinary words search indexed
headers and body text. `to:` filters recipient addresses, `from:` filters
senders, and `subject:` filters subjects. `date:YYYY-MM-DD` selects the 50-hour worldwide calendar-date window
from midnight in UTC+14 to the next midnight in UTC−12. `before:` ends before
that window starts; `after:` starts when it ends. Adjacent date windows overlap
by 26 hours. See the [date handling guide](https://simsong.github.io/email-collection-toolkit/advanced/#date-handling). Supplied terms are combined with AND. The default is ten results;
`--limit 0` prints every match.  Each result starts with its stable message
number, which can be supplied alone to print the original message. Numbers
align to the widest returned value; interactive terminals render subjects in
bold, while redirected output remains plain text.

```console
uv run mailsearch to:alice@example.com budget
uv run mailsearch subject:invoice after:2024-01-01
uv run mailsearch 42
uv run mailsearch --headers 42
uv run mailsearch --html 42
uv run mailsearch --mime 42
```

Use `uv run mailsearch --help` for the complete syntax. A numbered message
normally displays the main headers and `text/plain` body; `--headers` shows
all headers, `--html` shows decoded HTML, and `--mime` shows its original MIME
source. Printing uses its catalogued MBOX location and verifies its recorded
SHA-256; it does not alter canonical message bytes.

### Graphical search desktop architecture

The current graphical search tool uses pywebview with WKWebView on macOS.
The compiled replacement candidates are Dioxus Desktop and Tauri, retaining Python
archive services and system-webview rendering; see [DIOXUS.md](doc/DIOXUS.md).
The current controller supports multiple archive documents and multiple
independent search windows on one archive. Packaged GUI assets come from an
application-owned, nonce-authenticated server bound to an ephemeral
`127.0.0.1` port; it exposes no HTTP service API, and JavaScript calls Python
through pywebview's native bridge.
It has one search field with the same selectors and quoting rules as
`mailsearch`, sortable results, message and MIME-part viewing, `.eml` export
and drag-out, printing, and attachment viewing. Typing three characters offers
ranked address and subject completions. Selected addresses become removable
filters with **Any**, **From**, **To**, **Cc**, and **Bcc** role menus. The
window title identifies the archive and its deduplicated searchable-message
count. This is an archive interface, not an inbox: every nonempty query searches
the complete collection without treating recent mail as more important. Up to
2,000 matches appear together; larger result sets show the first 2,000 while
the remainder loads automatically in the background. Start it with:

```console
make gui ARGS="--archive /path/to/mail-archive"
```

The bottom status line shows the current ingest, or the latest completed run.
Click it to open the separate ingest-history and worker-detail window. The same
window is available from **Window → Ingests**; choosing it again brings the
existing window to the front.

The About window is hidden at startup and opens from the application menu. It shows the installed version,
free disk space, Internet reachability, startup warnings, and ingest activity.
Use **File → New** to select and initialize a new or empty `.mailarchive`
destination, **File → Import…** to choose local mail sources and edit owner include/exclude
rules, and **Window** to bring any application window forward. Import uses a
cross-process writer lock; its owning search window cannot close until the run
finishes, while other search windows remain usable.

Select **Search attachments** to include the separate text-attachment index in
ordinary full-text searches. Build the attachment index with `uv run
mailarchiver refresh-index --index-attachments` so that table has content.

Select **Show original folder structure** to filter before sorting and paging
by one or more remembered source folders or logical mailboxes. Counts are
deduplicated canonical messages. Directories containing only EML/EMLX messages
and Maildir `cur`/`new` contents collapse into one mailbox. Source volumes merge
by default and can be shown explicitly. Named filter sets are stored atomically
in the operating system's per-user preferences location, outside the archive.
See [`doc/USER_MANUAL.md`](doc/USER_MANUAL.md#filter-by-original-mailbox).

pywebview also supports Windows and Linux, but this application is not yet
portable: attachment opening currently calls the macOS `open` command, Finder
drag-out is macOS-specific, and only the Cocoa/WKWebView bridge has been tested.
The Python writer and scanner also require Windows portability work. Full
Windows support will be validated with the chosen UI framework and packaged
Python backend; existing browser tests do not establish native Windows support.

## Versioning and Updating

The [DevOps guide](doc/DEVOPS.md#github-actions) records the CI, Pages, and
tagged-release GitHub Actions gates.

One canonical PEP 440 version is used everywhere users see a version: package
metadata and the About window currently say `1.0.0a10`. The annotated Git tag
adds only `v`: `v1.0.0a10`. Alpha and beta releases use Sparkle's `preview`
channel; stable releases use the default channel. The release parser accepts
only canonical `MAJOR.MINOR.PATCH`, `MAJOR.MINOR.PATCHaN`, or
`MAJOR.MINOR.PATCHbN` versions and rejects all other forms. Sparkle uses a
separate increasing internal build number solely to order updates.
Historical-feed native trust checks discard inherited `DEVELOPER_DIR`,
`TOOLCHAINS`, `SDKROOT`, and `CODESIGN_ALLOCATE` because those variables can
select Apple toolchain components. Release jobs use their configured toolchain
outside that historical verification boundary.

## Test

The full test architecture and its explicit browser/Cocoa coverage boundary are
documented in [`doc/END_TO_END_TESTING.md`](doc/END_TO_END_TESTING.md).

The ordinary suite uses static MBOX and `.emlx` fixtures. Antivirus tests build
the EICAR signature from fragments only inside a pytest temporary directory,
ingest it with the real on-demand ClamAV daemon, and immediately delete the
generated source; no complete virus-test signature is tracked in Git. The
separate end-to-end suite copies its tracked, virus-free source corpus, ingests
110 discoveries, verifies deduplication, autosave exclusion, quarantine,
newline preservation, attachment indexing, BagIt fixity, and the installed
standalone verifier. Headless Chromium drives the shipped HTML and JavaScript
through the real Python service bridge, including empty-query suppression, complete searches,
sorting, message and MIME views, provenance, remote-content blocking, previews,
exports, printing, drag-out, keyboard navigation, and error display:

```console
make test
make test-e2e
make check
```

Install the pinned headless Chromium once with `make install-test-browser`.
`make check` runs Ruff and Pylint, then ty and Pyright, then Rust and license
checks, then both test suites and website validation without showing a window.
CI runs `make check-static` and `make check-tests` in parallel jobs; local
`make check` retains their order. Use `make lint` and `make types` for the
static checks alone. On macOS,
`make test-quit` checks real immediate/bounded process exit, message-boundary
stopping for local and loopback API sources, and interrupted archive recovery.
`make test-native-quit` uses `MAILARCHIVER_NATIVE_GUI_E2E=1` to exercise Quit
during status polling in a real macOS Cocoa window; it requires a GUI session.
Idle Quit exits after bounded private-export cleanup. Active imports finish their
current message and checkpoint; imports, settings saves, definition updates, and cleanup share one
five-second deadline. Forced exit can leave stale manifests or an
incomplete append; Continue Processing or reimport invokes archive recovery.

`make test-native-gui` additionally exercises the hidden Cocoa/WKWebView bridge.
This native target is an explicit local development check and does not run in
CI/CD, which retains the complete headless Chromium GUI test.
`make test-native-html-find` is a separate visible, opt-in macOS check for
WKWebView HTML finder highlighting and scrolling.
Regenerate the committed safe corpus after an
intentional fixture change with `make fixture-e2e`.

## Other Resources
Interested in email preservation? Check out:

* [Digital Preservation Coalition](https://www.dpconline.org/) established in 2002 as a collaboration between a number of agencies   operating in the UK and Ireland, building a welcoming and inclusive global community, working together to bring about a sustainable future for our digital assets.
* [code{4{lib](https://code4lib.org/) a volunteer-driven collective of hackers, designers, architects, curators, catalogers, artists and instigators from around the world, who largely work for and with libraries, archives and museums on technology “stuff.”
* [DigiPres.org](https://www.digipres.org/) a gateway to all of the wonderful community-owned and community-oriented resources dedicated to digital preservation!
* [The Digital Library Federation](https://www.diglib.org/), a community of practitioners who advance research, learning, social justice, and the public good through the creative design and wise application of digital library technologies.


## Copyright and licenses

Email Collection Toolkit is distributed under the [GNU GPL version 2 only](LICENSE).
Additional licenses are available from the copyright holder.

Original project material is covered by [COPYRIGHT](COPYRIGHT). Vendored and
separately licensed components are identified in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Release builds must run
`make runtime-license-check` on every target platform and include the complete
license bundle produced by `make runtime-license-bundle LICENSE_OUTPUT=PATH`.

Development PST/OST imports require `make pst-importer mcti-scan pff-converter`.
The DMG bundles these executables; the host never loads libpff.
