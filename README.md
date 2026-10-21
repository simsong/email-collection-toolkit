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
dependencies, the ClamAV engine and baseline definitions are bundled; the build
fails if ClamAV is not installed. The target mounts its DMG and
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

The website displays separate **Download Windows installer** and **Download
macOS installer** buttons, with **Show all installers** alongside them. All
buttons work without scripting or browser detection,
and the page states supported architectures. Update stream selection belongs
in the app's Preferences panel: **Release only** or **Alpha / beta / development
and release**. Both Sparkle and WinSparkle use that saved application preference.
Run `make website-download-check` for rendered and headless browser acceptance.

The original `v1.0.0a10` release signed its archive but omitted a complete XML
signature. The audited `Prepare historical signed appcast` workflow produced a
separate signed copy, which was verified and published as the a10 release asset
and Pages feed before the next tag. The failed `v1.0.0a11` run left a draft release;
its tag is retired by explicit request while the draft remains as failure
evidence. Read the current candidate version from `pyproject.toml`; prepare its
matching release from `main` only after branch validation and merge.

Release workflow variables: `GITHUB_REPOSITORY` names the repository used to
retrieve the prior feed; `GH_TOKEN` authorizes release API reads and publication;
`GITHUB_REF_NAME` is the pushed tag validated against `pyproject.toml`.
The Makefile's `RELEASE_TAG` selects the candidate tag and `APPCAST` identifies
the copied feed output. `RUNNER_TEMP` is the hosted runner's temporary area.
No variable should contain secret material except the protected signing and
notarization variables named above.

## Desktop implementation

Python/pywebview is the default desktop on macOS and Windows. `make gui` launches
it; `make dmg` (also `make python-dmg`) builds the macOS Python application.
Windows MSIX builds freeze that same Python entry point with its runtime and
WebView2 integration, for x64 only. The Windows desktop opens, creates and
imports into archives through the shared Python engine; it is an alpha (published
builds so far are test-signed; release workflows now require Microsoft public
signing), and PST/OST converters are not yet packaged for Windows.

The Rust GUI migration is retired. Its source remains in
[`rust/mailsearch-gui`](rust/mailsearch-gui) outside the supported Cargo workspace.
The historical GUI Make targets stop with a retirement message. See the detailed
[migration retrospective](doc/RUST_GUI_EXPERIMENTS.md) for what was tried, measured limitations,
lessons and the last compiling snapshot. Independent Rust importer, verifier,
PST and MIME tools remain supported. `RUSTUP_TOOLCHAIN` can explicitly override
the checkout's compiler; `rust-toolchain.toml` selects Rust 1.99 for these tools.

## Rust importer development

Rust importer development uses `make rust-programs` to build `mdti-validator`
`mcti-generator`, and [`pst-importer`](doc/PST_IMPORTER.md). See [MCT Importer API 1.0](doc/MCT_IMPORTER_API.md) for
the contract, individual targets and validation pipeline. Rust/Cargo are
required to build these tools. The standalone PST helper uses Microsoft's Rust crate;
the CLI archive host runs it through the common processing pipelines. The macOS
DMG bundles `pst-importer`, `mcti-scan` and the `pff-converter`, so packaged
users need no compiler; the Windows MSIX does not yet include them.

PST test inventories and the Rust corpus downloader live in [`pst/`](pst/README.md);
use `make pst-download-plan` to inspect the plan and `make pst-download` to fetch
into ignored `var/pst/`.

For the maintained documentation map, see [doc/README.md](doc/README.md).

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
Eudora, working IMAP cache directories, direct Gmail API/OAuth, redaction, richer
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
The [historical August source-code audit](doc/history/2026-08-25-source-code-audit.md) distinguishes completed
tightening from the remaining architectural gaps.
The [competitive analysis](doc/history/2026-09-09-competitive-analysis.md) explains how this
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

Install [uv](https://docs.astral.sh/uv/) and a ClamAV installation that
provides `libclamav` (for example, Homebrew `clamav` on macOS), then prepare
this project and its local virus definitions:

```console
cd email-collection-toolkit
uv sync
make freshclam
```

`make freshclam` seeds and updates `etc/clamdb` from the installed database.
No ClamAV daemon is configured or started; see
[embedded antivirus](doc/EMBEDDED_CLAMAV.md). Run the project command with `uv run mailarchiver`.

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

## CLI and archive workflows

[CLI_REFERENCE.md](doc/CLI_REFERENCE.md) is the single command reference for
local ingest, IMAP downloads, message listing/search, processing, verification,
reports, diagnostics and packaging. The [user manual](doc/USER_MANUAL.md) explains
the workflows and GUI; [requirements](doc/requirements.md) and
[implementation](doc/implementation.md) define preservation and recovery.

### Search matching

Search ignores case and accents and treats `ß`/`ẞ` as `ss`, without stemming.
Existing indexes need no rebuild. Quoted symbols remain significant: `"CACM-"`
requires the hyphen and `"Ham+Sandwhich"` requires the plus. Word boundaries apply.
See the [search command reference](doc/CLI_REFERENCE.md#search-and-message-display).

### Standalone IMAP downloads

The standalone Python CLI configures an account interactively, synchronizes once
and exits. It defaults to eight workers, excludes spam-named generic mailboxes,
and uses Gmail All Mail plus Gmail IDs/labels. Passwords use the native macOS or
Windows secret store. New downloads normalize CRLF to LF before ingest and retain
original wire hashes/sizes as provenance; existing archives and local imports
keep their line endings. New and duplicate download totals are separate. See the
[IMAP command reference](doc/CLI_REFERENCE.md#imap-downloads). Scheduling, graphs
and desktop account setup remain planned.

### Runtime configuration

| Variable / Make parameter | Purpose |
| --- | --- |
| `MAIL_ARCHIVE_DIR` | Default archive for local ingest/search; explicit `--archive` wins |
| `MAILARCHIVER_CLAMAV_LIBRARY` | Embedded native engine library override |
| `MAILARCHIVER_CLAMAV_DATABASE` | Baseline definition directory |
| `MAILARCHIVER_CLAMAV_UPDATES` | Per-user definition update storage |
| `MAILARCHIVER_FRESHCLAM` | One-shot definition updater executable |
| `MAILARCHIVER_CLAMAV_CERTIFICATES` | Signature verification certificates |
| `FQDN` | IMAP tests set `localhost` for the disposable server greeting, avoiding host reverse DNS; unused by the downloader |
| `SSL_CERT_FILE`, `SSL_CERT_DIR` | Standard TLS trust overrides for private IMAP servers |
| `CARGO`, `RUST_TARGET_DIR` | Cargo executable and Make-selected build output (default `target/`) |
| `ECT_RUST_RECENT_TEST_ROOT`, `ECT_RUST_RECENT_TEST_NAME` | Internal isolated recent-archive test fixtures |
| `ARGS`, `ARCHIVE`, `IMAP_ARGS` | Makefile command parameters; never a password channel |

Legacy `MAILARCHIVER_CLAMD*` variables are unused. Scanning uses embedded
libclamav in a run-owned worker, without a persistent daemon. No password
environment variable is supported.

The shared source API materializes one MIME message per worker; whole mailboxes
are not buffered. Attachment indexing currently handles text, not PDF/Office.
Raw bytes and MIME attachments are retained regardless of extraction limits.
Source idempotence avoids rereading unchanged local containers; exact message
admission uses normalized Message-ID plus raw SHA-256. Preserve raw variants.

Back up the entire archive. MBOX/manifests preserve canonical messages; search
indexes can be rebuilt, but manual identities/tags and operational history
cannot be reconstructed from MBOX alone. An interrupted run retains completed
checkpoints. Investigate integrity failures before further use or copying.

## Versioning and Updating

The [DevOps guide](doc/DEVOPS.md#github-actions) records the CI, Pages, and
tagged-release GitHub Actions gates.

One canonical PEP 440 version, set in `pyproject.toml`, is used everywhere
users see a version: package metadata and the About window show it, and the
annotated Git tag adds only `v` (for example `1.0.0a19` and `v1.0.0a19`).
Alpha and beta releases are published on Sparkle's `preview` channel; stable
releases use the default channel. Installations choose which they receive
under **Release only** or **Alpha / beta / development and release**. The release parser accepts
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
the EICAR signature from fragments only inside a disposable test directory,
ingest it with the real embedded ClamAV engine, and immediately delete the
generated source; no complete virus-test signature is tracked in Git. The
separate end-to-end suite copies its tracked, virus-free source corpus, ingests
210 observations with Rust-owned lifecycle assertions, and verifies deduplication,
autosave exclusion, quarantine,
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

## Windows development

Windows runs the same Python/pywebview application as macOS, using WebView2.
`make gui` runs it from a checkout; `make test-python-reader` and
`make test-python-reader-native` validate synthetic reading/export and the
native bridge. Windows New/Import uses the common Python engine with native
locks and byte-preserving MBOX I/O. See [the Windows Python desktop
notes](doc/PYTHON_WEBVIEW.md) for validation and remaining gaps.

`make prepare-windows-clamav` explicitly downloads the pinned official portable
x64 runtime into `.tmp/clamav-x64`; it installs no service. `make clamav-update`
fetches and validates definitions in application-specific user storage, outside
archives. `make clamav-seed SEED_DIRECTORY=...` copies an existing baseline and
validates it without modifying the source or clearing CDN cooldown state.
`make freshclam` prepares `etc/clamdb` for packaging. Native runtime and baseline
definitions are prerequisites for the MSIX builder, which requires and bundles
the runtime and definitions. Existing `MAILARCHIVER_CLAMAV_LIBRARY`,
`MAILARCHIVER_FRESHCLAM`, `MAILARCHIVER_CLAMAV_DATABASE`,
`MAILARCHIVER_CLAMAV_CERTIFICATES`, and `MAILARCHIVER_CLAMAV_UPDATES` override
the engine, updater, baseline databases, signing-certificate directory, and
per-user definition-update location respectively.

`make prepare-windows-updater` prepares the pinned WinSparkle DLL and licenses;
the MSIX includes them. `make test-windows-scanner` and `make test-windows-updater`
exercise the native runtime and update filtering. Help offers update checks and
settings. Source launches support manual discovery only. The shared feed must
publish a signed Python Windows enclosure with the package identity documented
in the requirements before an installed Python upgrade can be offered.

`make msix-test` builds the Windows payload; `make test-msix MSIX_PACKAGE=...
MSIX_EVIDENCE=...` tests relocation. `make msix-bundle MSIX_PAYLOADS=...` signs the
bundle using `MSIX_TEST_CERT_PFX_BASE64`, the private key matching the existing
public test certificate. Never publish that variable's value. `UV_CACHE_DIR`,
`UV_PYTHON_INSTALL_DIR`, and `UV_PYTHON_BIN_DIR` may place uv's cache, managed
interpreters, and interpreter links inside a development checkout. The packaging
script temporarily sets `UV_PROJECT_ENVIRONMENT` to its isolated runtime venv.
The focused lint target sets `PYTHONPATH` to the checkout solely to resolve
repository-local test helpers; the packaged interpreter remains isolated.

## Windows MSIX packaging

See [Windows MSIX test packaging](doc/WINDOWS_MSIX_TEST.md) for automated build/sign/test commands,
external WebView2 detection and native Windows evidence. Windows packages are
x64 alpha builds, not a fully validated Windows release.

For direct Cargo commands, `CARGO_TARGET_DIR` selects a reusable build cache;
for Make targets, use `RUST_TARGET_DIR` instead.
`ECT_RUST_ENGINE_PYTHON` was the retired Rust GUI's helper-interpreter override;
only historical Rust GUI tests still set it.

Windows test signing uses the GitHub Actions secret/environment variable
`MSIX_TEST_CERT_PFX_BASE64`, containing the base64-encoded persistent test PFX.
Only the signing step receives it. Local signing requires the same variable;
missing or mismatched keys fail rather than creating a new certificate. The
public identity is `scripts/win/test-signing.cer`; private keys never belong in
Git or artifacts. Testers trust it once, until expiration or deliberate rotation.

Release and candidate workflows require Microsoft public signing. See
[Windows public signing](doc/WINDOWS_PUBLIC_SIGNING.md) for provisioning and checks.
Repository variables `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`,
`AZURE_SIGNING_ENDPOINT`, `AZURE_SIGNING_ACCOUNT`, and `AZURE_SIGNING_PROFILE`
select the OIDC identity and signing service. `MSIX_PUBLISHER` is the exact approved
certificate subject, used in the manifest and verification. `PUBLIC_SIGNING` is
the workflow's boolean mode selector. Missing production configuration fails;
release bundles never fall back to the test key. `make test-release-files`
validates staging; `make verify-public-msix MSIX_PUBLISHER=...` verifies Windows
trust, timestamps and publisher on Windows after signing.

## Python desktop delivery

`make dmg` packages the Python GUI, bundled runtime, native dependencies, schemas,
plugins and importer tools. It retains the existing application identity, Sparkle
feed and document ownership. `SIGNING_IDENTITY` selects a local Developer ID
identity. `make test-dmg DMG=...` repeats mounted synthetic/headless validation;
physical interaction is separate acceptance. Building does not install or publish.

Windows MSIX contains private x64 CPython that runs `mailarchiver.desktop_entry`
through `pythonw.exe`; ARM64 is not a package target.
Installed CI exercises production search, autocomplete, exact message retrieval,
native window launch, upgrade and uninstall on the same signed bundle. The alpha
uses the persistent test certificate with explicit trust instructions. Windows
updates use the Python WinSparkle adapter (`winsparkle.py`), sharing update
preferences with macOS Sparkle; an end-to-end signed Windows upgrade has not yet
been validated.

`[release-ci]` opts the current branch into signed/notarized DMG and Windows
installation checks and signatures for both installers and complete XML.
`make test-python-desktop-package` checks the Python entry point and package gates.
`WINDOWS_ARCHIVE` and `WINDOWS_RELEASE_URL` select MSIX signing inputs;
`make release-candidate-base APPCAST=...` authenticates published history and
`make release-candidate-feed APPCAST=...` signs/verifies a candidate. No release
or tag is created by restoring Python defaults.
